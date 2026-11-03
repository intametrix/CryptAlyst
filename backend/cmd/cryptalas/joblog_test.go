package main

import (
	"strings"
	"testing"
)

// The agent must show what a job produced, in one readable line.
func TestDigestResultForecast(t *testing.T) {
	got := digestResult(map[string]any{
		"method":   "trend",
		"last":     81238.01,
		"forecast": []any{81101.99, 81120.0, 81246.67},
	})
	for _, want := range []string{"forecast 3 points", "81101.99", "81246.67", "trend"} {
		if !strings.Contains(got, want) {
			t.Errorf("digestResult missing %q in %q", want, got)
		}
	}
}

func TestDigestResultFallbacks(t *testing.T) {
	if got := digestResult(nil); got != "no result" {
		t.Errorf("nil result digested to %q", got)
	}
	// A plain map lists its fields rather than showing nothing.
	got := digestResult(map[string]any{"trained": true, "device": "cuda"})
	if !strings.Contains(got, "device") || !strings.Contains(got, "trained") {
		t.Errorf("generic digest lost the fields: %q", got)
	}
}

// A result carrying an error must be reported as a failure, not a success.
func TestResultError(t *testing.T) {
	if msg, failed := resultError(map[string]any{"trained": true}); failed {
		t.Errorf("a clean result was treated as failed: %q", msg)
	}
	msg, failed := resultError(map[string]any{"error": "need >= 3 points"})
	if !failed || msg != "need >= 3 points" {
		t.Errorf("resultError = (%q, %v), want the error text and true", msg, failed)
	}
	if _, failed := resultError(map[string]any{"error": ""}); failed {
		t.Error("an empty error string was treated as a failure")
	}
}

func TestJobPayloadBytes(t *testing.T) {
	if n := jobPayloadBytes(nil); n != 4 {
		// json.Marshal(nil map) is "null"
		t.Errorf("nil payload measured %d bytes, want 4 (null)", n)
	}
	n := jobPayloadBytes(map[string]any{"series": []any{1, 2, 3}})
	if n <= 10 {
		t.Errorf("payload measured %d bytes, expected the encoded size", n)
	}
}

// The agent must refuse work beyond its capacity instead of accepting it and
// running everything at once: a GPU box cannot train ten models in parallel.
func TestSubmitRejectsWhenAtCapacity(t *testing.T) {
	a := &Agent{jobs: map[string]*Job{}, state: t.TempDir(), device: "cpu", gitSHA: "test", maxJobs: 1}
	a.ensureWorkers()

	// Flood the queue: capacity is maxJobs*4 for the buffer plus one worker.
	var rejected int
	for i := 0; i < 50; i++ {
		j := a.submit("m", map[string]any{"rows": []any{}}, "cpu", "", "train")
		a.mu.Lock()
		status, errMsg := j.Status, j.Error
		a.mu.Unlock()
		if status == "failed" && errMsg != "" {
			rejected++
		}
	}
	if rejected == 0 {
		t.Fatal("50 jobs against a 1-worker agent were all accepted: the queue is unbounded")
	}
	if got := a.rejected.Load(); got == 0 {
		t.Fatal("rejection counter did not move")
	}
}
