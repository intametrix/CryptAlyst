package graph

import "sort"

// Registry maps a node ID to its Node. It is the catalog the palette renders
// from and the runtime instantiates from.
type Registry struct {
	nodes map[string]Node
}

// NewRegistry returns an empty registry.
func NewRegistry() *Registry {
	return &Registry{nodes: map[string]Node{}}
}

// Register adds a node, overwriting any previous node with the same ID.
func (r *Registry) Register(n Node) {
	r.nodes[n.ID()] = n
}

// Get returns the node with the given ID.
func (r *Registry) Get(id string) (Node, bool) {
	n, ok := r.nodes[id]
	return n, ok
}

// List returns all nodes sorted by ID for stable iteration.
func (r *Registry) List() []Node {
	out := make([]Node, 0, len(r.nodes))
	for _, n := range r.nodes {
		out = append(out, n)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID() < out[j].ID() })
	return out
}

// ByKind returns the nodes of a single kind, sorted by ID.
func (r *Registry) ByKind(k Kind) []Node {
	var out []Node
	for _, n := range r.nodes {
		if n.Kind() == k {
			out = append(out, n)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID() < out[j].ID() })
	return out
}
