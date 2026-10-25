package envelope

// job.go defines the sweep request: a graph specification, a parameter grid,
// and a reference to the history to replay.

// NodeSpec declares one node by type ID plus its tunable parameters. The agent
// re-instantiates a node from its ID (the same catalog the designer renders)
// and applies Params.
type NodeSpec struct {
	ID     string         `json:"id"`               // e.g. "calc.threshold"
	Params map[string]any `json:"params,omitempty"` // e.g. {"threshold": 50}
}

// LinkSpec declares one edge and its equation mapping.
type LinkSpec struct {
	From     string  `json:"from"`
	FromPort string  `json:"from_port"`
	To       string  `json:"to"`
	ToPort   string  `json:"to_port"`
	Weight   float64 `json:"weight,omitempty"`
	Expr     string  `json:"expr,omitempty"`
}

// GraphSpec is a serialisable strategy: nodes + links. It is declarative — the
// agent rebuilds a runnable graph from it, and the backend validates it the
// same way it validates any graph.
type GraphSpec struct {
	Nodes []NodeSpec `json:"nodes"`
	Links []LinkSpec `json:"links"`
}

// HistoryRef points at the data to replay. It is a reference, not the data:
// the agent resolves it (bundled bytes, a file, or a store) so a job packet
// stays small even when the history is large.
type HistoryRef struct {
	Source   string `json:"source"` // "klines" | "file" | "store"
	Symbol   string `json:"symbol"`
	Interval string `json:"interval"` // e.g. "1s", "5m"
	StartMS  int64  `json:"start_ms"`
	EndMS    int64  `json:"end_ms"`
}

// SweepJob is the payload of a KindJob envelope: a graph, the grid to sweep,
// and the history to replay over.
type SweepJob struct {
	Graph   GraphSpec            `json:"graph"`
	Grid    map[string][]float64 `json:"grid"` // param name -> values to try
	Symbols []string             `json:"symbols"`
	History HistoryRef           `json:"history"`
	Forward int                  `json:"forward"` // bars ahead to measure
	Seed    int64                `json:"seed"`    // reproducibility
}
