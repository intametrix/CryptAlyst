package flow

import "testing"

func pf(v float64) *float64 { return &v }

func cand(change float64, sigmaMult, burst, buyRatio, rangePos float64) Candidate {
	return Candidate{
		Symbol: "TESTUSDT", Price: 100,
		SigmaMultiple: sigmaMult, BurstMultiple: burst,
		TakerBuyRatio: buyRatio, RangePosition: rangePos,
		Change10s: pf(change),
	}
}

// THE CENTRAL CLAIM: the same market conditions score a DUMP higher than a PUMP.
// If this fails, the asymmetry is not actually implemented and the detector is
// symmetric with extra comments claiming otherwise.
func TestDumpIsMoreSensitiveThanPump(t *testing.T) {
	cfg := DefaultImpulseConfig()
	// Identical magnitudes, mirrored direction and mirrored range position.
	down := ReadImpulse(cand(-0.40, 2.2, 3.0, 0.34, 0.10), cfg)
	up := ReadImpulse(cand(+0.40, 2.2, 3.0, 0.66, 0.90), cfg)

	if down.DumpScore <= up.PumpScore {
		t.Errorf("dump %.4f must score above the mirrored pump %.4f (fear moves faster than greed)",
			down.DumpScore, up.PumpScore)
	}
	if down.Direction != "DUMP" {
		t.Errorf("direction = %s, want DUMP", down.Direction)
	}
	if up.Direction != "PUMP" {
		t.Errorf("direction = %s, want PUMP", up.Direction)
	}
}

// A moderately violent dump must FIRE where the mirrored pump does not, because
// the dump threshold is deliberately lower. This is the behaviour the human asked
// for: better dump detection, accepting more false positives on that side.
func TestDumpFiresOnEvidenceAPumpWouldNot(t *testing.T) {
	cfg := DefaultImpulseConfig()
	// Sigma 2.2 and burst 2.8 sit between the dump and pump thresholds.
	down := ReadImpulse(cand(-0.35, 2.2, 2.8, 0.36, 0.12), cfg)
	up := ReadImpulse(cand(+0.35, 2.2, 2.8, 0.64, 0.88), cfg)

	if !down.Fired {
		t.Errorf("a moderate dump must FIRE (score %.4f against a %.2f threshold)",
			down.DumpScore, cfg.DumpMinScore)
	}
	if up.Fired {
		t.Errorf("the mirrored pump must NOT fire at the same magnitude (score %.4f against %.2f)",
			up.PumpScore, cfg.PumpMinScore)
	}
}

// All three factors must be reported separately, so a reader can tell a
// three-factor dump from a volume print with nothing behind it.
func TestFactorsAreReportedSeparately(t *testing.T) {
	cfg := DefaultImpulseConfig()
	// Violent range and volume, but price has not travelled and sits mid-range.
	volOnly := ReadImpulse(cand(-0.01, 3.0, 6.0, 0.20, 0.50), cfg)

	if volOnly.Volatility < 0.9 {
		t.Errorf("volatility score %.3f should saturate on sigma 3.0", volOnly.Volatility)
	}
	if volOnly.PriceAction > 0.2 {
		t.Errorf("price action %.3f must stay low when price has not travelled", volOnly.PriceAction)
	}
	// It may still fire on volatility and volume -- but the caller can SEE which.
	found := false
	for _, f := range volOnly.FiredBy {
		if f == "price action" {
			found = true
		}
	}
	if found {
		t.Error("price action must not be listed as a carrier when it did not fire")
	}
}

// A quiet, balanced symbol must not fire in either direction.
func TestQuietSymbolDoesNotFire(t *testing.T) {
	cfg := DefaultImpulseConfig()
	q := ReadImpulse(cand(0.0, 0.8, 1.0, 0.50, 0.50), cfg)
	if q.Fired {
		t.Errorf("a quiet balanced symbol must not fire (dump %.3f pump %.3f)", q.DumpScore, q.PumpScore)
	}
	if q.Direction != "PUMP" && q.Direction != "DUMP" {
		t.Errorf("direction = %q, want one of PUMP/DUMP even when not firing", q.Direction)
	}
}

// Saturability: the thresholds must be reachable and must not alias, or the
// asymmetry is cosmetic.
func TestSaturate(t *testing.T) {
	if got := saturate(0.5, 1.0, 2.0); got != 0 {
		t.Errorf("below lo must be 0, got %f", got)
	}
	if got := saturate(3.0, 1.0, 2.0); got != 1 {
		t.Errorf("above hi must be 1, got %f", got)
	}
	if got := saturate(1.5, 1.0, 2.0); got != 0.5 {
		t.Errorf("midpoint must be 0.5, got %f", got)
	}
	if got := saturate(5, 2, 2); got != 0 {
		t.Errorf("degenerate range must not divide by zero, got %f", got)
	}
}
