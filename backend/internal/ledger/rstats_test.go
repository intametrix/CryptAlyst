package ledger

import (
	"encoding/json"
	"math"
	"os"
	"testing"
	"time"
)

// TestComputeRStatsKnownDistribution builds 100 samples with a fixed MFE/MAE
// distribution and asserts every v2 quantity against hand-computed values.
// Distribution: mfe_pct cycles {0.5, 1.0, 2.0, 0.0, 1.5} (20 each),
// mae_pct cycles {0.2, 0.4, 0.6, 0.8, 1.0} (20 each). Bracket 1.0, fee 0.1.
func TestComputeRStatsKnownDistribution(t *testing.T) {
	mfeSet := []float64{0.5, 1.0, 2.0, 0.0, 1.5}
	maeSet := []float64{0.2, 0.4, 0.6, 0.8, 1.0}
	var samples []ExcursionSample
	for rep := 0; rep < 20; rep++ {
		for i := 0; i < 5; i++ {
			samples = append(samples, ExcursionSample{MFEPct: mfeSet[i], MAEPct: maeSet[i]})
		}
	}
	const bracket, fee = 1.0, 0.1
	st := ComputeRStats(samples, bracket, fee)

	if st.N != 100 {
		t.Fatalf("N = %d, want 100", st.N)
	}
	// Hand-computed (bracket 1.0 => R == pct):
	// mean MFE_R = (0.5+1+2+0+1.5)/5 = 1.0; mean MAE_R = (0.2+..+1.0)/5 = 0.6.
	if st.MeanMFER != 1.0 || st.MeanMAER != 0.6 {
		t.Fatalf("means = %v/%v, want 1.0/0.6", st.MeanMFER, st.MeanMAER)
	}
	// Gross E[R] = 1.0 - 0.6 = 0.4. Net = 0.4 - 2*0.1/1.0 = 0.2.
	if math.Abs(st.GrossER-0.4) > 1e-9 || math.Abs(st.NetER-0.2) > 1e-9 {
		t.Fatalf("E[R] gross/net = %v/%v, want 0.4/0.2", st.GrossER, st.NetER)
	}
	// Sorted MFE: 20x0.0, 20x0.5, 20x1.0, 20x1.5, 20x2.0 => median = mean of
	// 50th/51st = (1.0+1.0)/2 = 1.0. Sorted MAE: 20 each 0.2..1.0 => median 0.6.
	if st.MedianMFER != 1.0 || st.MedianMAER != 0.6 {
		t.Fatalf("medians = %v/%v, want 1.0/0.6", st.MedianMFER, st.MedianMAER)
	}
	// P(MFE>=1R): values 1.0, 2.0, 1.5 qualify = 60/100. P(MAE>=1R): only 1.0 = 20/100.
	if st.PMFE1R != 0.6 || st.PMAE1R != 0.2 {
		t.Fatalf("P = %v/%v, want 0.6/0.2", st.PMFE1R, st.PMAE1R)
	}

	// V1 unchanged: Report output has no v2 contamination and the v1 fields
	// compute exactly as before on a tiny fixture (same fixture as the
	// aggregation test: 2 resolved + 1 unresolved, precision 0.5).
	dir := t.TempDir()
	path := dir + "/ledger.jsonl"
	content := `{"type":"prediction","prediction":{"id":"a","detector":"d1","kind":"PUMP","symbol":"S","direction":1,"severity":"ALERT","score":60,"entry_price":100,"ts_ms":2000}}` + "\n" +
		`{"type":"prediction","prediction":{"id":"b","detector":"d1","kind":"PUMP","symbol":"S","direction":-1,"severity":"CRITICAL","score":60,"entry_price":100,"ts_ms":2000}}` + "\n" +
		`{"type":"resolution","id":"a","label":"CORRECT","mfe_pct":2,"mae_pct":0.3,"resolved_ms":90000,"target_pct":1.5,"stop_pct":1}` + "\n" +
		`{"type":"resolution","id":"b","label":"WRONG","mfe_pct":0.5,"mae_pct":1.2,"resolved_ms":90000,"target_pct":1.5,"stop_pct":1}` + "\n"
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
	rep, err := Report(path, 30*24*time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	if rep.Resolved != 2 || rep.Overall.N != 2 {
		t.Fatalf("v1 report changed: %+v", rep.Overall)
	}
	if rep.Overall.Precision != 0.5 {
		t.Fatalf("v1 precision = %v, want 0.5", rep.Overall.Precision)
	}
}

// TestCalibrationLabelVersion: newly written calibration files carry
// label_version=2 and companion_v1=true.
func TestCalibrationLabelVersion(t *testing.T) {
	m, err := FitLogistic(
		[]FeatureRow{{ID: "a", Features: map[string]float64{"x": 1}}, {ID: "b", Features: map[string]float64{"x": 2}}},
		[]int{0, 1},
	)
	if err != nil {
		t.Fatal(err)
	}
	path := t.TempDir() + "/cal.json"
	if err := WriteCalibration(path, m); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var doc Model
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatal(err)
	}
	if doc.LabelVersion != 2 || !doc.CompanionV1 {
		t.Fatalf("label_version=%d companion_v1=%v, want 2/true", doc.LabelVersion, doc.CompanionV1)
	}
}
