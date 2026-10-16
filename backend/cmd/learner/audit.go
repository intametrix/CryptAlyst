package main

// Audit mode: scan a ledger for contaminated resolutions and emit a copy
// with retraction records appended, so DeepSeek can apply the retractions
// deliberately. The live ledger file is never modified.

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"sort"
	"time"
)

const (
	// auditImpossiblePct flags excursions beyond 20% in one tracked window —
	// far outside anything a 15min pump/dump track should produce.
	auditImpossiblePct = 20.0
)

type auditFinding struct {
	id     string
	reason string
}

// runAudit scans inPath and writes outPath = original content plus one
// {"type":"retraction"} line per contaminated id. It returns the scan
// counts for the report.
func runAudit(inPath, outPath string) (resolutions, impossible, contradictions, duplicates, nonmonotone, retracted int, err error) {
	f, err := os.Open(inPath)
	if err != nil {
		return 0, 0, 0, 0, 0, 0, err
	}
	defer f.Close()

	findings := map[string]string{}
	resCount := 0
	resPerID := map[string]int{}
	type resRec struct {
		label        string
		mfe, mae     float64
		target, stop float64
	}
	latest := map[string]resRec{}
	pathMaxMFE := map[string]float64{}
	pathMaxMAE := map[string]float64{}

	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 1<<20), 1<<20)
	for sc.Scan() {
		line := sc.Bytes()
		if len(line) == 0 {
			continue
		}
		var head struct {
			Type string `json:"type"`
			ID   string `json:"id"`
		}
		if json.Unmarshal(line, &head) != nil {
			continue
		}
		switch head.Type {
		case "resolution":
			resCount++
			resPerID[head.ID]++
			var rr struct {
				Label     string  `json:"label"`
				MFEPct    float64 `json:"mfe_pct"`
				MAEPct    float64 `json:"mae_pct"`
				TargetPct float64 `json:"target_pct"`
				StopPct   float64 `json:"stop_pct"`
			}
			if json.Unmarshal(line, &rr) != nil {
				continue
			}
			latest[head.ID] = resRec{rr.Label, rr.MFEPct, rr.MAEPct, rr.TargetPct, rr.StopPct}
		case "path":
			var pr struct {
				ID     string  `json:"id"`
				MFEPct float64 `json:"mfe_pct"`
				MAEPct float64 `json:"mae_pct"`
			}
			if json.Unmarshal(line, &pr) != nil || pr.ID == "" {
				continue
			}
			if pr.MFEPct < pathMaxMFE[pr.ID]-1e-9 || pr.MAEPct < pathMaxMAE[pr.ID]-1e-9 {
				// A running extreme decreased: pre-fix restart semantics
				// reset MFE/MAE to zero. Informational only — not retracted.
				nonmonotone++
			}
			if pr.MFEPct > pathMaxMFE[pr.ID] {
				pathMaxMFE[pr.ID] = pr.MFEPct
			}
			if pr.MAEPct > pathMaxMAE[pr.ID] {
				pathMaxMAE[pr.ID] = pr.MAEPct
			}
		}
	}
	if err := sc.Err(); err != nil {
		return 0, 0, 0, 0, 0, 0, err
	}

	ids := make([]string, 0, len(latest))
	for id := range latest {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	for _, id := range ids {
		r := latest[id]
		target, stop := r.target, r.stop
		if target == 0 {
			target = 1.5
		}
		if stop == 0 {
			stop = 1.0
		}
		switch {
		case abs(r.mfe) > auditImpossiblePct || abs(r.mae) > auditImpossiblePct:
			impossible++
			findings[id] = fmt.Sprintf("impossible excursion mfe=%.2f%% mae=%.2f%%", r.mfe, r.mae)
		case r.label == "EXPIRED" && r.mae >= stop:
			contradictions++
			findings[id] = fmt.Sprintf("EXPIRED with mae %.2f%% >= stop %.2f%%", r.mae, stop)
		case r.label == "WRONG" && r.mfe >= target:
			contradictions++
			findings[id] = fmt.Sprintf("WRONG with mfe %.2f%% >= target %.2f%%", r.mfe, target)
		case r.label == "EXPIRED" && r.mfe >= target:
			contradictions++
			findings[id] = fmt.Sprintf("EXPIRED with mfe %.2f%% >= target %.2f%%", r.mfe, target)
		}
		if resPerID[id] > 1 {
			duplicates++
			if _, ok := findings[id]; !ok {
				findings[id] = fmt.Sprintf("duplicate resolutions: %d", resPerID[id])
			}
		}
	}

	// Write the copy: original bytes + retraction lines.
	src, err := os.Open(inPath)
	if err != nil {
		return 0, 0, 0, 0, 0, 0, err
	}
	defer src.Close()
	dst, err := os.Create(outPath)
	if err != nil {
		return 0, 0, 0, 0, 0, 0, err
	}
	w := bufio.NewWriter(dst)
	if _, err := io.Copy(w, src); err != nil {
		dst.Close()
		return 0, 0, 0, 0, 0, 0, err
	}
	retractIDs := make([]string, 0, len(findings))
	for id := range findings {
		retractIDs = append(retractIDs, id)
	}
	sort.Strings(retractIDs)
	for _, id := range retractIDs {
		rec, _ := json.Marshal(map[string]any{
			"type":   "retraction",
			"id":     id,
			"reason": findings[id],
			"ts_ms":  time.Now().UnixMilli(),
		})
		w.Write(append(rec, '\n'))
	}
	if err := w.Flush(); err != nil {
		dst.Close()
		return 0, 0, 0, 0, 0, 0, err
	}
	if err := dst.Close(); err != nil {
		return 0, 0, 0, 0, 0, 0, err
	}
	return resCount, impossible, contradictions, duplicates, nonmonotone, len(findings), nil
}

func abs(v float64) float64 {
	if v < 0 {
		return -v
	}
	return v
}
