// Package gpuagent is the CPU-side sweep executor: it decodes a sweep job
// envelope, rebuilds the graph, runs the parameter sweep, and returns the
// result. It is the audit path — the GPU agent must reproduce these results
// within tolerance, and it shares the same envelope contract.
package gpuagent

import (
	"fmt"

	"pumpdump/backend/internal/envelope"
	"pumpdump/backend/internal/graph"
)

// Run executes a sweep job over the given history (contexts + closes). It is
// CPU-first and correct; a GPU runner can later fan the same enumeration across
// the device and return the identical result shape.
func Run(job envelope.SweepJob, contexts []graph.Context, closes []float64) (envelope.SweepResult, error) {
	ctx := &graph.Context{}
	// Validate the spec up front so an unknown node fails loudly rather than
	// being silently skipped inside the sweep's build closure.
	for _, ns := range job.Graph.Nodes {
		if _, err := instantiate(ns, nil, ctx); err != nil {
			return envelope.SweepResult{}, err
		}
	}
	build := func(combo map[string]float64) (*graph.Graph, *graph.VerdictNode) {
		g := graph.NewGraph()
		var verdict *graph.VerdictNode
		for _, ns := range job.Graph.Nodes {
			n, err := instantiate(ns, combo, ctx)
			if err != nil {
				continue
			}
			if v, ok := n.(*graph.VerdictNode); ok {
				verdict = v
			}
			g.Add(n)
		}
		for _, ls := range job.Graph.Links {
			g.Link(graph.Link{
				From: ls.From, FromPort: ls.FromPort,
				To: ls.To, ToPort: ls.ToPort,
				Weight: ls.Weight, Expr: ls.Expr,
			})
		}
		return g, verdict
	}

	res, err := graph.Sweep(build, graph.Grid(job.Grid), contexts, closes, ctx, job.Forward)
	if err != nil {
		return envelope.SweepResult{}, err
	}

	combos := make([]envelope.ComboResult, len(res))
	for i, r := range res {
		combos[i] = envelope.ComboResult{
			Params:  r.Params,
			HitRate: r.HitRate,
			Equity:  r.Equity,
			Signals: r.Signals,
			Right:   r.Right,
		}
	}
	return envelope.SweepResult{
		JobID:  jobID(job),
		Combos: combos,
		Device: "cpu",
	}, nil
}

// jobID derives a stable id for a job (the spec hash is out of scope; the seed
// and node count are a cheap deterministic stand-in).
func jobID(job envelope.SweepJob) string {
	return fmt.Sprintf("sweep_%d_%d", job.Seed, len(job.Graph.Nodes))
}

// instantiate rebuilds a node from its declarative spec. Combo params override
// the spec's own params, so a sweep varies a node's settings without rebuilding
// the whole graph.
func instantiate(spec envelope.NodeSpec, combo map[string]float64, ctx *graph.Context) (graph.Node, error) {
	f := func(name string, def float64) float64 {
		if v, ok := combo[name]; ok {
			return v
		}
		if v, ok := spec.Params[name].(float64); ok {
			return v
		}
		return def
	}
	str := func(name, def string) string {
		if v, ok := spec.Params[name].(string); ok {
			return v
		}
		return def
	}

	switch spec.ID {
	case "source.constant":
		return graph.ConstantNode{Value: f("value", 0), Type: graph.TypeScore}, nil
	case "calc.threshold":
		return graph.ThresholdNode{Threshold: f("threshold", 50)}, nil
	case "calc.combine":
		return graph.CombineNode{}, nil
	case "calc.equation":
		return graph.EquationNode{Equation: str("equation", ""), OutType: graph.TypeScore}, nil
	case "source.price":
		return graph.PriceSource("", func(string) float64 { return ctx.Price }), nil
	case "source.volume":
		return graph.VolumeSource("", func(string) float64 { return ctx.Volume }), nil
	case "source.flow":
		return graph.FlowSource("", func(string) float64 { return ctx.TakerRatio }), nil
	case "source.ta":
		return graph.TASource("", func(string) float64 { return ctx.TA }), nil
	case "source.regime":
		return graph.RegimeSource(func() string { return ctx.Regime }), nil
	case "source.sentiment":
		return graph.SentimentSource("", func(string) float64 { return ctx.Sentiment }), nil
	case "sink.verdict":
		return &graph.VerdictNode{}, nil
	default:
		return nil, fmt.Errorf("gpuagent: unknown node %q", spec.ID)
	}
}
