package operator

import "testing"

func TestWhoProduces(t *testing.T) {
	r := Default()
	got := r.WhoProduces("intel.forecast")
	if len(got) != 1 || got[0] != "forecast" {
		t.Fatalf("who produces intel.forecast: %v", got)
	}
	if hs := r.WhoProduces("intel.ta"); len(hs) != 1 || hs[0] != "tad" {
		t.Fatalf("who produces intel.ta: %v", hs)
	}
}

func TestRegisterIsIdempotentByNeed(t *testing.T) {
	r := New()
	r.Register(Operator{Name: "a", Produces: []string{"x"}})
	r.Register(Operator{Name: "a", Produces: []string{"x"}})
	if got := r.WhoProduces("x"); len(got) != 1 {
		t.Fatalf("duplicate registration indexed twice: %v", got)
	}
}

func TestObserveTracksLiveness(t *testing.T) {
	r := Default()
	r.Observe("market_data", false)
	r.Observe("market_data", false)
	all := r.All()
	for _, op := range all {
		if op.Name == "market_data" && op.Invocations != 2 {
			t.Fatalf("invocations = %d", op.Invocations)
		}
	}
	if stale := r.Stale(); len(stale) != 0 {
		t.Fatalf("freshly observed operator reported stale: %v", stale)
	}
}
