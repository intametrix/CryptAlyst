package ledger

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	"pumpdump/backend/internal/detect"
)

// fakeMarket serves a scripted price per symbol.
type fakeMarket struct {
	prices map[string]float64
}

func (f fakeMarket) Price(_ context.Context, symbol string) (float64, error) {
	return f.prices[symbol], nil
}

func newTestRecorder(t *testing.T, m PriceFetcher, cfg Config) *JSONLRecorder {
	t.Helper()
	cfg.Path = filepath.Join(t.TempDir(), "ledger.jsonl")
	r, err := New(cfg.Path, m, cfg)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	t.Cleanup(func() { r.Close() })
	return r
}

func testSignal(id, kind, symbol string, dir int, price int64) detect.Signal {
	return detect.Signal{
		ID:        id,
		Detector:  "flow-onset",
		Kind:      kind,
		Symbol:    symbol,
		Direction: dir,
		Severity:  "ALERT",
		Score:     60,
		Price:     float64(price),
		TSMS:      1000,
		Metrics: map[string]any{
			"burst_multiple": 7.5,
			"agg_confirmed":  true,
			"label":          "not-a-number",
		},
		Components: map[string]float64{"burst": 0.8},
	}
}

func TestRecordSkipsNonPumpDump(t *testing.T) {
	r := newTestRecorder(t, fakeMarket{map[string]float64{}}, Config{})
	r.Record(testSignal("x", "BREAKOUT_LONG", "BTCUSDT", 1, 100))
	if len(r.open) != 0 {
		t.Fatalf("non PUMP/DUMP recorded: %d open", len(r.open))
	}
	r.Record(testSignal("y", "PUMP", "BTCUSDT", 1, 100))
	if len(r.open) != 1 {
		t.Fatalf("PUMP not recorded: %d open", len(r.open))
	}
	p := r.open["y"].pred
	if p.Features["burst_multiple"] != 7.5 || p.Features["component_burst"] != 0.8 {
		t.Fatalf("features wrong: %v", p.Features)
	}
	if _, bad := p.Features["label"]; bad {
		t.Fatalf("non-numeric metric leaked into features")
	}
}

func TestLabellingCorrect(t *testing.T) {
	m := fakeMarket{map[string]float64{"AAA": 102}} // +2% > 1.5% target
	r := newTestRecorder(t, m, Config{Horizon: time.Minute})
	r.Record(testSignal("p1", "PUMP", "AAA", 1, 100))
	r.tick(context.Background(), 1000+61_000) // at horizon
	st := r.open["p1"]
	if !st.resolved {
		t.Fatal("not resolved at horizon")
	}
	if st.label != LabelCorrect {
		t.Fatalf("label = %s, want CORRECT", st.label)
	}
}

func TestLabellingWrongOnStopOut(t *testing.T) {
	m := fakeMarket{map[string]float64{"AAA": 98.5}} // -1.5% < -1% stop
	r := newTestRecorder(t, m, Config{Horizon: time.Minute})
	r.Record(testSignal("p1", "PUMP", "AAA", 1, 100))
	r.tick(context.Background(), 1000+10_000)
	st := r.open["p1"]
	if !st.resolved || st.label != LabelWrong {
		t.Fatalf("label = %q resolved=%v, want WRONG", st.label, st.resolved)
	}
	// A dump mirror: adverse = price rising.
	m2 := fakeMarket{map[string]float64{"BBB": 101.5}}
	r2 := newTestRecorder(t, m2, Config{Horizon: time.Minute})
	sig := testSignal("d1", "DUMP", "BBB", -1, 100)
	r2.Record(sig)
	r2.tick(context.Background(), 1000+10_000)
	if !r2.open["d1"].resolved || r2.open["d1"].label != LabelWrong {
		t.Fatalf("dump label = %q, want WRONG", r2.open["d1"].label)
	}
}

func TestLabellingExpired(t *testing.T) {
	m := fakeMarket{map[string]float64{"AAA": 100.5}} // +0.5% < target, no stop
	r := newTestRecorder(t, m, Config{Horizon: time.Minute})
	r.Record(testSignal("p1", "PUMP", "AAA", 1, 100))
	r.tick(context.Background(), 1000+61_000)
	if st := r.open["p1"]; !st.resolved || st.label != LabelExpired {
		t.Fatalf("label = %q resolved=%v, want EXPIRED", st.label, st.resolved)
	}
}

func TestReloadKeepsUnresolved(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "ledger.jsonl")
	m := fakeMarket{map[string]float64{"AAA": 100}}
	cfg := Config{Path: path, Horizon: time.Minute}
	r, err := New(path, m, cfg)
	if err != nil {
		t.Fatal(err)
	}
	open := testSignal("open1", "PUMP", "AAA", 1, 100)
	open.TSMS = 10_000_000 // horizon far in the future: stays open
	r.Record(open)
	r.Record(testSignal("done1", "PUMP", "AAA", 1, 100))
	r.tick(context.Background(), 1000+61_000) // resolves done1
	r.Close()

	r2, err := New(path, m, cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer r2.Close()
	if _, ok := r2.open["open1"]; !ok {
		t.Fatal("unresolved prediction lost on reload")
	}
	if _, ok := r2.open["done1"]; ok {
		t.Fatal("resolved prediction resurrected on reload")
	}
}

func TestReportAggregation(t *testing.T) {
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
	p2 := base
	p2.ID, p2.Severity, p2.Direction = "b", "CRITICAL", -1
	p3 := base
	p3.ID, p3.TSMS = "c", 3000 // unresolved
	write(predictionRecord{Type: "prediction", Prediction: base})
	write(predictionRecord{Type: "prediction", Prediction: p2})
	write(predictionRecord{Type: "prediction", Prediction: p3})
	write(resolutionRecord{Type: "resolution", ID: "a", Label: LabelCorrect, MFEPct: 2, MAEPct: 0.3,
		TimeToMFESec: 42, ResolvedMS: 90000, TargetPct: 1.5, StopPct: 1})
	write(resolutionRecord{Type: "resolution", ID: "b", Label: LabelWrong, MFEPct: 0.5, MAEPct: 1.2,
		ResolvedMS: 90000, TargetPct: 1.5, StopPct: 1})
	f.Close()

	rep, err := Report(path, 30*24*time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	if rep.Resolved != 2 || rep.Unresolved != 1 || rep.TotalPredictions != 3 {
		t.Fatalf("counts: %+v", rep)
	}
	if rep.Overall.Precision != 0.5 {
		t.Fatalf("precision = %v, want 0.5", rep.Overall.Precision)
	}
	if rep.Overall.Expectancy != (1.5-1.0)/2 {
		t.Fatalf("expectancy = %v", rep.Overall.Expectancy)
	}
	if rep.Overall.MedMFE != 1.25 || rep.Overall.MedMAE != 0.75 || rep.Overall.MedTTF != 21 {
		t.Fatalf("medians: %+v", rep.Overall)
	}
	if rep.BySeverity["ALERT"].N != 1 || rep.ByDirection["SHORT"].N != 1 || rep.ByDetector["d1"].N != 2 {
		t.Fatalf("groups: %+v %+v %+v", rep.BySeverity, rep.ByDirection, rep.ByDetector)
	}
	if rep.String() == "" {
		t.Fatal("empty String()")
	}
}
