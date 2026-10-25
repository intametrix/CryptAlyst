package graph

import "fmt"

// EquationNode is the generic "calc unit": it takes any number of named inputs
// and emits one output computed by an equation over the inputs' values. The
// input port names become the equation's variables, so the node is a pure
// function of its upstream signals.
//
// It is the smallest fully-general transform and the one the equation builder
// in the designer targets: a user writes a formula over the incoming values and
// the node produces a new signal.
type EquationNode struct {
	Equation string     // e.g. "0.6 * a + 0.4 * b"
	OutType  SignalType // type of the emitted signal
}

// ID implements Node.
func (e EquationNode) ID() string { return "calc.equation" }

// Title implements Node.
func (e EquationNode) Title() string { return "Equation" }

// Kind implements Node.
func (e EquationNode) Kind() Kind { return Transform }

// Inputs implements Node. Three conventional inputs keep the palette signature
// stable; unused ports simply receive no value.
func (e EquationNode) Inputs() []Port {
	return []Port{
		{Name: "a", Dir: Input, Type: TypeScore, Desc: "first value (a)"},
		{Name: "b", Dir: Input, Type: TypeScore, Desc: "second value (b)"},
		{Name: "c", Dir: Input, Type: TypeScore, Desc: "third value (c)"},
	}
}

// Outputs implements Node.
func (e EquationNode) Outputs() []Port {
	typ := e.OutType
	if typ == "" {
		typ = TypeScore
	}
	return []Port{{Name: "out", Dir: Output, Type: typ}}
}

// Eval implements Node.
func (e EquationNode) Eval(in map[string]Signal) (map[string]Signal, error) {
	vars := map[string]float64{}
	var sym string
	var ts int64
	for name, s := range in {
		vars[name] = s.Value
		if sym == "" {
			sym = s.Symbol
		}
		ts = s.TSMS
	}
	if e.Equation == "" {
		return nil, fmt.Errorf("calc.equation: no equation")
	}
	v, err := Eval(e.Equation, vars)
	if err != nil {
		return nil, fmt.Errorf("calc.equation: %w", err)
	}
	return map[string]Signal{"out": New(e.OutType, sym, v, ts)}, nil
}
