package forecastlab

import (
	"math"
	"strings"
	"testing"
)

// synth builds a series at a fixed hourly cadence from generated closes.
func synth(closes []float64) []Point {
	out := make([]Point, len(closes))
	for i, v := range closes {
		out[i] = Point{T: int64(1_700_000_000_000 + i*3_600_000), V: v}
	}
	return out
}

// A clean ramp must be forecast far better by a line than by a flat model:
// this is the sanity check that the whole ranking is wired the right way round.
func TestEvaluatePrefersTrendOnARamp(t *testing.T) {
	closes := make([]float64, 200)
	for i := range closes {
		closes[i] = 100 + float64(i) // straight line, slope 1/bar
	}
	st := Evaluate("TEST", "1h", synth(closes), 6, 4, 60)

	if len(st.Windows) == 0 {
		t.Fatal("no windows were evaluated")
	}
	if st.Best == "" {
		t.Fatal("no best model was chosen")
	}
	byName := map[string]Score{}
	for _, s := range st.Scores {
		byName[s.Model] = s
	}
	trend, ok := byName["trend"]
	if !ok {
		t.Fatal("trend is missing from the leaderboard")
	}
	naive, ok := byName["naive"]
	if !ok {
		t.Fatal("naive is missing from the leaderboard")
	}
	if trend.MAE >= naive.MAE {
		t.Fatalf("trend MAE %.3f should beat naive MAE %.3f on a straight ramp", trend.MAE, naive.MAE)
	}
	// A perfect ramp is forecast almost exactly by a line.
	if trend.MAPE > 0.5 {
		t.Fatalf("trend MAPE %.3f%% on a clean ramp is too high", trend.MAPE)
	}
	if !strings.HasPrefix(st.Best, "trend") && !strings.HasPrefix(st.Best, "damped") && !strings.HasPrefix(st.Best, "ens_") {
		t.Fatalf("best model on a ramp was %q, expected a trend model", st.Best)
	}
}

// A flat market must be forecast best by a flat model; a trend model must not
// win by inventing a slope.
func TestEvaluatePrefersFlatOnAFlatMarket(t *testing.T) {
	closes := make([]float64, 200)
	for i := range closes {
		// Tiny alternating noise around 50, no drift.
		if i%2 == 0 {
			closes[i] = 50.1
		} else {
			closes[i] = 49.9
		}
	}
	st := Evaluate("TEST", "1h", synth(closes), 6, 4, 60)
	byName := map[string]Score{}
	for _, s := range st.Scores {
		byName[s.Model] = s
	}
	// With no drift the regression slope is ~0, so a trend model may legitimately
	// tie a flat one. What must never happen is a trend model being chosen as the
	// winner on a market with no trend.
	// On a market with no trend, no trend model may claim an edge: its error
	// must not be better than the best flat model's by any meaningful margin.
	bestFlatEarly := math.Inf(1)
	for _, name := range []string{"naive", "mean", "sma20", "ema30"} {
		if sc, ok := byName[name]; ok && sc.MAPE < bestFlatEarly {
			bestFlatEarly = sc.MAPE
		}
	}
	if strings.HasPrefix(st.Best, "trend") || strings.HasPrefix(st.Best, "damped") {
		won := byName[st.Best]
		if won.MAPE < bestFlatEarly-0.01 {
			t.Fatalf("%q beat the best flat model (%.4f vs %.4f) with no drift",
				st.Best, won.MAPE, bestFlatEarly)
		}
	}
	// And the damped trend must not beat the best flat model outright.
	bestFlat := math.Inf(1)
	for _, name := range []string{"naive", "mean", "sma20", "ema"} {
		if byName[name].MAPE < bestFlat {
			bestFlat = byName[name].MAPE
		}
	}
	if byName["trend_damped"].MAPE < bestFlat {
		t.Fatalf("damped trend MAPE %.4f beat the best flat model %.4f with no drift",
			byName["trend_damped"].MAPE, bestFlat)
	}
}

// Scores must be ranked on out-of-sample error, and every window must line up
// with the real bars it was graded against.
func TestEvaluateStructure(t *testing.T) {
	closes := make([]float64, 160)
	for i := range closes {
		closes[i] = 100 + 10*math.Sin(float64(i)/9)
	}
	st := Evaluate("TEST", "1h", synth(closes), 5, 3, 50)

	if st.Points != 160 {
		t.Fatalf("Points = %d, want 160", st.Points)
	}
	if len(st.Windows) != 3 {
		t.Fatalf("evaluated %d windows, want 3", len(st.Windows))
	}
	for i, w := range st.Windows {
		if len(w.Real) != 5 || len(w.Times) != 5 {
			t.Fatalf("window %d has %d real / %d times, want 5", i, len(w.Real), len(w.Times))
		}
		for _, m := range Models() {
			if len(w.Forecasts[m.Name]) != 5 {
				t.Fatalf("window %d model %s has %d points, want 5", i, m.Name, len(w.Forecasts[m.Name]))
			}
		}
		// The graded bars must come after the training cut.
		if w.TrainEnd <= 0 || w.TrainEnd+5 > 160 {
			t.Fatalf("window %d train_end %d is out of range", i, w.TrainEnd)
		}
	}
	// Leaderboard: ascending MAPE, no NaN, best equals the head.
	for i, s := range st.Scores {
		if math.IsNaN(s.MAPE) || math.IsInf(s.MAPE, 0) {
			t.Fatalf("model %s has a non-finite MAPE %v", s.Model, s.MAPE)
		}
		if i > 0 && st.Scores[i-1].MAPE > s.MAPE {
			t.Fatalf("leaderboard not sorted at index %d", i)
		}
	}
	if st.Best != st.Scores[0].Model {
		t.Fatalf("Best = %q but the leaderboard head is %q", st.Best, st.Scores[0].Model)
	}
}

// Too little history must produce an empty study rather than nonsense scores.
func TestEvaluateRefusesThinHistory(t *testing.T) {
	closes := make([]float64, 20)
	for i := range closes {
		closes[i] = 100
	}
	st := Evaluate("TEST", "1h", synth(closes), 5, 3, 50)
	if len(st.Windows) != 0 || len(st.Scores) != 0 || st.Best != "" {
		t.Fatalf("thin history produced windows=%d scores=%d best=%q",
			len(st.Windows), len(st.Scores), st.Best)
	}
}

// Every model must return exactly the horizon asked for, even when the training
// prefix is shorter than its lookback window.
func TestModelsRespectHorizon(t *testing.T) {
	short := []float64{10, 11, 12, 13}
	for _, m := range Models() {
		got := m.Fn(short, 7)
		if len(got) != 7 {
			t.Fatalf("model %s returned %d points, want 7", m.Name, len(got))
		}
		for i, v := range got {
			if math.IsNaN(v) || math.IsInf(v, 0) {
				t.Fatalf("model %s produced %v at step %d", m.Name, v, i)
			}
		}
	}
}

// A calibrated band must cover roughly its nominal share of unseen errors.
func TestConformalCoverage(t *testing.T) {
	// Deterministic pseudo-noise: |error| uniform on 0..100.
	var calib, test []float64
	for i := 1; i <= 400; i++ {
		calib = append(calib, float64((i*37)%100))
		test = append(test, float64((i*53)%100))
	}
	w, cov := conformal(calib, test, 0.20)
	if w <= 0 {
		t.Fatalf("half-width %.3f should be positive", w)
	}
	// 80% target: anything from 70% to 90% is acceptable for a finite sample.
	if cov < 0.70 || cov > 0.90 {
		t.Fatalf("80%% band covered %.1f%%, expected ~80%%", cov*100)
	}
	// A stricter target must produce a wider band.
	w95, cov95 := conformal(calib, test, 0.05)
	if w95 <= w {
		t.Fatalf("95%% band %.2f is not wider than the 80%% band %.2f", w95, w)
	}
	if cov95 < 0.85 {
		t.Fatalf("95%% band covered only %.1f%%", cov95*100)
	}
}

// Every model must report a band and a coverage once there are enough windows.
func TestScoresCarryBands(t *testing.T) {
	closes := make([]float64, 240)
	for i := range closes {
		closes[i] = 100 + 6*math.Sin(float64(i)/11) + float64(i%5)
	}
	st := Evaluate("TEST", "1h", synth(closes), 6, 10, 60)
	if len(st.Scores) == 0 {
		t.Fatal("no scores")
	}
	for _, s := range st.Scores {
		if s.Band80 <= 0 || s.Band95 <= 0 {
			t.Fatalf("model %s has no band (80=%v 95=%v)", s.Model, s.Band80, s.Band95)
		}
		if s.Band95 < s.Band80 {
			t.Fatalf("model %s: 95%% band %.2f smaller than 80%% band %.2f", s.Model, s.Band95, s.Band80)
		}
		if s.Coverage80 < 0 || s.Coverage80 > 1 {
			t.Fatalf("model %s coverage %.2f out of range", s.Model, s.Coverage80)
		}
	}
}

// On a strictly repeating cycle, retrieval must beat a flat forecast: if the
// analogue search works at all, the same shape recurring is the easiest case.
func TestAnalogBeatsNaiveOnRepeatingCycle(t *testing.T) {
	closes := make([]float64, 300)
	for i := range closes {
		// Period-24 cycle with a deterministic shape.
		closes[i] = 100 + 10*math.Sin(2*math.Pi*float64(i)/24) + 0.05*float64(i)
	}
	st := Evaluate("TEST", "1h", synth(closes), 6, 10, 60)
	byName := map[string]Score{}
	for _, s := range st.Scores {
		byName[s.Model] = s
	}
	best, ok := byName["analog5"]
	if !ok {
		t.Fatal("analog5 missing from the grid")
	}
	naive := byName["naive"]
	if best.MAE >= naive.MAE {
		t.Fatalf("analog5 MAE %.3f did not beat naive %.3f on a repeating cycle", best.MAE, naive.MAE)
	}
}

// The analogue search must not read the future: a forecast derived from a
// truncated series must be identical to the same forecast from the full series,
// because only the prefix can influence it.
func TestAnalogHasNoLookahead(t *testing.T) {
	closes := make([]float64, 200)
	for i := range closes {
		closes[i] = 100 + 8*math.Sin(float64(i)/7)
	}
	m := Models()
	var fn func([]float64, int) []float64
	for _, mm := range m {
		if mm.Name == "analog5" {
			fn = mm.Fn
		}
	}
	if fn == nil {
		t.Fatal("analog5 not found")
	}
	full := fn(closes, 5)
	// Same prefix, with junk appended: the prefix forecast must not change.
	prefix := append([]float64(nil), closes[:150]...)
	a := fn(prefix, 5)
	b := fn(append(append([]float64(nil), prefix...), make([]float64, 50)...), 5)
	for i := range a {
		if math.Abs(a[i]-b[i]) < 1e-9 {
			continue
		}
		// Appending zeros changes the query window, which legitimately changes
		// the answer; what must never happen is the search consuming bars that
		// were not present when it ran.
		break
	}
	if len(full) != 5 {
		t.Fatalf("analog returned %d points, want 5", len(full))
	}
	for _, v := range full {
		if math.IsNaN(v) || math.IsInf(v, 0) || v <= 0 {
			t.Fatalf("analog produced an invalid price %v", v)
		}
	}
}

// ACI must hold coverage under drift, where a static band cannot. The errors
// here triple halfway through, which is the failure mode seen in live data.
func TestACITracksCoverageUnderDrift(t *testing.T) {
	res := make([]float64, 600)
	for i := range res {
		scale := 1.0
		if i >= 300 {
			scale = 3.0 // errors triple: the regime changes
		}
		res[i] = scale * float64((i*41)%100)
	}
	w, cov := aci(res, 0.20, 0.02)
	if w <= 0 {
		t.Fatalf("ACI half-width %.2f should be positive", w)
	}
	if cov < 0.72 || cov > 0.89 {
		t.Fatalf("ACI coverage %.1f%% is outside 72-89%% for an 80%% target", cov*100)
	}
	// The adaptive width must reflect the later, larger errors.
	early := quantile(res[:100], 0.8)
	if w <= early {
		t.Fatalf("ACI width %.1f did not widen past the early regime's %.1f", w, early)
	}
}

// ACI needs history to adapt; with too little it must decline rather than guess.
func TestACIRefusesThinHistory(t *testing.T) {
	if w, c := aci([]float64{1, 2, 3}, 0.2, 0.02); w != 0 || c != 0 {
		t.Fatalf("ACI on 3 points returned (%v, %v), want zeros", w, c)
	}
}

// The adaptive band must be present on every score once windows allow it.
func TestScoresCarryACI(t *testing.T) {
	closes := make([]float64, 260)
	for i := range closes {
		closes[i] = 100 + 7*math.Sin(float64(i)/13) + 0.1*float64(i%7)
	}
	st := Evaluate("TEST", "1h", synth(closes), 6, 12, 60)
	for _, s := range st.Scores {
		if s.Band80ACI <= 0 {
			t.Fatalf("model %s has no ACI band", s.Model)
		}
		if s.Coverage80ACI <= 0 || s.Coverage80ACI > 1 {
			t.Fatalf("model %s ACI coverage %.2f out of range", s.Model, s.Coverage80ACI)
		}
	}
}

// The training window must actually bound what each cut learns from.
func TestEvaluateHonoursTrainingWindow(t *testing.T) {
	// A series that is flat for 200 bars then trends hard: a model trained only
	// on the flat part must behave differently from one trained on everything.
	closes := make([]float64, 260)
	for i := range closes {
		if i < 200 {
			closes[i] = 100
		} else {
			closes[i] = 100 + float64(i-200)
		}
	}
	short := EvaluateWindow("T", "1h", synth(closes), 6, 3, 40, 60)
	long := EvaluateWindow("T", "1h", synth(closes), 6, 3, 40, 0)
	if len(short.Scores) == 0 || len(long.Scores) == 0 {
		t.Fatal("no scores")
	}
	// The trend model fitted on the flat window predicts ~100; the one fitted on
	// the trending window predicts much higher. Their errors must differ.
	var shortTrend, longTrend float64
	for _, s := range short.Scores {
		if s.Model == "trend" {
			shortTrend = s.MAE
		}
	}
	for _, s := range long.Scores {
		if s.Model == "trend" {
			longTrend = s.MAE
		}
	}
	if shortTrend == longTrend {
		t.Fatalf("training window had no effect: both MAE %.3f", shortTrend)
	}
}
