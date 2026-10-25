package graph

import "fmt"

// Link is one edge in the graph: it maps a source output port to a target
// input port through an optional equation.
//
// The equation is closed over a fixed set of variables derived from the source
// signal — score, direction, value, weight — so a link is a pure transform
// from one signal to another, not an arbitrary program.
type Link struct {
	ID       string  `json:"id"`
	From     string  `json:"from"`
	FromPort string  `json:"from_port"`
	To       string  `json:"to"`
	ToPort   string  `json:"to_port"`
	Weight   float64 `json:"weight"`
	Expr     string  `json:"expr"`
}

// Transform applies the link's equation to a source signal, producing the
// signal to feed into the target input. An empty Expr is the identity
// (pass the value through). The result inherits the source's symbol, ts and
// basis so provenance is preserved.
func (l Link) Transform(src Signal) (Signal, error) {
	expr := l.Expr
	if expr == "" {
		expr = "value"
	}
	vars := map[string]float64{
		"score":     src.ScoreValue(),
		"direction": float64(src.Direction()),
		"value":     src.Value,
		"weight":    l.Weight,
	}
	v, err := Eval(expr, vars)
	if err != nil {
		return Signal{}, fmt.Errorf("link %q: %w", l.ID, err)
	}
	return New(src.Type, src.Symbol, v, src.TSMS).WithBasis(src.Basis), nil
}
