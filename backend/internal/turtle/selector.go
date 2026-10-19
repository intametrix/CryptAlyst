package turtle

import (
	"math"
	"sort"
)

// Universe selection: deciding WHAT to trade before deciding WHEN.
//
// The human's framing, and it is the right one: the platform must first decide
// what to invest in from its prediction engine, and only then follow the alerts
// and the Turtle rules. Choosing the instrument and choosing the moment are
// separate problems and conflating them is how a system ends up taking every
// alert in a 400-symbol universe and paying fees for the privilege.
//
// The score is deliberately built from things the platform already measures, so
// it is an OPINION ABOUT EDGE and not a new prediction invented for this file:
//
//	precursor   quiet-then-buy-surge. The best available precursor to a move.
//	trend       multi-horizon agreement. A move already confirmed on 3/5/10/15s.
//	flow        burst multiple. Is anything happening at all.
//	vol penalty volatility is SUBTRACTED, because high realised vol is the
//	            condition under which alerts mean-revert rather than continue.
//
// Volatility is the only term that can make a candidate look worse than dead
// weight, and that is intentional: it is the one input that changes the SIGN of
// the expectation rather than its size.
type Candidate struct {
	Symbol string  `json:"symbol"`
	Price  float64 `json:"price"`
	ATR    float64 `json:"atr"`
	VolPct float64 `json:"vol_pct"`

	PrecursorScore float64 `json:"precursor_score"`
	TrendAgreement float64 `json:"trend_agreement"`
	TrendSign      int     `json:"trend_sign"`
	BurstMultiple  float64 `json:"burst_multiple"`
	QuoteVolume24h float64 `json:"quote_volume_24h"`

	// Donchian channel bounds, used for the entry confirm once an alert fires.
	DonchianHigh float64 `json:"donchian_high"`
	DonchianLow  float64 `json:"donchian_low"`
	// ADX and ATRPct feed the trend-quality term.
	ADX    float64 `json:"adx"`
	ATRPct float64 `json:"atr_pct"`
}

// Scored is a candidate with its selection score and the reasons for it, so the
// dashboard can show WHY something was chosen rather than a bare number.
type Scored struct {
	Candidate
	Score   float64  `json:"score"`
	Reasons []string `json:"reasons"`
}

// SelectUniverse ranks candidates and returns the best n that clear the floor.
//
// Returning fewer than n is correct behaviour, not a failure: if only two symbols
// have an edge, watching eight means six are noise. A system that always fills
// its quota is a system that always finds something to trade.
func SelectUniverse(cands []Candidate, n int, volVetoPct float64) []Scored {
	out := make([]Scored, 0, len(cands))
	for _, c := range cands {
		s, reasons := scoreCandidate(c, volVetoPct)
		if s <= 0 {
			continue
		}
		out = append(out, Scored{Candidate: c, Score: s, Reasons: reasons})
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].Score > out[j].Score })

	// FALLBACK: a session with an empty watchlist can never trade, however good
	// its rules are. In a genuinely quiet market nothing clears the floor -- no
	// precursor, no trend, no flow -- and the first version returned zero symbols
	// and then sat idle through every alert, which is a silent failure that looks
	// like patience.
	//
	// So when fewer than n clear the floor, the remainder is filled with the most
	// LIQUID candidates, scored near zero and labelled as a fallback. They are not
	// expected to have an edge; they are there so that an alert which does fire is
	// evaluated rather than blocked for a symbol nobody was watching. The label
	// matters: these must not be mistaken for selected opportunities.
	if n > 0 && len(out) < n {
		chosen := make(map[string]bool, len(out))
		for _, s := range out {
			chosen[s.Symbol] = true
		}
		rest := make([]Candidate, 0, len(cands))
		for _, c := range cands {
			if !chosen[c.Symbol] && c.Price > 0 && c.QuoteVolume24h > 0 {
				rest = append(rest, c)
			}
		}
		sort.SliceStable(rest, func(i, j int) bool {
			return rest[i].QuoteVolume24h > rest[j].QuoteVolume24h
		})
		for _, c := range rest {
			if len(out) >= n {
				break
			}
			out = append(out, Scored{
				Candidate: c, Score: 0,
				Reasons: []string{"liquidity fallback: no edge measured, watched so alerts are not blocked"},
			})
		}
	}
	if n > 0 && len(out) > n {
		out = out[:n]
	}
	return out
}

func scoreCandidate(c Candidate, volVetoPct float64) (float64, []string) {
	var reasons []string
	if c.Price <= 0 {
		return 0, nil
	}

	score := 0.0

	// Precursor carries the most weight: it is the only term that fires BEFORE a
	// move rather than during one.
	if c.PrecursorScore > 0 {
		score += 0.45 * c.PrecursorScore
		reasons = append(reasons, "precursor")
	}

	// Trend agreement, signed. A confirmed direction is worth more than an
	// unconfirmed one, which is what the agreement fraction encodes.
	if c.TrendAgreement > 0 && c.TrendSign != 0 {
		score += 0.25 * c.TrendAgreement
		reasons = append(reasons, "trend agreement")
	}

	// Flow: something must actually be happening. Log-scaled because a 50x burst
	// is not fifty times as tradeable as a 1x one.
	if c.BurstMultiple > 1 {
		score += 0.15 * math.Min(1, math.Log(c.BurstMultiple)/math.Log(10))
		reasons = append(reasons, "flow burst")
	}

	// Trend quality: a directional move with real ADX is more likely to continue.
	if c.ADX > 20 {
		score += 0.15 * math.Min(1, (c.ADX-20)/30)
		reasons = append(reasons, "trend quality")
	}

	// THE VOLATILITY PENALTY. Subtracted, and large enough to veto on its own,
	// because this is the condition that flips the expectation rather than
	// merely reducing it.
	if volVetoPct > 0 && c.VolPct > 0 {
		if c.VolPct >= volVetoPct {
			return 0, []string{"volatility veto: alerts at this volatility mean-revert"}
		}
		penalty := 0.30 * (c.VolPct / volVetoPct)
		score -= penalty
		if penalty > 0.1 {
			reasons = append(reasons, "volatility penalty")
		}
	}

	// A tradability floor: a symbol with almost no 24h volume cannot be entered
	// and exited at the signal price even under the perfect-liquidity assumption.
	if c.QuoteVolume24h > 0 && c.QuoteVolume24h < 500000 {
		return 0, []string{"too illiquid to fill at the signal price"}
	}

	if score <= 0 {
		return 0, reasons
	}
	return math.Round(score*10000) / 10000, reasons
}
