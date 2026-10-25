package graph

import "fmt"

// ThresholdNode converts a SCORE into a DIRECTION: value >= threshold is +1,
// value <= -threshold is -1, and anything inside the band is 0.
//
// It is the smallest useful transform and exists here to prove the Node
// contract end to end: a typed input, one evaluation step, a typed output.
type ThresholdNode struct {
	Threshold float64
}

// ID implements Node.
func (t ThresholdNode) ID() string { return "calc.threshold" }

// Title implements Node.
func (t ThresholdNode) Title() string { return "Threshold" }

// Kind implements Node.
func (t ThresholdNode) Kind() Kind { return Transform }

// Inputs implements Node.
func (t ThresholdNode) Inputs() []Port {
	return []Port{{Name: "score", Dir: Input, Type: TypeScore, Desc: "0..100 strength signal"}}
}

// Outputs implements Node.
func (t ThresholdNode) Outputs() []Port {
	return []Port{{Name: "direction", Dir: Output, Type: TypeDirection, Desc: "-1 / 0 / +1"}}
}

// Eval implements Node.
func (t ThresholdNode) Eval(in map[string]Signal) (map[string]Signal, error) {
	s, ok := in["score"]
	if !ok {
		return nil, fmt.Errorf("calc.threshold: missing input score")
	}
	thr := t.Threshold
	if thr == 0 {
		thr = 50
	}
	dir := 0
	if s.Value >= thr {
		dir = 1
	} else if s.Value <= -thr {
		dir = -1
	}
	out := New(TypeDirection, s.Symbol, float64(dir), s.TSMS).
		WithBasis(fmt.Sprintf("threshold %.0f: score %.1f -> %d", thr, s.Value, dir))
	return map[string]Signal{"direction": out}, nil
}

// CombineNode merges any number of DIRECTION inputs into one DIRECTION by a
// majority vote and one SCORE by averaging. It is the "chained model" building
// block: upstream models feed it and it emits a single consolidated signal.
type CombineNode struct {
	Weights map[string]float64 // input port name -> weight (default 1.0)
}

// ID implements Node.
func (c CombineNode) ID() string { return "calc.combine" }

// Title implements Node.
func (c CombineNode) Title() string { return "Combine" }

// Kind implements Node.
func (c CombineNode) Kind() Kind { return Transform }

// Inputs implements Node. It declares the three conventional input ports; the
// number is fixed for a stable palette signature, and unused ports simply
// receive no value.
func (c CombineNode) Inputs() []Port {
	return []Port{
		{Name: "a", Dir: Input, Type: TypeDirection, Desc: "first directional input"},
		{Name: "b", Dir: Input, Type: TypeDirection, Desc: "second directional input"},
		{Name: "c", Dir: Input, Type: TypeDirection, Desc: "third directional input"},
	}
}

// Outputs implements Node.
func (c CombineNode) Outputs() []Port {
	return []Port{
		{Name: "direction", Dir: Output, Type: TypeDirection, Desc: "majority vote"},
		{Name: "score", Dir: Output, Type: TypeScore, Desc: "mean strength"},
	}
}

// Eval implements Node.
func (c CombineNode) Eval(in map[string]Signal) (map[string]Signal, error) {
	if len(in) == 0 {
		return nil, fmt.Errorf("calc.combine: no inputs")
	}
	var net float64
	var sum, weight float64
	var sym string
	var ts int64
	var basis string
	for name, s := range in {
		w := c.weight(name)
		net += float64(s.Direction()) * w
		sum += s.Value * w
		weight += w
		if sym == "" {
			sym = s.Symbol
		}
		ts = s.TSMS
		basis += fmt.Sprintf("%s=%s(%d) ", name, s.Symbol, s.Direction())
	}
	dir := 0
	if net > 0 {
		dir = 1
	} else if net < 0 {
		dir = -1
	}
	score := 0.0
	if weight > 0 {
		score = sum / weight
	}
	return map[string]Signal{
		"direction": New(TypeDirection, sym, float64(dir), ts).WithBasis("combine " + basis),
		"score":     New(TypeScore, sym, score, ts).WithBasis("combine " + basis),
	}, nil
}

func (c CombineNode) weight(name string) float64 {
	if c.Weights != nil {
		if w, ok := c.Weights[name]; ok && w > 0 {
			return w
		}
	}
	return 1.0
}
