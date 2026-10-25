package graph

import "pumpdump/backend/internal/detect"

// Catalog builds the designer palette: the built-in calc/source nodes plus one
// ModelNode adapter per registered detector. It is what the /api/graph/nodes
// endpoint serves, so the UI lists real, runnable nodes without special-casing.
func Catalog(detectors *detect.Registry) *Registry {
	r := NewRegistry()
	r.Register(ConstantNode{Type: TypeScore})
	r.Register(PriceSource("", func(string) float64 { return 0 }))
	r.Register(VolumeSource("", func(string) float64 { return 0 }))
	r.Register(FlowSource("", func(string) float64 { return 0 }))
	r.Register(TASource("", func(string) float64 { return 0 }))
	r.Register(RegimeSource(func() string { return "sideways" }))
	r.Register(SentimentSource("", func(string) float64 { return 0 }))
	r.Register(ThresholdNode{Threshold: 50})
	r.Register(CombineNode{})
	r.Register(EquationNode{OutType: TypeScore})
	if detectors != nil {
		for _, name := range detectors.Names() {
			if d, ok := detectors.Get(name); ok {
				r.Register(ModelNode{Det: d})
			}
		}
	}
	return r
}
