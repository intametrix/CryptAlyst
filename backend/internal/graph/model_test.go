package graph

import (
	"testing"

	"pumpdump/backend/internal/detect"
)

// fakeDetector is a minimal detect.Detector for adapter tests.
type fakeDetector struct {
	name  string
	title string
	sigs  []detect.Signal
}

func (f fakeDetector) Name() string         { return f.name }
func (f fakeDetector) Title() string        { return f.title }
func (f fakeDetector) Description() string  { return "fake" }
func (f fakeDetector) Batch() bool          { return false }
func (f fakeDetector) Observe(detect.Event) {}
func (f fakeDetector) Evaluate(int64) []detect.Signal {
	return f.sigs
}
func (f fakeDetector) Params() map[string]any { return nil }

func TestModelNodeEmitsSignal(t *testing.T) {
	m := ModelNode{
		Det: fakeDetector{
			name:  "flow-imbalance",
			title: "Flow Imbalance",
			sigs:  []detect.Signal{{Symbol: "BTCUSDT", Direction: 1, Score: 82, TSMS: 100, Summary: "taker imbalance 0.71"}},
		},
	}
	if m.ID() != "model.flow-imbalance" {
		t.Errorf("ID = %q", m.ID())
	}
	out, err := m.Eval(nil)
	if err != nil {
		t.Fatalf("Eval: %v", err)
	}
	if d := out["direction"].Direction(); d != 1 {
		t.Errorf("direction = %d, want 1", d)
	}
	if out["score"].Value != 82 {
		t.Errorf("score = %v, want 82", out["score"].Value)
	}
	if out["direction"].Basis != "taker imbalance 0.71" {
		t.Errorf("basis = %q", out["direction"].Basis)
	}
}

func TestModelNodeNoSignal(t *testing.T) {
	m := ModelNode{Det: fakeDetector{name: "flow-imbalance"}}
	out, err := m.Eval(nil)
	if err != nil {
		t.Fatalf("Eval: %v", err)
	}
	if d := out["direction"].Direction(); d != 0 {
		t.Errorf("direction = %d, want 0 (neutral)", d)
	}
}
