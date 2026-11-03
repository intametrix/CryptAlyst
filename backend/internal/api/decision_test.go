package api

import "testing"

// The rule is deterministic, so it is tested with generated values and no clock.
func TestDecideThresholdAndFreshness(t *testing.T) {
	cases := []struct {
		name  string
		edge  float64
		fresh bool
		want  string
	}{
		{"stale beats a profitable edge", 5.0, false, "refused"},
		{"edge above the floor is a buy", decisionThresholdPct + 0.01, true, "buy"},
		{"edge below the floor is a sell", -decisionThresholdPct - 0.01, true, "sell"},
		{"edge at zero is no trade", 0, true, "none"},
		{"edge just inside the floor is no trade", decisionThresholdPct * 0.5, true, "none"},
		{"negative edge inside the floor is no trade", -decisionThresholdPct * 0.5, true, "none"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := decide(decisionVerdict{EdgePct: c.edge}, c.fresh)
			if got.Verdict != c.want {
				t.Fatalf("verdict = %q, want %q (edge %.4f, fresh %v)", got.Verdict, c.want, c.edge, c.fresh)
			}
			if got.Reason == "" {
				t.Fatal("every verdict must carry a reason")
			}
		})
	}
}
