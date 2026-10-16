package ledger

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// TestReloadRestoresRunningExtremes is the regression test for the audit
// root cause: before the fix, a restart reset MFE/MAE to zero, producing
// impossible post-restart path records (running maxima decreasing) and
// resolutions that under-reported true excursions.
func TestReloadRestoresRunningExtremes(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "ledger.jsonl")
	m := fakeMarket{map[string]float64{"AAA": 100}}

	cfg := Config{Path: path, Horizon: time.Hour, SampleInterval: time.Second}
	r1, err := New(path, m, cfg)
	if err != nil {
		t.Fatal(err)
	}
	r1.Record(testSignal("p1", "PUMP", "AAA", 1, 100))
	// Drive price to +1.0% MFE before the "restart".
	m.prices["AAA"] = 101
	r1.tick(context.Background(), 1000+3000)
	if got := r1.open["p1"].mfe; got != 0.01 {
		t.Fatalf("pre-restart mfe = %v, want 0.01", got)
	}
	r1.Close()

	// Restart: reload must restore mfe=0.01 so the next path record cannot
	// show a decrease.
	r2, err := New(path, m, cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer r2.Close()
	st := r2.open["p1"]
	if st == nil {
		t.Fatal("prediction lost on reload")
	}
	if st.mfe != 0.01 {
		t.Fatalf("reload did not restore running MFE: got %v, want 0.01", st.mfe)
	}
	// A small adverse move now: MAE accumulates from zero, MFE must stay.
	m.prices["AAA"] = 99.5
	r2.tick(context.Background(), 1000+6000)
	if st.mfe != 0.01 {
		t.Fatalf("post-reload mfe decreased: %v", st.mfe)
	}
	if st.mae != 0.005 {
		t.Fatalf("post-reload mae = %v, want 0.005", st.mae)
	}
}

// TestOutlierGuardRejectsGarbageSamples: a single garbage print (10x price)
// must not enter MFE/MAE, and the next sane sample re-anchors.
func TestOutlierGuardRejectsGarbageSamples(t *testing.T) {
	m := fakeMarket{map[string]float64{"AAA": 100}}
	r := newTestRecorder(t, m, Config{Horizon: time.Hour, MaxSampleJumpPct: 25})
	r.Record(testSignal("p1", "PUMP", "AAA", 1, 100))

	m.prices["AAA"] = 1000 // 900% jump: garbage
	r.tick(context.Background(), 1000+3000)
	if got := r.open["p1"].mfe; got != 0 {
		t.Fatalf("garbage sample entered MFE: %v", got)
	}

	m.prices["AAA"] = 102 // sane +2%
	r.tick(context.Background(), 1000+6000)
	st := r.open["p1"]
	if st.mfe != 0.02 {
		t.Fatalf("sane sample rejected after garbage: mfe %v, want 0.02", st.mfe)
	}
}

// TestRetractionExcludesFromReport: retracted ids are excluded from accuracy
// numbers but counted explicitly.
func TestRetractionExcludesFromReport(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "ledger.jsonl")
	f, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	write := func(v any) {
		b, _ := json.Marshal(v)
		f.Write(append(b, '\n'))
	}
	base := Prediction{ID: "a", Detector: "d1", Kind: "PUMP", Symbol: "S", Direction: 1,
		Severity: "ALERT", Score: 60, EntryPrice: 100, TSMS: 2000}
	bad := base
	bad.ID = "b"
	write(predictionRecord{Type: "prediction", Prediction: base})
	write(predictionRecord{Type: "prediction", Prediction: bad})
	write(resolutionRecord{Type: "resolution", ID: "a", Label: LabelCorrect, MFEPct: 2,
		ResolvedMS: 90000, TargetPct: 1.5, StopPct: 1})
	write(resolutionRecord{Type: "resolution", ID: "b", Label: LabelWrong, MAEPct: 1.2,
		ResolvedMS: 90000, TargetPct: 1.5, StopPct: 1})
	write(map[string]any{"type": "retraction", "id": "b", "reason": "contaminated", "ts_ms": 91000})
	f.Close()

	rep, err := Report(path, 30*24*time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	if rep.Retracted != 1 {
		t.Fatalf("retracted = %d, want 1", rep.Retracted)
	}
	if rep.Resolved != 1 || rep.TotalPredictions != 1 {
		t.Fatalf("resolved=%d total=%d, want 1/1 (retracted id excluded)", rep.Resolved, rep.TotalPredictions)
	}
	if rep.Overall.Precision != 1.0 {
		t.Fatalf("precision = %v, want 1.0 (only the clean row counts)", rep.Overall.Precision)
	}
}
