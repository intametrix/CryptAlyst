package flow

import (
	"math"
	"sort"
)

// Movers: the greatest increases and decreases on the exchange.
//
// This is the cheapest useful question to ask Binance -- the 24h ticker already
// carries the change percentage and the quote volume for every symbol, and the
// engine is already subscribed to all of them. Nothing new is fetched; the data
// was arriving and being discarded.
//
// WHY IT MATTERS FOR THIS SYSTEM SPECIFICALLY: the detector is built to find
// ONSET, which means it deliberately looks at symbols that are quiet and then
// suddenly are not. That is the right bias and it has a blind spot -- it has no
// view of where the market as a whole has already gone. A symbol up 40% on the day
// is a very different proposition from one up 2%, both for a Turtle breakout
// (late) and for a mean-reversion alert (ripe), and the flow measurement alone
// cannot tell them apart.

// Mover is one symbol's 24h standing.
type Mover struct {
	Symbol         string  `json:"symbol"`
	Price          float64 `json:"price"`
	ChangePct24h   float64 `json:"change_pct_24h"`
	QuoteVolume24h float64 `json:"quote_volume_24h"`
	High24h        float64 `json:"high_24h"`
	Low24h         float64 `json:"low_24h"`
	// RangePosition is where price sits in the 24h range, 0 at the low and 1 at
	// the high. A big gain that has faded to mid-range is a different signal from
	// one closing at its high, and the change percentage alone cannot separate
	// them.
	RangePosition float64 `json:"range_position"`
	// Tradable says whether the symbol clears the session's own liquidity floor.
	// Reported rather than filtered so a reader can see what was excluded and why.
	Tradable bool `json:"tradable"`
}

// MoversResult is the ranked set.
type MoversResult struct {
	Gainers []Mover `json:"gainers"`
	Losers  []Mover `json:"losers"`
	// Scanned and Tradable make the filter visible: a list of eight gainers drawn
	// from a universe of four hundred is a very different statement from eight
	// drawn from twelve.
	Scanned   int     `json:"scanned"`
	Tradable  int     `json:"tradable"`
	MinVolume float64 `json:"min_quote_volume"`
}

// RankMovers sorts candidates into the greatest increases and decreases.
//
// minQuoteVolume is a hard floor, not a preference: a symbol with 20k of 24h
// turnover can show a 300% gain on a single trade, and putting that at the top of
// a gainers list is the most common way a movers feed misleads its reader. The
// floor is applied BEFORE ranking so the lists are not silently padded with
// untradeable names.
func RankMovers(cands []Candidate, n int, minQuoteVolume float64) MoversResult {
	out := MoversResult{MinVolume: minQuoteVolume}
	all := make([]Mover, 0, len(cands))
	for _, c := range cands {
		if c.Price <= 0 || c.PriceChangePct24h == 0 {
			continue
		}
		out.Scanned++
		m := Mover{
			Symbol: c.Symbol, Price: c.Price,
			ChangePct24h:   roundTo(c.PriceChangePct24h, 4),
			QuoteVolume24h: roundTo(c.QuoteVolume24h, 2),
			High24h:        roundTo(c.High24h, 8),
			Low24h:         roundTo(c.Low24h, 8),
		}
		if c.High24h > c.Low24h {
			m.RangePosition = roundTo((c.Price-c.Low24h)/(c.High24h-c.Low24h), 4)
		}
		m.Tradable = c.QuoteVolume24h >= minQuoteVolume
		if m.Tradable {
			out.Tradable++
		}
		all = append(all, m)
	}

	sort.SliceStable(all, func(i, j int) bool {
		return all[i].ChangePct24h > all[j].ChangePct24h
	})
	for _, m := range all {
		if len(out.Gainers) >= n {
			break
		}
		if m.Tradable {
			out.Gainers = append(out.Gainers, m)
		}
	}
	for i := len(all) - 1; i >= 0; i-- {
		if len(out.Losers) >= n {
			break
		}
		if all[i].Tradable {
			out.Losers = append(out.Losers, all[i])
		}
	}
	return out
}

// ------------------------------------------------------------------ money flow

// MoneyFlow samples order flow at several intervals and turns disagreement
// between them into a conviction read.
//
// The human's framing: sample the money flow at various intervals as another
// indicator to add conviction to a BUY, a SELL or a HOLD. That is the right shape
// for it. One interval tells you what just happened; several tell you whether it
// is still happening, and the second question is the one that decides whether a
// signal is worth acting on.
//
// A note on what this is NOT: it is not order-book depth or the exchange's own
// "money flow" indicator. It is measured from observed TAKER prints, which means
// net aggressive buying minus aggressive selling. Unfilled resting orders are
// invisible to it, and the caveat is recorded on the response rather than left
// for the reader to discover.
type FlowInterval struct {
	Seconds float64 `json:"seconds"`
	Label   string  `json:"label"`
	Buy     float64 `json:"buy_quote"`
	Sell    float64 `json:"sell_quote"`
	Net     float64 `json:"net_quote"`
	// BuyRatio is buy / (buy + sell). 0.5 is balanced.
	BuyRatio float64 `json:"buy_ratio"`
	// Coverage is the fraction of the window actually covered by observed prints,
	// 0..1. A 1h interval on a symbol with ten minutes of prints is mostly
	// unmeasured, and reporting the figure without the coverage would present a
	// ten-minute sample as an hour.
	Coverage float64 `json:"coverage"`
	Measured bool    `json:"measured"`
}

// Conviction is the combined read across intervals.
type Conviction struct {
	Intervals []FlowInterval `json:"intervals"`
	// Action is BUY, SELL or HOLD.
	Action string `json:"action"`
	// Score is 0..1: how strongly the intervals agree on a direction, weighted by
	// how long a window each covers.
	Score float64 `json:"score"`
	// Agreement is the fraction of total weight pointing the dominant way.
	Agreement float64 `json:"agreement"`
	// MeasuredIntervals counts the intervals with enough coverage to count.
	MeasuredIntervals int    `json:"measured_intervals"`
	Reason            string `json:"reason"`
	// Basis states the measurement's own limits, so the number is not read as
	// something it is not.
	Basis string `json:"basis"`
}

// moneyFlowIntervals are the sampling windows, shortest first.
var moneyFlowIntervals = []struct {
	seconds float64
	label   string
}{
	{60, "1m"}, {300, "5m"}, {900, "15m"}, {3600, "1h"},
}

// MinCoverage is the fraction of a window that must be covered by observed
// prints before its flow is treated as measured.
const MinCoverage = 0.5

// SampleMoneyFlow measures flow at each interval and turns the agreement into a
// conviction.
func (s *symbolFlow) SampleMoneyFlow(now int64) Conviction {
	firstTS, lastTS := s.takerSpan()

	out := Conviction{
		MeasuredIntervals: 0,
		Action:            "HOLD",
		Basis:             "measured from observed taker prints (aggressive buys minus sells); resting orders are invisible to it",
	}

	// Weighting rises with the interval: a 1h of net buying is stronger evidence
	// than a 1m, which is the same principle as the multi-horizon trend read.
	weights := []float64{0.15, 0.20, 0.30, 0.35}
	var buyW, sellW float64
	var weightSum float64

	for i, iv := range moneyFlowIntervals {
		buy, sell := s.buyPressure(iv.seconds, now)
		total := buy + sell

		// Coverage: how much of the window the buffer actually spans. Without
		// this a quiet symbol silently reports a 1h figure measured over 3
		// minutes of prints.
		coverage := 0.0
		if firstTS > 0 && lastTS > firstTS {
			span := float64(lastTS-firstTS) / 1000
			coverage = math.Min(1, span/iv.seconds)
		}
		if total <= 0 {
			coverage = 0
		}

		fi := FlowInterval{
			Seconds: iv.seconds, Label: iv.label,
			Buy: roundTo(buy, 2), Sell: roundTo(sell, 2),
			Net:      roundTo(buy-sell, 2),
			Coverage: roundTo(coverage, 3),
			Measured: coverage >= MinCoverage && total > 0,
		}
		if total > 0 {
			fi.BuyRatio = roundTo(buy/total, 4)
		} else {
			fi.BuyRatio = 0.5
		}
		out.Intervals = append(out.Intervals, fi)

		if !fi.Measured {
			continue
		}
		out.MeasuredIntervals++
		w := weights[i]
		weightSum += w
		// A deadband around balance, so noise on a quiet symbol does not register
		// as conviction. 0.55 corresponds to a 10% imbalance.
		switch {
		case fi.BuyRatio > 0.55:
			buyW += w
		case fi.BuyRatio < 0.45:
			sellW += w
		}
	}

	if out.MeasuredIntervals == 0 {
		out.Reason = "not enough observed prints to measure flow at any interval"
		return out
	}

	dominant, sign := buyW, "BUY"
	if sellW > buyW {
		dominant, sign = sellW, "SELL"
	}
	// Agreement is a share of ALL measured weight, so an interval that is
	// balanced counts against conviction rather than being ignored -- the same
	// correction that a test forced on the multi-horizon trend read.
	if weightSum > 0 {
		out.Agreement = roundTo(dominant/weightSum, 4)
	}
	out.Score = out.Agreement

	switch {
	case out.Agreement >= 0.7:
		out.Action = sign
		out.Reason = "flow is directionally consistent across " + itoa(out.MeasuredIntervals) + " measured interval(s)"
	case out.Agreement >= 0.5 && dominant > 0:
		out.Action = sign
		out.Reason = "flow leans " + sign + " but the intervals disagree; conviction is weak"
	default:
		out.Action = "HOLD"
		out.Reason = "flow is balanced or the intervals contradict each other"
	}
	return out
}

// takerSpan reports the oldest and newest timestamps in the taker buffer, which
// is what bounds how much of a long window can actually be measured.
func (s *symbolFlow) takerSpan() (first, last int64) {
	if len(s.taker) == 0 {
		return 0, 0
	}
	return s.taker[0].TS, s.taker[len(s.taker)-1].TS
}

func itoa(v int) string {
	if v == 0 {
		return "0"
	}
	var b [8]byte
	i := len(b)
	for v > 0 {
		i--
		b[i] = byte('0' + v%10)
		v /= 10
	}
	return string(b[i:])
}
