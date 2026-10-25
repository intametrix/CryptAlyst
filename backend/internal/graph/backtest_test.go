package graph

import (
	"math"
	"testing"
)

// buildRegimeGraph returns a graph whose verdict is the regime direction, so
// the backtest can be driven entirely by the regime field.
func buildRegimeGraph() (*Graph, *Context, *VerdictNode) {
	ctx := &Context{}
	g := NewGraph()
	for _, n := range ContextSources(ctx) {
		g.Add(n)
	}
	v := &VerdictNode{}
	g.Add(v)
	g.Link(Link{ID: "l", From: "source.regime", FromPort: "direction", To: "sink.verdict", ToPort: "in"})
	return g, ctx, v
}

func TestBacktestRisingHits(t *testing.T) {
	g, ctx, v := buildRegimeGraph()
	n := 10
	contexts := make([]Context, n)
	closes := make([]float64, n)
	for i := 0; i < n; i++ {
		contexts[i] = Context{TSMS: int64(i), Symbol: "BTCUSDT", Regime: "rising"}
		closes[i] = 100 + float64(i)
	}
	bt := &Backtest{Graph: g, Ctx: ctx, Contexts: contexts, Closes: closes, Verdict: v, Forward: 1}
	res, err := bt.Run()
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if res.Signals != n-1 {
		t.Errorf("Signals = %d, want %d", res.Signals, n-1)
	}
	if res.Right != res.Signals {
		t.Errorf("Right = %d/%d, want all right (rising regime + rising price)", res.Right, res.Signals)
	}
	if math.Abs(res.HitRate-1.0) > 1e-9 {
		t.Errorf("HitRate = %v, want 1.0", res.HitRate)
	}
	if res.Equity <= 1.0 {
		t.Errorf("Equity = %v, want > 1.0", res.Equity)
	}
}

func TestBacktestFallingMisfires(t *testing.T) {
	g, ctx, v := buildRegimeGraph()
	n := 10
	contexts := make([]Context, n)
	closes := make([]float64, n)
	for i := 0; i < n; i++ {
		contexts[i] = Context{TSMS: int64(i), Symbol: "BTCUSDT", Regime: "falling"} // bear call
		closes[i] = 100 + float64(i)                                                // but price rises
	}
	bt := &Backtest{Graph: g, Ctx: ctx, Contexts: contexts, Closes: closes, Verdict: v, Forward: 1}
	res, err := bt.Run()
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if res.Right != 0 {
		t.Errorf("Right = %d, want 0 (bear call against a rising price)", res.Right)
	}
	if math.Abs(res.HitRate) > 1e-9 {
		t.Errorf("HitRate = %v, want 0", res.HitRate)
	}
	if res.Equity >= 1.0 {
		t.Errorf("Equity = %v, want < 1.0", res.Equity)
	}
}
