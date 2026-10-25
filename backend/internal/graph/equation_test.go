package graph

import (
	"math"
	"testing"
)

func TestEquationNode(t *testing.T) {
	n := EquationNode{Equation: "0.6 * a + 0.4 * b", OutType: TypeScore}
	in := map[string]Signal{
		"a": New(TypeScore, "BTCUSDT", 80, 10),
		"b": New(TypeScore, "BTCUSDT", 50, 10),
	}
	out, err := n.Eval(in)
	if err != nil {
		t.Fatalf("Eval: %v", err)
	}
	got := out["out"].Value
	want := 0.6*80 + 0.4*50 // 68
	if math.Abs(got-want) > 1e-9 {
		t.Errorf("equation = %v, want %v", got, want)
	}
	if out["out"].Symbol != "BTCUSDT" {
		t.Errorf("symbol = %q", out["out"].Symbol)
	}
}

func TestEquationNodeMissingEquation(t *testing.T) {
	n := EquationNode{}
	if _, err := n.Eval(map[string]Signal{"a": New(TypeScore, "X", 1, 0)}); err == nil {
		t.Error("expected an error for a missing equation")
	}
}

func TestEquationNodeInGraph(t *testing.T) {
	g := NewGraph()
	g.Add(sourceNode{id: "s1", typ: TypeScore, sig: New(TypeScore, "BTCUSDT", 80, 10)})
	g.Add(sourceNode{id: "s2", typ: TypeScore, sig: New(TypeScore, "BTCUSDT", 50, 10)})
	g.Add(EquationNode{Equation: "0.6 * a + 0.4 * b", OutType: TypeScore})
	g.Add(&sinkNode{id: "k", typ: TypeScore})
	g.Link(Link{From: "s1", FromPort: "out", To: "calc.equation", ToPort: "a"})
	g.Link(Link{From: "s2", FromPort: "out", To: "calc.equation", ToPort: "b"})
	g.Link(Link{From: "calc.equation", FromPort: "out", To: "k", ToPort: "in"})

	if _, err := g.Evaluate(); err != nil {
		t.Fatalf("Evaluate: %v", err)
	}
	if want := 68.0; math.Abs(g.nodes["k"].(*sinkNode).got.Value-want) > 1e-9 {
		t.Errorf("sink value = %v, want %v", g.nodes["k"].(*sinkNode).got.Value, want)
	}
}
