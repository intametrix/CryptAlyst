package main

// Short-horizon (10s) learner: joins fastsample label_10s records to
// predictions by id, reports base rate / discard rate per detector, and fits
// a logistic model for P(CORRECT within the 10s band) — shipped only if it
// beats the pooled-base-rate Brier (the no-lift rule, locked pre-fit).

import (
	"fmt"
	"os"

	"pumpdump/backend/internal/ledger"
)

// minShortN is the minimum usable (CORRECT+WRONG) label count for the 10s
// model. Higher than the 15m bar: 10s labels are plentiful but noisy, and a
// reliability table on fewer is noise shaped like a table.
const minShortN = 300

// shortLiftMargin is part of the locked no-lift rule: a fit ships only if
// its in-sample Brier improves on the pooled-base-rate Brier by at least 3%
// relative. A strict "<" cannot refuse honestly — a zero-signal fit wins by
// ~1e-4 of pure in-sample noise — so "no lift" means no meaningful lift.
const shortLiftMargin = 0.97

const shortLabelRule = "CORRECT=1, WRONG=0, DISCARD excluded (10s +/-0.1% band, in-sample fit)"

// shortLabel is one label_10s record.
type shortLabel struct {
	id            string
	label         string
	band          float64 // band_pct, the locked 10s bracket
	direction     int
	entry         float64
	price         float64
	mfe, mae      float64 // recorded excursions if the record carries them
	hasExcursions bool
}

// shortDet is the per-detector breakdown.
type shortDet struct {
	total, usable, correct, discard int
}

// shortStats is the computed short-horizon report, returned for tests.
type shortStats struct {
	labels, usable, discard, withoutFeatures, fitN int
	baseRate, brier, baseBrier                     float64
	byDetector                                     map[string]shortDet
	shipped                                        bool
	refusal                                        string
}

func (st *store) labels() []*shortLabel { return st.short }

// computeShort joins labels to predictions and fits the 10s model. out is
// the calibration path used when the fit ships; feePct is the per-side taker
// fee for the v2 net expectancy line.
func computeShort(st *store, out string, feePct float64) shortStats {
	ls := st.labels()
	var s shortStats
	s.byDetector = map[string]shortDet{}
	s.labels = len(ls)
	if s.labels == 0 {
		s.refusal = "no label_10s records"
		return s
	}

	type usableRow struct {
		features map[string]float64
		label    int
		detector string
	}
	var fitRows []usableRow
	for _, l := range ls {
		det := "unknown"
		var feat map[string]float64
		if p, ok := st.byID[l.id]; ok && p != nil {
			det = p.detector
			feat = p.features
		} else {
			s.withoutFeatures++
		}
		d := s.byDetector[det]
		d.total++
		switch l.label {
		case "CORRECT":
			d.usable++
			d.correct++
			if feat != nil {
				fitRows = append(fitRows, usableRow{feat, 1, det})
			}
		case "WRONG":
			d.usable++
			if feat != nil {
				fitRows = append(fitRows, usableRow{feat, 0, det})
			}
		case "DISCARD":
			d.discard++
		default:
			// unknown labels are neither usable nor discard; ignore.
		}
		s.byDetector[det] = d
	}
	for _, d := range s.byDetector {
		s.usable += d.usable
		s.discard += d.discard
	}
	if s.usable == 0 {
		s.refusal = "no usable labels"
		return s
	}
	s.baseRate = float64(correctTotal(s.byDetector)) / float64(s.usable)

	fmt.Printf("\nSHORT-HORIZON (10s): labels=%d usable=%d discard=%d (%.1f%%) base_rate=%.3f labels_without_features=%d\n",
		s.labels, s.usable, s.discard, 100*float64(s.discard)/float64(s.labels), s.baseRate, s.withoutFeatures)
	dets := sortedKeys(s.byDetector)
	fmt.Printf("  %-16s %6s %6s %8s %8s\n", "detector", "usable", "disc", "base", "discard%")
	for _, k := range dets {
		d := s.byDetector[k]
		b := 0.0
		dp := 0.0
		if d.usable > 0 {
			b = float64(d.correct) / float64(d.usable)
		}
		if d.total > 0 {
			dp = 100 * float64(d.discard) / float64(d.total)
		}
		fmt.Printf("  %-16s %6d %6d %8.3f %7.1f%%\n", k, d.usable, d.discard, b, dp)
	}

	// V2 (expectancy-in-R) at the 10s band. label_10s records normally carry
	// no running extremes — only entry and label-time price — so the exact
	// MFE/MAE distribution is not recoverable. What IS recoverable is the
	// bracket-trade expectancy: CORRECT touched +band (+1R), WRONG touched
	// −band (−1R), so E[R] = 2p − 1 gross. When a record does carry
	// mfe_pct/mae_pct (newer fastsample), the real v2 block is used instead.
	if s.usable > 0 {
		fmt.Println("  V2 (10s, bracket=band):")
		var exact []v2Row
		p := s.baseRate
		for _, l := range ls {
			if (l.label == "CORRECT" || l.label == "WRONG") && l.hasExcursions {
				band := l.band
				if band <= 0 {
					band = 0.1
				}
				exact = append(exact, v2Row{mfe: l.mfe, mae: l.mae, bracket: band})
			}
		}
		if len(exact) >= minShortN/3 {
			fmt.Println("    " + v2Line("usable (recorded mfe/mae)", exact, feePct))
		} else {
			band := 0.1
			for _, l := range ls {
				if l.band > 0 {
					band = l.band
					break
				}
			}
			fmt.Printf("    bracket-trade expectancy (band-touch model, no mfe/mae in label_10s): E[R]gross=%+.3fR E[R]net=%+.3fR (p=%.3f, band=%.2f%%, fee=%.3f%%/side)\n",
				2*p-1, 2*p-1-2*feePct/band, p, band, feePct)
			fmt.Println("    (full v2 block needs mfe_pct/mae_pct fields in label_10s records)")
		}
	}

	if s.usable < minShortN {
		s.refusal = fmt.Sprintf("insufficient sample: %d usable, need %d for 10s calibration", s.usable, minShortN)
		fmt.Println(s.refusal)
		return s
	}

	rows := make([]ledger.FeatureRow, 0, len(fitRows))
	labels := make([]int, 0, len(fitRows))
	for _, r := range fitRows {
		rows = append(rows, ledger.FeatureRow{Features: r.features})
		labels = append(labels, r.label)
	}
	s.fitN = len(rows)
	if len(rows) < ledger.MinCalibrationN {
		s.refusal = fmt.Sprintf("insufficient sample: %d fit rows with features, need %d", len(rows), ledger.MinCalibrationN)
		fmt.Println(s.refusal)
		return s
	}
	m, err := ledger.FitLogistic(rows, labels)
	if err != nil {
		s.refusal = fmt.Sprintf("fit failed: %v", err)
		fmt.Println(s.refusal)
		return s
	}
	s.brier = m.Brier
	s.baseBrier = s.baseRate * (1 - s.baseRate)

	fmt.Printf("fit: n=%d (IN-SAMPLE) brier=%.4f base_brier=%.4f\n", s.fitN, s.brier, s.baseBrier)
	if m.Brier < s.baseBrier*shortLiftMargin {
		m.LabelRule = shortLabelRule
		fmt.Println("coefficients (standardized):")
		fmt.Printf("  %-22s %+.4f\n", "(intercept)", m.Intercept)
		for i, f := range m.Features {
			fmt.Printf("  %-22s %+.4f\n", f, m.Weights[i])
		}
		if err := ledger.WriteCalibration(out, m); err != nil {
			fmt.Fprintf(os.Stderr, "learner: write 10s calibration: %v\n", err)
			os.Exit(1)
		}
		s.shipped = true
		fmt.Printf("model shipped: %s\n", out)
	} else {
		s.refusal = fmt.Sprintf("no lift at 10s horizon: brier %.4f vs base %.4f, no model shipped", m.Brier, s.baseBrier)
		fmt.Println(s.refusal)
	}
	return s
}

func correctTotal(m map[string]shortDet) int {
	n := 0
	for _, d := range m {
		n += d.correct
	}
	return n
}

func sortedKeys(m map[string]shortDet) []string {
	ks := make([]string, 0, len(m))
	for k := range m {
		ks = append(ks, k)
	}
	for i := 1; i < len(ks); i++ {
		for j := i; j > 0 && ks[j] < ks[j-1]; j-- {
			ks[j], ks[j-1] = ks[j-1], ks[j]
		}
	}
	return ks
}
