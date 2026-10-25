package microburst

// Offline feature/label builder for the one-minute detector. It turns a
// one-second kline series (which already carries taker buy/sell via the
// Binance aggTrade-aggregated fields) into the exact per-second feature map the
// onset and continuation models train on, plus the two outcome labels.
//
// Everything here is computed from bars up to and including the current second
// only — the labels are the only thing that look forward, and they do so from a
// copy of the future bars, so a feature can never use information from after
// its timestamp.

import (
	"math"
	"sort"

	"pumpdump/backend/internal/binance"
)

// LabelConfig scales the barriers to recent volatility and a trading-cost
// floor, so ordinary noise is not scored as a "run" and a move that cannot
// survive fees+spread+slippage is not called a win.
type LabelConfig struct {
	UpMult        float64 // upside barrier = upMult * vol30
	DownMult      float64 // downside barrier = downMult * vol30
	MinBarrierPct float64 // trading-cost floor, in percent
	VolWindow     int     // rolling seconds for the volatility estimate
}

// DefaultLabelConfig returns the starting values: 2x 30s volatility, floored at
// 0.10% to cover a round-trip taker fee plus typical spread and slippage.
func DefaultLabelConfig() LabelConfig {
	return LabelConfig{UpMult: 2.0, DownMult: 2.0, MinBarrierPct: 0.10, VolWindow: 30}
}

// Onset labels.
const (
	OnsetUp    = "UP_BURST"
	OnsetDown  = "DOWN_MOVE"
	OnsetNone  = "NO_EVENT"
	ContUp     = "UP_FIRST"
	ContRevers = "REVERSAL_FIRST"
	ContNone   = "NEITHER"
)

// FeatureBuckets computes the per-second feature map for every bar index,
// using only data up to that index. Order-book spread/depth are absent from
// klines and are therefore simply not present (unsupported), so the trainer
// imputes them at the train-set mean exactly as the runtime scorer does.
func FeatureBuckets(bars []binance.Kline, btc []binance.Kline, volWindow int) []map[string]float64 {
	if volWindow <= 0 {
		volWindow = 30
	}
	out := make([]map[string]float64, len(bars))
	for i := range bars {
		out[i] = featuresAt(bars, btc, i, volWindow)
	}
	return out
}

func featuresAt(bars, btc []binance.Kline, i, volWindow int) map[string]float64 {
	f := map[string]float64{}
	closeP := bars[i].Close
	if closeP <= 0 {
		return f
	}

	f["return_1s"] = retAt(bars, i, 1)
	f["return_3s"] = retAt(bars, i, 3)
	f["return_5s"] = retAt(bars, i, 5)
	f["return_10s"] = retAt(bars, i, 10)
	f["return_acceleration"] = retAt(bars, i, 1) - retAt(bars, i-1, 1)
	f["trade_count_1s"] = float64(bars[i].Trades)
	f["trade_count_5s"] = sumTrades(bars, i, 5)
	f["trade_count_30s"] = sumTrades(bars, i, 30)
	f["quote_volume_1s"] = bars[i].QuoteVolume
	f["quote_volume_5s"] = sumQuote(bars, i, 5)
	f["quote_volume_30s"] = sumQuote(bars, i, 30)

	// Aggressive buy minus aggressive sell over 5s: TakerBuyBase is the buyer-
	// aggressor base volume, so sell volume = Volume - TakerBuyBase.
	var buy, vol float64
	for j := i - 4; j <= i; j++ {
		if j < 0 {
			continue
		}
		buy += bars[j].TakerBuyBase
		vol += bars[j].Volume
	}
	f["buy_minus_sell_5s"] = 2*buy - vol
	if vol > 0 {
		f["buy_sell_imbalance_5s"] = 2*buy/vol - 1 // -1..1
	}

	f["volatility_30s"] = retVol(bars, i, volWindow)

	if len(btc) > 0 {
		bi := len(btc) - 1
		f["btc_return_10s"] = retAt(btc, bi, 10)
		f["btc_return_60s"] = retAt(btc, bi, 60)
	}
	return f
}

// OnsetLabel answers "will an upward burst begin in the next horizon seconds?"
// at bar i: UP_BURST if the upper barrier is hit first, DOWN_MOVE if the lower
// barrier is hit first, else NO_EVENT.
func OnsetLabel(bars []binance.Kline, i, horizon int, cfg LabelConfig) string {
	up, down := barriersAt(bars, i, horizon, cfg)
	for j := i + 1; j < len(bars) && j <= i+horizon; j++ {
		if bars[j].High >= up {
			return OnsetUp
		}
		if bars[j].Low <= down {
			return OnsetDown
		}
	}
	return OnsetNone
}

// ContinuationLabel answers the same question for an ALREADY-underway burst
// over a chosen horizon: does it reach the upside first, reverse first, or
// neither.
func ContinuationLabel(bars []binance.Kline, i, horizon int, cfg LabelConfig) string {
	up, down := barriersAt(bars, i, horizon, cfg)
	entry := bars[i].Close
	for j := i + 1; j < len(bars) && j <= i+horizon; j++ {
		hitUp := bars[j].High >= up
		hitDown := bars[j].Low <= down
		if hitUp && hitDown {
			if bars[j].Close >= entry {
				return ContUp
			}
			return ContRevers
		}
		if hitUp {
			return ContUp
		}
		if hitDown {
			return ContRevers
		}
	}
	return ContNone
}

// barriersAt returns the volatility-scaled barriers at bar i, floored at the
// trading-cost minimum.
// barriersAt returns the volatility-scaled barriers at bar i, floored at the
// trading-cost minimum. The per-second volatility is scaled by sqrt(horizon)
// so a 60s barrier is wider than a 10s one, exactly as uncertainty grows with
// the square root of time.
func barriersAt(bars []binance.Kline, i, horizon int, cfg LabelConfig) (up, down float64) {
	closeP := bars[i].Close
	volPerSec := retVol(bars, i, cfg.VolWindow) * 100 // percent per second
	vol := volPerSec * math.Sqrt(float64(horizon))    // percent over the horizon
	band := math.Max(cfg.MinBarrierPct, math.Max(cfg.UpMult, cfg.DownMult)*vol)
	up = closeP * (1 + band/100)
	down = closeP * (1 - band/100)
	return up, down
}

func retAt(bars []binance.Kline, i, n int) float64 {
	if i-n < 0 {
		return 0
	}
	base := bars[i-n].Close
	if base <= 0 {
		return 0
	}
	return (bars[i].Close - base) / base
}

func sumTrades(bars []binance.Kline, i, n int) float64 {
	var s int64
	for j := i - n + 1; j <= i; j++ {
		if j < 0 {
			continue
		}
		s += bars[j].Trades
	}
	return float64(s)
}

func sumQuote(bars []binance.Kline, i, n int) float64 {
	var s float64
	for j := i - n + 1; j <= i; j++ {
		if j < 0 {
			continue
		}
		s += bars[j].QuoteVolume
	}
	return s
}

// retVol is the standard deviation of 1s returns over the trailing window,
// as a fraction (not percent).
func retVol(bars []binance.Kline, i, window int) float64 {
	if i < 2 {
		return 0
	}
	n := window
	if i+1 < n {
		n = i + 1
	}
	rets := make([]float64, 0, n)
	for j := i - n + 1; j <= i; j++ {
		if j < 1 {
			continue
		}
		if bars[j-1].Close > 0 {
			rets = append(rets, (bars[j].Close-bars[j-1].Close)/bars[j-1].Close)
		}
	}
	if len(rets) < 2 {
		return 0
	}
	mean := 0.0
	for _, r := range rets {
		mean += r
	}
	mean /= float64(len(rets))
	var ss float64
	for _, r := range rets {
		d := r - mean
		ss += d * d
	}
	return math.Sqrt(ss / float64(len(rets)))
}

// MergeBursts collapses a run of consecutive positive onset labels into a
// single burst id so one multi-second surge is not counted as dozens of
// separate alerts. It returns a burst id per bar (0 = no burst).
func MergeBursts(labels []string) []int {
	ids := make([]int, len(labels))
	id := 0
	for i, l := range labels {
		if l == OnsetUp {
			if id == 0 || labels[i-1] != OnsetUp {
				id++
			}
			ids[i] = id
		}
	}
	return ids
}

// medianMAD returns the median and median absolute deviation of a slice, used
// for the per-coin baseline normalisation.
func medianMAD(vals []float64) (med, mad float64) {
	if len(vals) == 0 {
		return 0, 0
	}
	s := append([]float64(nil), vals...)
	sort.Float64s(s)
	med = s[len(s)/2]
	devs := make([]float64, len(s))
	for i, v := range s {
		devs[i] = math.Abs(v - med)
	}
	sort.Float64s(devs)
	mad = devs[len(devs)/2]
	return med, mad
}
