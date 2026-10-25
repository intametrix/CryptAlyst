package graph

// Direction declares whether a port is an input or an output.
type Direction string

// Port directions.
const (
	Input  Direction = "input"
	Output Direction = "output"
)

// Port is a typed, self-describing connection point on a Node.
//
// A Port is the unit of wiring: a link connects one output port to one input
// port, and the link is only valid when the two ports' SignalType values are
// compatible (equal, or the output is a subtype of the input). Name is unique
// within its direction on a single node; it is the key used in Eval's in/out
// maps.
type Port struct {
	Name string     `json:"name"`
	Dir  Direction  `json:"dir"`
	Type SignalType `json:"type"`
	Desc string     `json:"desc,omitempty"`
}

// Compatible reports whether an output port may feed an input port.
//
// The simple rule for now is type equality; a fuller subtyping lattice (for
// example, every signal may feed a TEXT input via String) can be added without
// changing the Port shape, only this function.
func (p Port) Compatible(in Port) bool {
	if p.Dir != Output || in.Dir != Input {
		return false
	}
	return p.Type == in.Type
}
