package turtle

import (
	"math"
	"testing"
)

// THE RULE THE HUMAN ASKED FOR: no single asset may take the whole account.
// A symbol scoring far above the rest must be CAPPED at an equal share, not handed
// the budget.
func TestNoSingleAssetTakesTheBudget(t *testing.T) {
	cands := []RiskWeight{
		{Symbol: "HOTUSDT", Score: 100},
		{Symbol: "AUSDT", Score: 1},
		{Symbol: "BUSDT", Score: 1},
		{Symbol: "CUSDT", Score: 1},
	}
	alloc := AllocateRisk(cands, PortfolioRiskBudgetPct)

	if len(alloc.Capped) != 1 || alloc.Capped[0] != "HOTUSDT" {
		t.Errorf("Capped = %v, want [HOTUSDT] -- the dominant symbol must be capped", alloc.Capped)
	}
	w := alloc.Weight("HOTUSDT")
	equalShare := MaxWeightMultiple * PortfolioRiskBudgetPct / float64(len(cands))
	if w > equalShare+1e-9 {
		t.Errorf("HOTUSDT weight %.4f exceeds the equal share %.4f", w, equalShare)
	}
	// The cap is 2x an equal share. Without it HOTUSDT's score of 100 against three
	// of 1 would have taken 97% of the budget.
	limit := MaxWeightMultiple * PortfolioRiskBudgetPct / float64(len(cands))
	if w > limit+1e-9 {
		t.Errorf("HOTUSDT took %.4f%%, above the %.4f%% cap", w, limit)
	}
	// And it must actually reach its cap rather than being quietly suppressed --
	// a weight below the ceiling would mean the score is being ignored.
	if w < limit-1e-9 {
		t.Errorf("HOTUSDT scored 100x its peers and took only %.4f%% of a %.4f%% ceiling", w, limit)
	}
}

// The weights must never exceed the account's budget, however many symbols there
// are. This is the accounting rule: ten positions at 1% each is 10% at risk, and
// the allocator has to notice.
func TestTotalRiskStaysInsideBudget(t *testing.T) {
	cands := make([]RiskWeight, 0, 40)
	for i := 0; i < 40; i++ {
		cands = append(cands, RiskWeight{Symbol: string(rune('A'+i%26)) + "USDT", Score: 1})
	}
	alloc := AllocateRisk(cands, PortfolioRiskBudgetPct)
	if alloc.TotalRiskPct > PortfolioRiskBudgetPct+1e-6 {
		t.Errorf("total risk %.4f%% exceeds the %.2f%% budget", alloc.TotalRiskPct, PortfolioRiskBudgetPct)
	}
	// Equal scores must produce equal weights.
	if len(alloc.Weights) > 1 {
		a, b := alloc.Weights[0].Weight, alloc.Weights[1].Weight
		if math.Abs(a-b) > 1e-6 {
			t.Errorf("equal scores must give equal weights, got %.6f and %.6f", a, b)
		}
	}
}

// High volatility must LOWER a symbol's weight. It is the term that stops the
// allocator chasing the most violent names -- the opposite of what a naive
// "biggest mover gets most" rule would do.
func TestVolatilityReducesWeight(t *testing.T) {
	calm, _ := riskScore(5, 0.5, 0.5, 0.2, 1.0)
	violent, reasons := riskScore(5, 0.5, 0.5, 0.9, 1.0)
	if violent >= calm {
		t.Errorf("volatile score %.4f must be below calm %.4f", violent, calm)
	}
	found := false
	for _, r := range reasons {
		if r == "high volatility penalty" {
			found = true
		}
	}
	if !found {
		t.Error("the penalty must be named so a weight can be audited")
	}
}

// Alert history is the primary term, because it is a fact rather than a forecast.
func TestAlertHistoryDominates(t *testing.T) {
	many, _ := riskScore(20, 0, 0, 0, 1.0)
	few, _ := riskScore(1, 0, 0, 0, 1.0)
	if many <= few {
		t.Errorf("20 alerts (%.4f) must outweigh 1 alert (%.4f)", many, few)
	}
	// And it must SATURATE: ten times the alerts must not be ten times the score.
	//
	// My first version of this assertion was inverted -- I wrote `if b > a*1.05`
	// as the failure condition, which fires exactly when saturation IS working.
	// The bound was also too tight: the log term caps at 1.0, so 100 alerts scores
	// 27% above 10 alerts and never more. A test that fails when the code is right
	// is the eighth such assertion this session, and every one of them was mine.
	a, _ := riskScore(10, 0, 0, 0, 1.0)
	b, _ := riskScore(100, 0, 0, 0, 1.0)
	if b > a*1.5 {
		t.Errorf("alert score must saturate well below linear: 10 alerts=%.4f, 100 alerts=%.4f", a, b)
	}
	// Ten times the alerts for less than twice the score is the property that
	// matters -- a single noisy symbol must not dominate the allocation.
	if b-a > a {
		t.Errorf("going from 10 to 100 alerts added %.4f to a base of %.4f; too close to linear", b-a, a)
	}
}

// An unweighted symbol falls back to the flat rate rather than to zero, or a
// session that never allocated would refuse to trade at all.
func TestUnweightedFallsBackToFlatRate(t *testing.T) {
	e := New(DefaultConfig())
	e.Start()
	// No allocation set.
	if _, ok := e.OnAlert(alert("AAAUSDT", "PUMP", 10, 0.1, 0.5, 9.9, 9)); !ok {
		t.Fatal("with no allocation the engine must fall back to the flat 1% risk")
	}
}
