package detect

import (
	"fmt"
	"testing"
)

// fakeBatch mimics the screen and Turtle detectors: it republishes the same
// snapshot on every Evaluate, which is exactly the shape that flooded the feed
// before the Registry deduplicated by Signal ID.
type fakeBatch struct {
	name     string
	emitted  int
	withID   bool
	calls    int
	resetHit int
}

func (f *fakeBatch) Name() string        { return f.name }
func (f *fakeBatch) Title() string       { return f.name }
func (f *fakeBatch) Description() string { return "test" }
func (f *fakeBatch) Batch() bool         { return true }
func (f *fakeBatch) Observe(Event)       {}
func (f *fakeBatch) Params() map[string]any {
	return map[string]any{}
}
func (f *fakeBatch) Reset() { f.resetHit++ }

func (f *fakeBatch) Evaluate(int64) []Signal {
	f.calls++
	sig := Signal{
		Detector: f.name,
		Kind:     "PUMP",
		Symbol:   "TESTUSDT",
		Severity: "ALERT",
		Score:    60,
	}
	if f.withID {
		// Identity is the snapshot, so repeated Evaluates carry the same ID.
		sig.ID = fmt.Sprintf("%s|TESTUSDT|PUMP|%d", f.name, 1792095000000)
	}
	return []Signal{sig}
}

func TestRegistryEmitsEachIDOnce(t *testing.T) {
	reg := NewRegistry()
	det := &fakeBatch{name: "batch", withID: true}
	reg.Register(det)

	total := 0
	for i := 0; i < 5; i++ {
		total += len(reg.Evaluate(int64(1792095000000 + i*1000)))
	}
	if total != 1 {
		t.Fatalf("a batch detector republishing one snapshot must emit once, got %d signals over 5 ticks", total)
	}
	if det.calls != 5 {
		t.Fatalf("detector should still be evaluated every tick, got %d calls", det.calls)
	}
}

func TestRegistryPassesThroughUnidentifiedSignals(t *testing.T) {
	reg := NewRegistry()
	reg.Register(&fakeBatch{name: "stream", withID: false})

	total := 0
	for i := 0; i < 5; i++ {
		total += len(reg.Evaluate(int64(1792095000000 + i*1000)))
	}
	if total != 5 {
		t.Fatalf("signals without an ID must pass through (streaming detectors "+
			"rate-limit themselves), got %d over 5 ticks", total)
	}
}

func TestDisablingSkipsEvaluationAndPreservesCooldowns(t *testing.T) {
	reg := NewRegistry()
	det := &fakeBatch{name: "batch", withID: true}
	reg.Register(det)

	if got := len(reg.Evaluate(1000)); got != 1 {
		t.Fatalf("expected the first evaluation to emit, got %d", got)
	}
	if !reg.SetEnabled("batch", false) {
		t.Fatal("SetEnabled should find a registered detector")
	}
	if got := len(reg.Evaluate(2000)); got != 0 {
		t.Fatalf("a disabled detector must not be evaluated, got %d signals", got)
	}
	// Reset clears volatile state; it must NOT be relied on to clear cooldowns.
	if det.resetHit != 1 {
		t.Fatalf("disabling should request a volatile-state reset, got %d", det.resetHit)
	}
	if !reg.SetEnabled("batch", true) {
		t.Fatal("re-enable should succeed")
	}
	// The same ID must still be suppressed after a disable/enable cycle even
	// though a reset was requested.
	if got := len(reg.Evaluate(3000)); got != 0 {
		t.Fatalf("re-enabling must not re-emit an already-seen ID, got %d", got)
	}
}

func TestSetEnabledRejectsUnknownDetector(t *testing.T) {
	reg := NewRegistry()
	if reg.SetEnabled("nope", true) {
		t.Fatal("SetEnabled should report failure for an unknown detector")
	}
}

func TestDescribeReportsParamsAndCounters(t *testing.T) {
	reg := NewRegistry()
	reg.Register(&fakeBatch{name: "batch", withID: true})
	reg.Evaluate(5000)

	descriptors := reg.Describe()
	if len(descriptors) != 1 {
		t.Fatalf("expected 1 descriptor, got %d", len(descriptors))
	}
	d := descriptors[0]
	if d.Name != "batch" || !d.Enabled || !d.Batch {
		t.Fatalf("descriptor fields wrong: %+v", d)
	}
	if d.SignalCount != 1 {
		t.Fatalf("expected signal count 1, got %d", d.SignalCount)
	}
	if d.Params == nil {
		t.Fatal("params must be non-nil so the UI can render controls")
	}
}
