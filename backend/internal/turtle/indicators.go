// Package turtle implements the Turtle trading system: Wilder-correct
// indicators, breakout signals, N-based position sizing, and the original
// portfolio risk limits.
//
// Two deliberate departures from the 1980s original are exposed as parameters:
//
//   - an ADX trend filter (the Turtles used none, but a raw breakout system is
//     a whipsaw machine in a range; set ADXMin to 0 for pure original behaviour)
//   - a position notional cap, because N-based sizing on a low-volatility
//     crypto pair can demand more notional than a spot account can fill.
package turtle

import (
	"math"
	"sort"

	"pumpdump/backend/internal/binance"
)

// Params are every number a Turtle trade depends on.
type Params struct {
	EntryPeriod     int     `json:"entry_period"`
	EntryPeriodSlow int     `json:"entry_period_slow"`
	ExitPeriod      int     `json:"exit_period"`
	ExitPeriodSlow  int     `json:"exit_period_slow"`
	ATRPeriod       int     `json:"atr_period"`
	StopATR         float64 `json:"stop_atr_multiple"`
	PyramidStepATR  float64 `json:"pyramid_step_atr"`
	PyramidMaxUnits int     `json:"pyramid_max_units"`

	MaxUnitsPerMarket  int `json:"max_units_per_market"`
	MaxUnitsCorrelated int `json:"max_units_correlated"`
	MaxUnitsDirection  int `json:"max_units_direction"`
	MaxUnitsTotal      int `json:"max_units_total"`

	RiskPerUnit    float64 `json:"risk_per_unit"`
	MaxNotionalPct float64 `json:"max_notional_pct"`

	ADXPeriod int     `json:"adx_period"`
	ADXMin    float64 `json:"adx_min"`

	MaxExtensionATR float64 `json:"max_extension_atr"`

	// Volume confirmation: the highest-value precision lever in the system.
	// Naked Donchian breaks on alts are mostly stop-runs and wick hunts, and
	// requiring the breakout bar to carry real volume removes most of them.
	VolumeConfirmMult  float64 `json:"volume_confirm_mult"`
	VolumeMedianWindow int     `json:"volume_median_window"`
	// Floor applied to the DIVISOR, so a near-zero median cannot make the
	// ratio trivially large.
	VolumeMedianFloorUSD float64 `json:"volume_median_floor_usd"`
	// Absolute money the breakout bar must have traded. Together with the
	// divisor floor this makes the gate fail CLOSED on dust-volume symbols.
	VolumeAbsoluteFloorUSD float64 `json:"volume_absolute_floor_usd"`

	// Volatility band, as ATR% of price.
	MinATRPct float64 `json:"min_atr_pct"`
	MaxATRPct float64 `json:"max_atr_pct"`

	// BTC regime gate: alt trend quality is dominated by BTC beta.
	BTCSymbol       string `json:"btc_symbol"`
	BTCGatePeriod   int    `json:"btc_gate_period"`
	BTCGateInterval string `json:"btc_gate_interval"`
}

// DefaultParams returns the starting values agreed with the design partner.
//
// Three of these differ deliberately from the classic Turtle numbers, and all
// three deviations are load-bearing on crypto:
//
//   - RiskPerUnit is 0.5%, not the original 2%. With a 3% total-open-risk cap
//     and alt correlations of 0.6-0.9, 2% per unit breaches the cap in two
//     trades.
//   - MaxUnitsCorrelated is 2, not 6, and MaxUnitsTotal 6, not 12. Crypto alts
//     are one asset class wearing different tickers; the original per-market
//     class limits do not survive the translation.
//   - ADX is a soft score component, not a veto. ADX is smoothed about three
//     times over, so it lags exactly the fresh breakouts this system wants and
//     reads high during blow-off tops -- the worst possible entries.
func DefaultParams() Params {
	return Params{
		EntryPeriod:            20,
		EntryPeriodSlow:        55,
		ExitPeriod:             10,
		ExitPeriodSlow:         20,
		ATRPeriod:              20,
		StopATR:                2.0,
		PyramidStepATR:         0.5,
		PyramidMaxUnits:        4,
		MaxUnitsPerMarket:      4,
		MaxUnitsCorrelated:     2,
		MaxUnitsDirection:      10,
		MaxUnitsTotal:          6,
		RiskPerUnit:            0.005,
		MaxNotionalPct:         0.25,
		ADXPeriod:              14,
		ADXMin:                 20,
		MaxExtensionATR:        2.0,
		VolumeConfirmMult:      1.5,
		VolumeMedianWindow:     20,
		VolumeMedianFloorUSD:   20_000,
		VolumeAbsoluteFloorUSD: 150_000,
		MinATRPct:              0.015,
		MaxATRPct:              0.25,
		BTCSymbol:              "BTCUSDT",
		BTCGatePeriod:          55,
		BTCGateInterval:        "1d",
	}
}

// MinimumBars is the history needed before the slowest indicator is meaningful.
func (p Params) MinimumBars() int {
	return maxInt(p.EntryPeriodSlow+5, maxInt(p.ATRPeriod+5, p.ADXPeriod*3))
}

// --------------------------------------------------------------------------
// Indicators
// --------------------------------------------------------------------------

// TrueRange returns Wilder's true range for each bar.
func TrueRange(bars []binance.Kline) []float64 {
	out := make([]float64, len(bars))
	for i, b := range bars {
		if i == 0 {
			out[i] = b.High - b.Low
			continue
		}
		prevClose := bars[i-1].Close
		out[i] = math.Max(b.High-b.Low, math.Max(
			math.Abs(b.High-prevClose),
			math.Abs(b.Low-prevClose),
		))
	}
	return out
}

// wilderSmooth applies Wilder's smoothing (the RMA), seeded with the simple
// mean of the first `period` values. This is what makes ATR reproduce
// published values; an EMA would drift over a 20-period average and shift
// every stop distance with it.
func wilderSmooth(values []float64, period int) []float64 {
	out := make([]float64, len(values))
	for i := range out {
		out[i] = math.NaN()
	}
	if period <= 0 || len(values) < period {
		return out
	}
	var seed float64
	for i := 0; i < period; i++ {
		seed += values[i]
	}
	seed /= float64(period)
	out[period-1] = seed
	prev := seed
	for i := period; i < len(values); i++ {
		prev = prev + (values[i]-prev)/float64(period)
		out[i] = prev
	}
	return out
}

// ATR is Wilder-smoothed Average True Range: the Turtles' N.
func ATR(bars []binance.Kline, period int) []float64 {
	return wilderSmooth(TrueRange(bars), period)
}

// Donchian holds the rolling channel plus the level a breakout must clear.
//
// BreakoutUpper/BreakoutLower EXCLUDE the current bar. Comparing a bar's high
// against a channel that includes that same bar is a tautology -- it always
// equals the high -- so the entry level must come from the prior window.
type Donchian struct {
	Upper         []float64
	Lower         []float64
	BreakoutUpper []float64
	BreakoutLower []float64
}

// DonchianChannel computes the channel over `period` bars.
func DonchianChannel(bars []binance.Kline, period int) Donchian {
	n := len(bars)
	out := Donchian{
		Upper:         filled(n),
		Lower:         filled(n),
		BreakoutUpper: filled(n),
		BreakoutLower: filled(n),
	}
	if period <= 0 {
		return out
	}
	for i := 0; i < n; i++ {
		if start := i - period + 1; start >= 0 {
			high, low := bars[start].High, bars[start].Low
			for _, b := range bars[start : i+1] {
				high = math.Max(high, b.High)
				low = math.Min(low, b.Low)
			}
			out.Upper[i], out.Lower[i] = high, low
		}
		if start := i - period; start >= 0 {
			high, low := bars[start].High, bars[start].Low
			for _, b := range bars[start:i] {
				high = math.Max(high, b.High)
				low = math.Min(low, b.Low)
			}
			out.BreakoutUpper[i], out.BreakoutLower[i] = high, low
		}
	}
	return out
}

// RSI computes Wilder's RSI.
func RSI(bars []binance.Kline, period int) []float64 {
	out := filled(len(bars))
	if len(bars) < period+1 {
		return out
	}
	gains := make([]float64, len(bars))
	losses := make([]float64, len(bars))
	for i := 1; i < len(bars); i++ {
		change := bars[i].Close - bars[i-1].Close
		if change > 0 {
			gains[i] = change
		} else {
			losses[i] = -change
		}
	}
	var avgGain, avgLoss float64
	for i := 1; i <= period; i++ {
		avgGain += gains[i]
		avgLoss += losses[i]
	}
	avgGain /= float64(period)
	avgLoss /= float64(period)
	out[period] = rsiValue(avgGain, avgLoss)
	for i := period + 1; i < len(bars); i++ {
		avgGain = (avgGain*float64(period-1) + gains[i]) / float64(period)
		avgLoss = (avgLoss*float64(period-1) + losses[i]) / float64(period)
		out[i] = rsiValue(avgGain, avgLoss)
	}
	return out
}

func rsiValue(avgGain, avgLoss float64) float64 {
	if avgLoss == 0 {
		if avgGain > 0 {
			return 100
		}
		return 50
	}
	rs := avgGain / avgLoss
	return 100 - (100 / (1 + rs))
}

// ADXResult carries Wilder's trend-strength trio.
type ADXResult struct {
	ADX     []float64
	PlusDI  []float64
	MinusDI []float64
}

// ADX computes Wilder's ADX / DMI. The Turtles did not use it, but it is the
// standard way to ask the question their entry filter was really about: is
// this market trending or chopping?
func ADX(bars []binance.Kline, period int) ADXResult {
	n := len(bars)
	out := ADXResult{ADX: filled(n), PlusDI: filled(n), MinusDI: filled(n)}
	if n < period*2 {
		return out
	}
	tr := TrueRange(bars)
	plusDM := make([]float64, n)
	minusDM := make([]float64, n)
	for i := 1; i < n; i++ {
		up := bars[i].High - bars[i-1].High
		down := bars[i-1].Low - bars[i].Low
		if up > down && up > 0 {
			plusDM[i] = up
		}
		if down > up && down > 0 {
			minusDM[i] = down
		}
	}
	sTR := wilderSmooth(tr, period)
	sPlus := wilderSmooth(plusDM, period)
	sMinus := wilderSmooth(minusDM, period)

	dx := filled(n)
	for i := 0; i < n; i++ {
		if math.IsNaN(sTR[i]) || math.IsNaN(sPlus[i]) || math.IsNaN(sMinus[i]) || sTR[i] == 0 {
			continue
		}
		pDI := 100 * sPlus[i] / sTR[i]
		mDI := 100 * sMinus[i] / sTR[i]
		out.PlusDI[i], out.MinusDI[i] = pDI, mDI
		if denom := pDI + mDI; denom > 0 {
			dx[i] = 100 * math.Abs(pDI-mDI) / denom
		}
	}

	first := -1
	for i, v := range dx {
		if !math.IsNaN(v) {
			first = i
			break
		}
	}
	if first < 0 {
		return out
	}
	seedEnd := first + period - 1
	if seedEnd >= n {
		return out
	}
	var sum float64
	var count int
	for _, v := range dx[first : seedEnd+1] {
		if !math.IsNaN(v) {
			sum += v
			count++
		}
	}
	if count < period {
		return out
	}
	prev := sum / float64(count)
	out.ADX[seedEnd] = prev
	for i := seedEnd + 1; i < n; i++ {
		if math.IsNaN(dx[i]) {
			continue
		}
		prev = (prev*float64(period-1) + dx[i]) / float64(period)
		out.ADX[i] = prev
	}
	return out
}

// PercentileRank reports where `value` sits within `values`, as 0-1.
func PercentileRank(values []float64, value float64) float64 {
	clean := make([]float64, 0, len(values))
	for _, v := range values {
		if !math.IsNaN(v) && !math.IsInf(v, 0) {
			clean = append(clean, v)
		}
	}
	if len(clean) == 0 {
		return 0.5
	}
	below := 0
	for _, v := range clean {
		if v <= value {
			below++
		}
	}
	return float64(below) / float64(len(clean))
}

// Correlation computes Pearson correlation over the overlapping tail.
func Correlation(a, b []float64) float64 {
	n := len(a)
	if len(b) < n {
		n = len(b)
	}
	if n < 3 {
		return 0
	}
	a, b = a[len(a)-n:], b[len(b)-n:]
	var sumA, sumB float64
	for i := 0; i < n; i++ {
		sumA += a[i]
		sumB += b[i]
	}
	meanA, meanB := sumA/float64(n), sumB/float64(n)
	var cov, varA, varB float64
	for i := 0; i < n; i++ {
		da, db := a[i]-meanA, b[i]-meanB
		cov += da * db
		varA += da * da
		varB += db * db
	}
	if varA <= 0 || varB <= 0 {
		return 0
	}
	return cov / math.Sqrt(varA*varB)
}

// Returns computes bar-to-bar fractional returns aligned to the later bar.
func Returns(bars []binance.Kline) []float64 {
	out := make([]float64, 0, len(bars))
	for i := 1; i < len(bars); i++ {
		if prev := bars[i-1].Close; prev > 0 {
			out = append(out, (bars[i].Close-prev)/prev)
		}
	}
	return out
}

// ClusterByCorrelation greedily groups symbols whose returns correlate above
// `threshold`.
//
// The Turtles grouped markets by hand (currencies, grains, metals). Crypto
// correlation is too unstable for a static map, so groups come from realised
// returns instead. Symbols are visited in `order` (usually descending
// liquidity) and join the first group whose representative they match.
func ClusterByCorrelation(returns map[string][]float64, order []string, threshold float64) map[string]string {
	groups := make(map[string]string)
	type rep struct {
		name   string
		series []float64
	}
	reps := make([]rep, 0, 16)
	for _, symbol := range order {
		series, ok := returns[symbol]
		if !ok {
			continue
		}
		if len(series) < 5 {
			groups[symbol] = "unclustered"
			continue
		}
		placed := false
		for _, r := range reps {
			if Correlation(series, r.series) >= threshold {
				groups[symbol] = r.name
				placed = true
				break
			}
		}
		if !placed {
			name := "g" + itoa(len(reps)+1)
			reps = append(reps, rep{name: name, series: series})
			groups[symbol] = name
		}
	}
	return groups
}

// --------------------------------------------------------------------------
// Small helpers
// --------------------------------------------------------------------------

func filled(n int) []float64 {
	out := make([]float64, n)
	for i := range out {
		out[i] = math.NaN()
	}
	return out
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var buf [20]byte
	i := len(buf)
	for n > 0 {
		i--
		buf[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		buf[i] = '-'
	}
	return string(buf[i:])
}

func sortedKeys(m map[string][]float64) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
