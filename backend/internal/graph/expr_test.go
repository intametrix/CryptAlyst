package graph

import (
	"math"
	"testing"
)

func TestEvalArithmetic(t *testing.T) {
	cases := []struct {
		expr string
		want float64
	}{
		{"1 + 2", 3},
		{"2 * 3 + 4", 10},
		{"2 * (3 + 4)", 14},
		{"-5 + 10", 5},
		{"10 / 4", 2.5},
		{"1 + 2 * 3 - 4 / 2", 5},
	}
	for _, c := range cases {
		got, err := Eval(c.expr, nil)
		if err != nil {
			t.Fatalf("Eval(%q): %v", c.expr, err)
		}
		if math.Abs(got-c.want) > 1e-9 {
			t.Errorf("Eval(%q) = %v, want %v", c.expr, got, c.want)
		}
	}
}

func TestEvalVariables(t *testing.T) {
	vars := map[string]float64{"score": 80, "direction": 1, "weight": 0.6}
	got, err := Eval("weight * score", vars)
	if err != nil {
		t.Fatalf("Eval: %v", err)
	}
	if math.Abs(got-48) > 1e-9 {
		t.Errorf("weight * score = %v, want 48", got)
	}
}

func TestEvalFunctions(t *testing.T) {
	cases := []struct {
		expr string
		want float64
	}{
		{"min(3, 7)", 3},
		{"max(3, 7)", 7},
		{"abs(-4)", 4},
		{"clamp(120, 0, 100)", 100},
		{"clamp(-5, 0, 100)", 0},
	}
	for _, c := range cases {
		got, err := Eval(c.expr, nil)
		if err != nil {
			t.Fatalf("Eval(%q): %v", c.expr, err)
		}
		if math.Abs(got-c.want) > 1e-9 {
			t.Errorf("Eval(%q) = %v, want %v", c.expr, got, c.want)
		}
	}
}

func TestEvalComparisonAndTernary(t *testing.T) {
	cases := []struct {
		expr string
		want float64
	}{
		{"1 > 2", 0},
		{"2 >= 2", 1},
		{"1 != 1", 0},
		{"score > 50 ? 1 : 0", 1},
		{"score < 50 ? 1 : 0", 0},
	}
	vars := map[string]float64{"score": 80}
	for _, c := range cases {
		got, err := Eval(c.expr, vars)
		if err != nil {
			t.Fatalf("Eval(%q): %v", c.expr, err)
		}
		if math.Abs(got-c.want) > 1e-9 {
			t.Errorf("Eval(%q) = %v, want %v", c.expr, got, c.want)
		}
	}
}

func TestEvalErrors(t *testing.T) {
	for _, expr := range []string{"1 +", "unknownvar + 1", "bogus(1)", "1 / 0", "(1 + 2", "1 ? 2"} {
		if _, err := Eval(expr, nil); err == nil {
			t.Errorf("Eval(%q) should error", expr)
		}
	}
}
