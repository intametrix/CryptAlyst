package graph

import (
	"testing"
)

func TestSignalDirection(t *testing.T) {
	cases := []struct {
		typ  SignalType
		val  float64
		want int
	}{
		{TypeDirection, 1, 1},
		{TypeDirection, -1, -1},
		{TypeDirection, 0, 0},
		{TypeDirection, 0.5, 1}, // any positive value is +1
		{TypeScore, 80, 0},      // non-directional types yield 0
	}
	for _, c := range cases {
		got := New(c.typ, "BTCUSDT", c.val, 0).Direction()
		if got != c.want {
			t.Errorf("Direction(%s, %.1f) = %d, want %d", c.typ, c.val, got, c.want)
		}
	}
}

func TestSignalWithBasisAndMeta(t *testing.T) {
	s := New(TypeScore, "ETHUSDT", 82, 123).
		WithBasis("taker imbalance 0.71").
		WithMeta(map[string]any{"model": "flow-imbalance"})
	if s.Basis != "taker imbalance 0.71" {
		t.Errorf("basis = %q", s.Basis)
	}
	if s.Meta["model"] != "flow-imbalance" {
		t.Errorf("meta[model] = %v", s.Meta["model"])
	}
}

func TestPortCompatible(t *testing.T) {
	out := Port{Name: "score", Dir: Output, Type: TypeScore}
	if !out.Compatible(Port{Name: "in", Dir: Input, Type: TypeScore}) {
		t.Error("score output should feed a score input")
	}
	if out.Compatible(Port{Name: "in", Dir: Input, Type: TypeDirection}) {
		t.Error("score output must not feed a direction input")
	}
	if out.Compatible(Port{Name: "in", Dir: Output, Type: TypeScore}) {
		t.Error("output must not feed an output")
	}
}

func TestRegistry(t *testing.T) {
	r := NewRegistry()
	r.Register(ThresholdNode{})
	r.Register(CombineNode{})

	if _, ok := r.Get("calc.threshold"); !ok {
		t.Error("calc.threshold not registered")
	}
	if len(r.List()) != 2 {
		t.Errorf("List() = %d nodes, want 2", len(r.List()))
	}
	transforms := r.ByKind(Transform)
	if len(transforms) != 2 {
		t.Errorf("ByKind(Transform) = %d, want 2", len(transforms))
	}
}

func TestThresholdNode(t *testing.T) {
	n := ThresholdNode{Threshold: 60}
	in := map[string]Signal{"score": New(TypeScore, "BTCUSDT", 80, 10)}
	out, err := n.Eval(in)
	if err != nil {
		t.Fatalf("Eval: %v", err)
	}
	if d := out["direction"].Direction(); d != 1 {
		t.Errorf("direction = %d, want 1", d)
	}
	if out["direction"].Type != TypeDirection {
		t.Errorf("output type = %s", out["direction"].Type)
	}

	in["score"] = New(TypeScore, "BTCUSDT", -70, 10)
	out, _ = n.Eval(in)
	if d := out["direction"].Direction(); d != -1 {
		t.Errorf("direction = %d, want -1", d)
	}
}

func TestThresholdNodeMissingInput(t *testing.T) {
	if _, err := (ThresholdNode{}).Eval(map[string]Signal{}); err == nil {
		t.Error("expected an error for a missing input")
	}
}

func TestCombineNode(t *testing.T) {
	n := CombineNode{Weights: map[string]float64{"a": 0.6, "b": 0.4}}
	in := map[string]Signal{
		"a": New(TypeDirection, "BTCUSDT", 1, 10),
		"b": New(TypeDirection, "BTCUSDT", -1, 10),
	}
	out, err := n.Eval(in)
	if err != nil {
		t.Fatalf("Eval: %v", err)
	}
	// weighted vote: 0.6*(+1) + 0.4*(-1) = +0.2 -> bull
	if d := out["direction"].Direction(); d != 1 {
		t.Errorf("direction = %d, want 1", d)
	}
	if out["score"].Type != TypeScore {
		t.Errorf("score output type = %s", out["score"].Type)
	}
}
