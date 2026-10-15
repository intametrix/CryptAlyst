package main

import (
	"encoding/json"
	"fmt"
	"math"
	"math/rand"
	"os"
	"path/filepath"
	"testing"
)

// writeShortLedger writes a synthetic mixed ledger: prediction lines with
// planted features, label_10s lines driven by sigmoid(2.0*z(burst)-0.3) with
// ~17% DISCARD. If noise is true the label is independent of the features.
func writeShortLedger(t *testing.T, n int, noise bool) string {
	t.Helper()
	rng := rand.New(rand.NewSource(11))
	var lines []string
	bmMean, bmStd := 8.5, 4.33
	for i := 0; i < n; i++ {
		bm := 1 + 15*rng.Float64()
		id := "flow-onset|AAAUSDT|PUMP|" + itoa(i)
		var feat map[string]float64
		if noise {
			// Pure-noise control: a constant feature standardizes to 0, so
			// the model provably cannot beat the pooled base rate and the
			// no-lift refusal must fire.
			feat = map[string]float64{"noise_const": 2.0}
		} else {
			feat = map[string]float64{"burst_multiple": bm, "vol_ratio": 2 + 3*rng.Float64()}
		}
		p := map[string]any{"type": "prediction", "prediction": map[string]any{
			"id": id, "detector": "flow-onset", "kind": "PUMP", "symbol": "AAAUSDT",
			"direction": 1, "severity": "ALERT", "score": 60.0, "price": 1.0,
			"ts_ms": int64(1_700_000_000_000 + i*1000), "features": feat}}
		b, _ := json.Marshal(p)
		lines = append(lines, string(b))

		label := "DISCARD"
		if rng.Float64() > 0.17 {
			z := (bm - bmMean) / bmStd
			logit := -0.3 + 2.0*z
			if noise {
				logit = -0.3 + 0.0*z
			}
			if rng.Float64() < 1/(1+math.Exp(-logit)) {
				label = "CORRECT"
			} else {
				label = "WRONG"
			}
		}
		l := map[string]any{"type": "label_10s", "band_pct": 0.1, "direction": 1,
			"entry": 1.0, "id": id, "label": label, "price": 1.0, "symbol": "AAAUSDT",
			"ts_ms": int64(1_700_000_000_000 + i*1000)}
		b, _ = json.Marshal(l)
		lines = append(lines, string(b))
	}
	// One orphan label with no matching prediction.
	l := map[string]any{"type": "label_10s", "band_pct": 0.1, "id": "no-such-id",
		"label": "CORRECT", "symbol": "AAAUSDT", "ts_ms": int64(1_700_000_000_000)}
	b, _ := json.Marshal(l)
	lines = append(lines, string(b))

	path := filepath.Join(t.TempDir(), "short_ledger.jsonl")
	if err := os.WriteFile(path, []byte(joinLines(lines)), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

func joinLines(ls []string) string {
	out := ""
	for _, l := range ls {
		out += l + "\n"
	}
	return out
}

func TestShortPlantedSignalShips(t *testing.T) {
	path := writeShortLedger(t, 600, false)
	st, err := load(path, 1.5, 1.0)
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	out := filepath.Join(t.TempDir(), "calibration_10s.json")
	stats := computeShort(st, out)

	if stats.labels != 601 {
		t.Fatalf("labels = %d, want 601 (600 + 1 orphan)", stats.labels)
	}
	if stats.usable+stats.discard != 601 {
		t.Fatalf("usable+discard = %d, want 601", stats.usable+stats.discard)
	}
	discardRate := float64(stats.discard) / float64(stats.labels)
	if discardRate < 0.10 || discardRate > 0.25 {
		t.Fatalf("discard rate = %.3f, want ~0.17", discardRate)
	}
	if stats.withoutFeatures != 1 {
		t.Fatalf("labels_without_features = %d, want 1", stats.withoutFeatures)
	}
	// Base rate sanity: planted rate at z~0 is sigmoid(-0.3) ~ 0.43, plus
	// DISCARD rows are excluded, skewing it a bit; just check plausible band.
	if stats.baseRate < 0.3 || stats.baseRate > 0.7 {
		t.Fatalf("base rate = %.3f, want in [0.3, 0.7]", stats.baseRate)
	}
	if !stats.shipped {
		t.Fatalf("model not shipped: refusal=%q brier=%.4f base=%.4f", stats.refusal, stats.brier, stats.baseBrier)
	}
	if stats.brier >= stats.baseBrier*shortLiftMargin {
		t.Fatalf("brier %.4f misses lift margin on base %.4f yet shipped", stats.brier, stats.baseBrier)
	}
	raw, err := os.ReadFile(out)
	if err != nil {
		t.Fatalf("calibration_10s.json not written: %v", err)
	}
	var doc struct {
		LabelRule string `json:"label_rule"`
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("bad json: %v", err)
	}
	if doc.LabelRule != shortLabelRule {
		t.Fatalf("label_rule = %q, want %q", doc.LabelRule, shortLabelRule)
	}
	// Per-detector breakdown present.
	if d, ok := stats.byDetector["flow-onset"]; !ok || d.usable == 0 {
		t.Fatalf("per-detector breakdown missing: %+v", stats.byDetector)
	}
}

func TestShortNoLiftRefuses(t *testing.T) {
	path := writeShortLedger(t, 600, true)
	st, err := load(path, 1.5, 1.0)
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	out := filepath.Join(t.TempDir(), "calibration_10s.json")
	stats := computeShort(st, out)
	if stats.shipped {
		t.Fatalf("pure-noise fit shipped: brier=%.4f base=%.4f", stats.brier, stats.baseBrier)
	}
	if stats.refusal == "" {
		t.Fatal("expected a refusal message")
	}
	if _, err := os.Stat(out); err == nil {
		t.Fatal("no-lift refusal must not write a file")
	}
}

// itoa renders a synthetic prediction id.
func itoa(i int) string {
	return fmt.Sprintf("x%d", i)
}
