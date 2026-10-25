package graph

import (
	"fmt"
	"sort"
)

// Graph is a strategy: a set of nodes wired together by links.
//
// The graph owns the wiring, not the nodes: a node is added once and may be
// referenced by any number of links. Evaluation is eager and topological — the
// nodes are ordered so that every node runs after the nodes that feed it, and
// each link maps a source output into a target input through its equation.
type Graph struct {
	nodes map[string]Node
	links []Link
}

// NewGraph returns an empty graph.
func NewGraph() *Graph {
	return &Graph{nodes: map[string]Node{}}
}

// Add registers a node by its ID.
func (g *Graph) Add(n Node) {
	g.nodes[n.ID()] = n
}

// Link adds an edge. The link is validated by Validate, not by Link itself, so
// wiring can be built incrementally before it is checked.
func (g *Graph) Link(l Link) {
	g.links = append(g.links, l)
}

// Validate checks every link references real nodes and compatible ports, and
// that the graph is acyclic.
func (g *Graph) Validate() error {
	for _, l := range g.links {
		from, ok := g.nodes[l.From]
		if !ok {
			return fmt.Errorf("link %q: unknown node %q", l.ID, l.From)
		}
		to, ok := g.nodes[l.To]
		if !ok {
			return fmt.Errorf("link %q: unknown node %q", l.ID, l.To)
		}
		fp, ok := findPort(from.Outputs(), l.FromPort)
		if !ok {
			return fmt.Errorf("link %q: node %q has no output port %q", l.ID, l.From, l.FromPort)
		}
		tp, ok := findPort(to.Inputs(), l.ToPort)
		if !ok {
			return fmt.Errorf("link %q: node %q has no input port %q", l.ID, l.To, l.ToPort)
		}
		if !fp.Compatible(tp) {
			return fmt.Errorf("link %q: %s output (%s) cannot feed %s input (%s)", l.ID, l.FromPort, fp.Type, l.ToPort, tp.Type)
		}
	}
	_, err := g.topological()
	return err
}

// Evaluate runs every node once in topological order and returns the outputs
// keyed by node ID then output-port name.
func (g *Graph) Evaluate() (map[string]map[string]Signal, error) {
	order, err := g.topological()
	if err != nil {
		return nil, err
	}
	outputs := map[string]map[string]Signal{}
	for _, id := range order {
		n := g.nodes[id]
		in := map[string]Signal{}
		for _, l := range g.links {
			if l.To != id {
				continue
			}
			src, ok := outputs[l.From][l.FromPort]
			if !ok {
				return nil, fmt.Errorf("link %q: node %q produced no %q", l.ID, l.From, l.FromPort)
			}
			mapped, err := l.Transform(src)
			if err != nil {
				return nil, err
			}
			in[l.ToPort] = mapped
		}
		out, err := n.Eval(in)
		if err != nil {
			return nil, fmt.Errorf("node %q: %w", id, err)
		}
		outputs[id] = out
	}
	return outputs, nil
}

// topological returns node IDs in evaluation order (dependencies first) using
// Kahn's algorithm. A non-empty remaining set means a cycle.
func (g *Graph) topological() ([]string, error) {
	indeg := map[string]int{}
	adj := map[string][]string{}
	for id := range g.nodes {
		indeg[id] = 0
	}
	for _, l := range g.links {
		if _, ok := g.nodes[l.From]; !ok {
			return nil, fmt.Errorf("link %q: unknown node %q", l.ID, l.From)
		}
		if _, ok := g.nodes[l.To]; !ok {
			return nil, fmt.Errorf("link %q: unknown node %q", l.ID, l.To)
		}
		indeg[l.To]++
		adj[l.From] = append(adj[l.From], l.To)
	}

	var ready []string
	for id, d := range indeg {
		if d == 0 {
			ready = append(ready, id)
		}
	}
	sort.Strings(ready)

	var order []string
	for len(ready) > 0 {
		n := ready[0]
		ready = ready[1:]
		order = append(order, n)
		for _, to := range adj[n] {
			indeg[to]--
			if indeg[to] == 0 {
				ready = append(ready, to)
				sort.Strings(ready)
			}
		}
	}
	if len(order) != len(g.nodes) {
		return nil, fmt.Errorf("cycle detected in graph (%d of %d nodes resolved)", len(order), len(g.nodes))
	}
	return order, nil
}

func findPort(ports []Port, name string) (Port, bool) {
	for _, p := range ports {
		if p.Name == name {
			return p, true
		}
	}
	return Port{}, false
}
