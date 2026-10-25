package graph

import (
	"math"
	"testing"
)

func TestLinkIdentity(t *testing.T) {
	l := Link{ID: "l1", From: "a", FromPort: "score", To: "b", ToPort: "score"}
	src := New(TypeScore, "BTCUSDT", 82, 10).WithBasis("taker imbalance")
	out, err := l.Transform(src)
	if err != nil {
		t.Fatalf("Transform: %v", err)
	}
	if out.Value != 82 {
		t.Errorf("identity value = %v, want 82", out.Value)
	}
	if out.Symbol != "BTCUSDT" || out.Basis != "taker imbalance" {
		t.Errorf("identity lost provenance: %+v", out)
	}
}

func TestLinkWeighted(t *testing.T) {
	l := Link{ID: "l1", Weight: 0.6, Expr: "weight * score"}
	src := New(TypeScore, "BTCUSDT", 80, 10)
	out, err := l.Transform(src)
	if err != nil {
		t.Fatalf("Transform: %v", err)
	}
	if math.Abs(out.Value-48) > 1e-9 {
		t.Errorf("weighted value = %v, want 48", out.Value)
	}
}

func TestLinkGate(t *testing.T) {
	l := Link{Expr: "direction > 0 ? score : 0"}
	src := New(TypeDirection, "BTCUSDT", 1, 10)
	out, err := l.Transform(src)
	if err != nil {
		t.Fatalf("Transform: %v", err)
	}
	if out.Value != 0 {
		t.Errorf("gate value = %v, want 0 (direction has no score)", out.Value)
	}
}

func TestLinkError(t *testing.T) {
	l := Link{ID: "l1", Expr: "nope(1)"}
	if _, err := l.Transform(New(TypeScore, "X", 1, 0)); err == nil {
		t.Error("expected an error for a bad equation")
	}
}
