package graph

import "pumpdump/backend/internal/detect"

// ModelNode adapts a detect.Detector into a graph Model node. It emits the
// detector's most recent signal as two outputs — direction and score — with
// the detector's human-readable summary carried as the basis, so a strategy
// graph can consume live model output without knowing which model produced it.
//
// A model with no signal yet emits a neutral direction/score rather than
// failing, so a graph evaluates cleanly before the first detection fires.
type ModelNode struct {
	Det detect.Detector
	Now func() int64 // clock; injectable for deterministic tests
}

// ID implements Node.
func (m ModelNode) ID() string { return "model." + m.Det.Name() }

// Title implements Node.
func (m ModelNode) Title() string { return m.Det.Title() }

// Kind implements Node.
func (m ModelNode) Kind() Kind { return Model }

// Inputs implements Node. A model is a source: it reads the bus, not the graph.
func (m ModelNode) Inputs() []Port { return nil }

// Outputs implements Node.
func (m ModelNode) Outputs() []Port {
	return []Port{
		{Name: "direction", Dir: Output, Type: TypeDirection, Desc: "latest signal direction (-1/0/+1)"},
		{Name: "score", Dir: Output, Type: TypeScore, Desc: "latest signal strength (0..100)"},
	}
}

// Eval implements Node.
func (m ModelNode) Eval(map[string]Signal) (map[string]Signal, error) {
	now := int64(0)
	if m.Now != nil {
		now = m.Now()
	}
	sigs := m.Det.Evaluate(now)
	if len(sigs) == 0 {
		return map[string]Signal{
			"direction": New(TypeDirection, "", 0, now).WithBasis(m.Det.Name() + ": no signal yet"),
			"score":     New(TypeScore, "", 0, now).WithBasis(m.Det.Name() + ": no signal yet"),
		}, nil
	}
	s := sigs[len(sigs)-1] // the most recent signal
	return map[string]Signal{
		"direction": New(TypeDirection, s.Symbol, float64(s.Direction), s.TSMS).WithBasis(s.Summary),
		"score":     New(TypeScore, s.Symbol, s.Score, s.TSMS).WithBasis(s.Summary),
	}, nil
}
