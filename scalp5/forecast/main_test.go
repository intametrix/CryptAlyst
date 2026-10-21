package main

import "testing"

// makeBars builds a deterministic up-trending series: enough bars for a
// directional view, and with a size we control for the warm-up guard.
func makeBars(n int) []bar {
	bs := make([]bar, n)
	for i := 0; i < n; i++ {
		bs[i] = bar{
			T: int64(i),
			O: 100 + float64(i),
			H: 101 + float64(i),
			L: 99 + float64(i),
			C: 100.5 + float64(i),
			V: 1000,
		}
	}
	return bs
}

// The panic: cl[n-50:] with n=40 sliced past the front of the series.
// Below MIN_BARS the MA agent must return a neutral view, not panic.
func TestAgentMAWarmupGuard(t *testing.T) {
	v := agentMA(makeBars(40), "buy")
	if v.Prob != 0.5 || v.MovePct != 0 {
		t.Fatalf("40 bars should be neutral, got prob %.3f move %.3f", v.Prob, v.MovePct)
	}
}

// Above MIN_BARS the MA agent produces a directional, non-neutral view.
func TestAgentMADirectional(t *testing.T) {
	v := agentMA(makeBars(51), "buy")
	if v.Prob == 0.5 && v.MovePct == 0 {
		t.Fatalf("51 bars should be directional, got a neutral view")
	}
}

// Every agent shares the warm-up guard: none may panic below MIN_BARS.
func TestAllAgentsSurviveWarmup(t *testing.T) {
	bs := makeBars(40)
	agents := []struct {
		name string
		call func() view
	}{
		{"momentum", func() view { return agentMomentum(bs, "buy") }},
		{"volume", func() view { return agentVolume(bs, "buy") }},
		{"volatility", func() view { return agentVolatility(bs, "buy", 300) }},
		{"reversion", func() view { return agentReversion(bs, "buy") }},
		{"ma", func() view { return agentMA(bs, "buy") }},
		{"breakout", func() view { return agentBreakout(bs, "buy") }},
		{"pressure", func() view { return agentPressure(bs, "buy") }},
	}
	for _, a := range agents {
		func() {
			defer func() {
				if r := recover(); r != nil {
					t.Fatalf("agent %s panicked on 40 bars: %v", a.name, r)
				}
			}()
			_ = a.call()
		}()
	}
}
