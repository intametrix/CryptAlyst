package correlation

import "testing"

func TestMintIsUnique(t *testing.T) {
	seen := map[string]bool{}
	for i := 0; i < 1000; i++ {
		id := Mint()
		if seen[id] {
			t.Fatalf("duplicate cid %s", id)
		}
		seen[id] = true
	}
}

func TestRecordCreatesAndAppends(t *testing.T) {
	s := New()
	s.Record("doc-1", "cid-a", "mint", "market_data", "ticks")
	s.Record("doc-1", "cid-b", "decision", "engine", "buy BTCUSDT")
	docs, steps, _ := s.Stats()
	if docs != 1 || steps != 2 {
		t.Fatalf("docs=%d steps=%d", docs, steps)
	}
	if id, ok := s.ByCID("cid-b"); !ok || id != "doc-1" {
		t.Fatalf("cid lookup failed: %s %v", id, ok)
	}
}

func TestResolveIsGatedAndRecorded(t *testing.T) {
	s := New()
	s.Record("doc-1", "cid-a", "mint", "feed", "")
	d, ok := s.Resolve("doc-1", "auditor")
	if !ok {
		t.Fatal("resolve failed")
	}
	last := d.Steps[len(d.Steps)-1]
	if last.Kind != "read" || last.Actor != "auditor" {
		t.Fatalf("read step not recorded: %+v", last)
	}
	_, _, reads := s.Stats()
	if reads != 1 {
		t.Fatalf("reads = %d", reads)
	}
}

func TestTraceWalksAncestry(t *testing.T) {
	s := New()
	s.Record("ticks-1", "c1", "mint", "market_data", "")
	s.Record("flow-1", "c2", "compute", "calculator", "")
	s.Link("flow-1", "ticks-1")
	s.Record("decision-1", "c3", "decision", "engine", "buy")
	s.Link("decision-1", "flow-1")
	chain := s.Trace("decision-1")
	if len(chain) != 3 {
		t.Fatalf("chain length %d, want 3", len(chain))
	}
	if chain[0].ID != "ticks-1" || chain[2].ID != "decision-1" {
		t.Fatalf("order wrong: %v", []string{chain[0].ID, chain[1].ID, chain[2].ID})
	}
}

func TestTraceHandlesCycles(t *testing.T) {
	s := New()
	s.Record("a", "c1", "mint", "x", "")
	s.Record("b", "c2", "mint", "x", "")
	s.Link("a", "b")
	s.Link("b", "a")
	if got := len(s.Trace("a")); got != 2 {
		t.Fatalf("cycle not handled: %d", got)
	}
}
