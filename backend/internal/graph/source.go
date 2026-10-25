package graph

// ConstantNode emits a fixed signal — useful as a designer seed value and for
// deterministic tests. It is the simplest bus source: no subscription, just a
// value.
type ConstantNode struct {
	Value float64
	Type  SignalType
}

// ID implements Node.
func (c ConstantNode) ID() string { return "source.constant" }

// Title implements Node.
func (c ConstantNode) Title() string { return "Constant" }

// Kind implements Node.
func (c ConstantNode) Kind() Kind { return Source }

// Inputs implements Node.
func (c ConstantNode) Inputs() []Port { return nil }

// Outputs implements Node.
func (c ConstantNode) Outputs() []Port {
	typ := c.Type
	if typ == "" {
		typ = TypeScore
	}
	return []Port{{Name: "out", Dir: Output, Type: typ, Desc: "the fixed value"}}
}

// Eval implements Node.
func (c ConstantNode) Eval(map[string]Signal) (map[string]Signal, error) {
	typ := c.Type
	if typ == "" {
		typ = TypeScore
	}
	return map[string]Signal{"out": New(typ, "", c.Value, 0)}, nil
}
