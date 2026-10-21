package scheduler

import (
	"sync"
	"testing"
)

// testEval runs evals through a blocking gate so tests control exactly when a
// worker may start, making the coalescing/lost-wakeup/bound assertions
// deterministic (no wall clock, no races).
type testEval struct {
	mu      sync.Mutex
	evals   []string
	started chan string
	release chan struct{}
}

func newTestEval() *testEval {
	return &testEval{started: make(chan string, 100), release: make(chan struct{})}
}

func (t *testEval) fn() func(string) {
	return func(sym string) {
		t.mu.Lock()
		t.evals = append(t.evals, sym)
		t.mu.Unlock()
		t.started <- sym
		<-t.release
	}
}

func (t *testEval) snapshot() []string {
	t.mu.Lock()
	defer t.mu.Unlock()
	out := make([]string, len(t.evals))
	copy(out, t.evals)
	return out
}

func has(s []string, sym string) bool {
	for _, x := range s {
		if x == sym {
			return true
		}
	}
	return false
}

// Coalescing: two events for the same symbol while it is still queued are one
// run; a third event while it is processing re-runs it once. Total = 2 evals.
func TestCoalescing(t *testing.T) {
	te := newTestEval()
	s := New(1, 1, 4, te.fn())

	s.Submit("X") // -> primary queue
	s.Submit("X") // busy + not processing -> coalesced (drop)
	<-te.started  // worker began X
	s.Submit("X") // processing -> dirty (re-run after)
	close(te.release)
	s.Close()

	evals := te.snapshot()
	if len(evals) != 2 {
		t.Fatalf("want 2 evals (queued run + dirty re-run), got %v", evals)
	}
	_, coalesced, _, _ := s.Stats()
	if coalesced != 1 {
		t.Fatalf("want 1 coalesced, got %d", coalesced)
	}
}

// No lost wakeup: with a full primary queue, a symbol goes to the secondary
// queue and is still evaluated (never silently dropped).
func TestNoLostWakeup(t *testing.T) {
	te := newTestEval()
	s := New(0, 1, 8, te.fn()) // primary capacity 0 -> always full

	s.Submit("X")
	close(te.release)
	s.Close()

	if !has(te.snapshot(), "X") {
		t.Fatalf("X was not evaluated despite the full queue: lost wakeup")
	}
	dropped, _, _, _ := s.Stats()
	if dropped != 1 {
		t.Fatalf("want 1 dropped (routed to secondary), got %d", dropped)
	}
}

// Dirty bound: under a flood the secondary queue is bounded; overflow evicts
// the oldest with a counter, and the evicted symbol is the only one not run.
func TestDirtyBound(t *testing.T) {
	te := newTestEval()
	s := New(0, 1, 2, te.fn()) // primary full, secondary holds 2

	s.Submit("X")
	s.Submit("Y")
	s.Submit("Z") // evicts X (dirtyQ was [X,Y])
	close(te.release)
	s.Close()

	evals := te.snapshot()
	if has(evals, "X") {
		t.Fatalf("X should have been evicted, got evals %v", evals)
	}
	if !has(evals, "Y") || !has(evals, "Z") {
		t.Fatalf("Y and Z should both be evaluated, got %v", evals)
	}
	_, _, evicted, _ := s.Stats()
	if evicted != 1 {
		t.Fatalf("want 1 evicted, got %d", evicted)
	}
}

// Non-blocking: Submit returns immediately even when everything is full, and
// Close drains cleanly.
func TestSubmitNonBlocking(t *testing.T) {
	te := newTestEval()
	s := New(0, 2, 1, te.fn())
	for i := 0; i < 100; i++ {
		s.Submit("S") // never blocks; most coalesce, some evict
	}
	close(te.release)
	s.Close()
	// The scheduler must have terminated without deadlock; no assertion on counts
	// beyond liveness (the flood is adversarial).
}
