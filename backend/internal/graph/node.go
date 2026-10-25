package graph

// Kind categorises a node by its role in the graph.
type Kind string

// Node kinds.
const (
	// Source reads the messaging bus; it has no inputs and emits signals.
	Source Kind = "source"
	// Transform takes inputs, computes, and emits new outputs (a "calc unit").
	Transform Kind = "transform"
	// Model is a detector — learned or heuristic — that consumes market
	// features and emits a direction/score/probability. It is a transform
	// whose internals are the model, exposed through the same ports.
	Model Kind = "model"
	// Sink consumes signals and acts (verdict, alert, note); it has no outputs.
	Sink Kind = "sink"
)

// Node is the building block of a strategy graph.
//
// Every node is self-describing: it declares its ports up front, so the graph
// can type-check links, the runtime can resolve a topological order, and the
// UI can render a palette without knowing any particular node. Eval is a
// stateless step for now: it reads the named inputs and returns the named
// outputs; windows and delays arrive later as stateful nodes.
//
// IDs are namespaced and stable (for example "source.flow-imbalance" or
// "calc.threshold") so a saved strategy can be re-instantiated from its ID.
type Node interface {
	ID() string
	Title() string
	Kind() Kind
	Inputs() []Port
	Outputs() []Port
	Eval(in map[string]Signal) (map[string]Signal, error)
}

// InputNames returns the declared input port names in order.
func InputNames(n Node) []string {
	ports := n.Inputs()
	names := make([]string, len(ports))
	for i, p := range ports {
		names[i] = p.Name
	}
	return names
}

// OutputNames returns the declared output port names in order.
func OutputNames(n Node) []string {
	ports := n.Outputs()
	names := make([]string, len(ports))
	for i, p := range ports {
		names[i] = p.Name
	}
	return names
}
