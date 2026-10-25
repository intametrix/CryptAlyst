package graph

import (
	"testing"

	"pumpdump/backend/internal/detect"
)

func TestCatalogBuiltins(t *testing.T) {
	r := Catalog(nil)
	for _, id := range []string{"source.constant", "calc.threshold", "calc.combine", "calc.equation"} {
		if _, ok := r.Get(id); !ok {
			t.Errorf("catalog missing %q", id)
		}
	}
}

func TestCatalogWithDetectors(t *testing.T) {
	dr := detect.NewRegistry()
	dr.Register(fakeDetector{name: "flow-imbalance", title: "Flow Imbalance"})
	r := Catalog(dr)
	n, ok := r.Get("model.flow-imbalance")
	if !ok || n == nil {
		t.Fatal("catalog missing model.flow-imbalance adapter")
	}
	if n.Kind() != Model {
		t.Errorf("kind = %s, want model", n.Kind())
	}
}
