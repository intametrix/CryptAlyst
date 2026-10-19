package turtle

import "math"

// Risk-weighted allocation across the whole account.
//
// THE RULE, stated as the human put it: "the equity should not be tied to one
// asset." In the Turtle programme a trader ran ONE account and allocated it across
// many markets. The equity was the account's, every unit was sized from that same
// equity, and the limits were portfolio limits -- four units in any one market, six
// in a correlated group, ten in a direction, twelve in total.
//
// Sizing each position independently from the same equity is correct but not
// sufficient, because it ignores that the positions COMPETE for the same risk
// budget. Two symbols sized at 1% each is 2% of the account at risk, and ten of
// them is 10% -- which is not what "1% risk per unit" is supposed to mean when the
// account is only 300 USDT.
//
// So each symbol is given a WEIGHT: its share of the total risk budget. The weights
// come from the same evidence the rest of the system already produces, and they are
// normalised so the sum of intended risk never exceeds the account's own limit.

// RiskWeight is one symbol's share of the account's risk budget.
type RiskWeight struct {
	Symbol string  `json:"symbol"`
	Weight float64 `json:"weight"`
	// Score is the pre-normalisation risk score, kept so a weight can be audited
	// rather than taken on trust.
	Score   float64  `json:"score"`
	Reasons []string `json:"reasons"`
}

// Allocation is the portfolio's intended risk distribution.
type Allocation struct {
	Weights []RiskWeight `json:"weights"`
	// TotalRiskPct is the fraction of equity the whole book intends to risk if
	// every stop is hit. It is the number that must stay inside the account's
	// limit, and it is reported rather than assumed.
	TotalRiskPct float64 `json:"total_risk_pct"`
	// BudgetPct is the account's allowance.
	BudgetPct float64 `json:"budget_pct"`
	// Capped lists symbols whose weight was reduced by a portfolio limit rather
	// than by their own score.
	Capped []string `json:"capped"`
}

// MaxWeightMultiple bounds any single symbol's weight as a multiple of an equal
// share. 2.0 means the best candidate may hold twice what an average one holds.
const MaxWeightMultiple = 2.0

// PortfolioRiskBudgetPct is how much of the account may be at risk across ALL open
// positions at once.
//
// The book's own arithmetic implies a ceiling of this kind: twelve units at 1% each
// would be 12% of the account, and Turtle traders ran larger accounts precisely
// because a small account cannot carry twelve independent 1% risks without the
// portfolio risk dominating the return. On a 300 USDT account, 6% is the point
// where a bad hour is survivable and a good one is still worth having.
const PortfolioRiskBudgetPct = 6.0

// riskScore turns the evidence for one symbol into a relative risk score.
//
// THE COMPONENTS, and why each is here rather than being a single number:
//
//	alertCount   a symbol that has repeatedly tripped a detector has demonstrated
//	             movement. This is the primary weight because it is a FACT rather
//	             than a forecast.
//	precursor    quiet-then-buy-surge, the one signal that fires BEFORE a move.
//	trend        multi-horizon agreement: a confirmed direction.
//	volatility   a NEGATIVE contribution. High realised volatility is the
//	             condition under which entries mean-revert, and it is also where a
//	             2N stop is most likely to be hit by noise rather than by being
//	             wrong. A symbol that moves violently gets LESS weight, not more.
//
// RiskScoreFor is the exported form, so the API can build an allocation with the
// same arithmetic the tests exercise rather than a second implementation.
func RiskScoreFor(alertCount int, precursor, trendAgreement, volPct, volVetoPct float64) (float64, []string) {
	return riskScore(alertCount, precursor, trendAgreement, volPct, volVetoPct)
}

func riskScore(alertCount int, precursor, trendAgreement float64, volPct, volVetoPct float64) (float64, []string) {
	var reasons []string
	score := 0.0

	if alertCount > 0 {
		// Log-scaled and capped: the fortieth alert on a symbol is not ten times
		// the evidence of the fourth.
		a := math.Log1p(float64(alertCount)) / math.Log1p(20)
		if a > 1 {
			a = 1
		}
		score += 0.40 * a
		reasons = append(reasons, "alert history")
	}
	if precursor > 0 {
		score += 0.30 * precursor
		reasons = append(reasons, "precursor")
	}
	if trendAgreement > 0 {
		score += 0.15 * trendAgreement
		reasons = append(reasons, "trend agreement")
	}

	// The volatility penalty. Subtracted, and large enough to dominate: this is
	// the term that stops the allocator chasing the most violent symbols.
	if volVetoPct > 0 && volPct > 0 {
		penalty := 0.50 * math.Min(1, volPct/volVetoPct)
		score -= penalty
		if penalty > 0.15 {
			reasons = append(reasons, "high volatility penalty")
		}
	}
	if score < 0 {
		score = 0
	}
	return score, reasons
}

// AllocateRisk distributes the account's risk budget across candidates by score.
//
// Returns weights that SUM to at most the budget. If the candidates' scores would
// exceed it, every weight is scaled down proportionally -- scaling rather than
// truncating, because truncating would silently drop the lowest-ranked symbols and
// give the top-ranked one an unintended concentration.
func AllocateRisk(cands []RiskWeight, budgetPct float64) Allocation {
	out := Allocation{BudgetPct: budgetPct}
	if len(cands) == 0 || budgetPct <= 0 {
		return out
	}

	total := 0.0
	for _, c := range cands {
		if c.Score > 0 {
			total += c.Score
		}
	}
	if total <= 0 {
		return out
	}

	// Concentration ceiling: no single symbol may take more than MaxWeightMultiple
	// times an equal share of the budget.
	//
	// The first version capped at EXACTLY an equal share, and a live run showed the
	// flaw: 6 of 12 symbols hit the ceiling, so the ranking the scores had just done
	// was thrown away and the allocation became near-uniform. That is diversified
	// but it is not WEIGHTED, and the human asked for weights.
	//
	// Twice the equal share keeps the anti-concentration property -- a symbol
	// scoring far above the rest still cannot take the account -- while letting a
	// genuinely better candidate hold more than an average one.
	equalShare := MaxWeightMultiple * budgetPct / float64(len(cands))

	for _, c := range cands {
		if c.Score <= 0 {
			continue
		}
		share := c.Score / total * budgetPct
		capped := false
		if share > equalShare {
			share = equalShare
			capped = true
		}
		w := RiskWeight{Symbol: c.Symbol, Weight: round4(share), Score: round4(c.Score), Reasons: c.Reasons}
		if capped {
			w.Reasons = append(w.Reasons, "capped at an equal share of the budget")
			out.Capped = append(out.Capped, c.Symbol)
		}
		out.Weights = append(out.Weights, w)
		out.TotalRiskPct += share
	}
	out.TotalRiskPct = round4(out.TotalRiskPct)
	return out
}

// Weight returns a symbol's share of the risk budget, or 0 if it has none.
func (a Allocation) Weight(symbol string) float64 {
	for _, w := range a.Weights {
		if w.Symbol == symbol {
			return w.Weight
		}
	}
	return 0
}

// SetAllocation installs a portfolio allocation. Sizing for a symbol is then its
// share of the account's risk budget rather than a flat 1% each.
func (e *Engine) SetAllocation(a Allocation) {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.allocation = a
	e.store.Record("allocation", a)
}

// Allocation returns the current distribution.
func (e *Engine) Allocation() Allocation {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.allocation
}
