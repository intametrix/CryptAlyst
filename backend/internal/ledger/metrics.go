package ledger

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strings"
	"time"
)

// GroupStats is accuracy over one cohort of predictions.
type GroupStats struct {
	N          int     `json:"n"`
	Correct    int     `json:"correct"`
	Precision  float64 `json:"precision"` // Correct/N; 0 when N == 0
	Expectancy float64 `json:"expectancy_pct"`
	MedMFE     float64 `json:"median_mfe_pct"`
	MedMAE     float64 `json:"median_mae_pct"`
	MedTTF     float64 `json:"median_time_to_mfe_sec"`
}

func (g GroupStats) String() string {
	return fmt.Sprintf("n=%d correct=%d precision=%.1f%% exp=%+.2f%% medMFE=%.2f%% medMAE=%.2f%% medTTF=%.0fs",
		g.N, g.Correct, g.Precision*100, g.Expectancy, g.MedMFE, g.MedMAE, g.MedTTF)
}

// LedgerReport aggregates a ledger file. Precision is always reported per cohort —
// overall, by severity, by direction, by detector — never blended, and the
// unresolved count is published explicitly.
type LedgerReport struct {
	Path             string                `json:"path"`
	Since            time.Duration         `json:"since"`
	Generated        time.Time             `json:"generated"`
	TotalPredictions int                   `json:"total_predictions"`
	Resolved         int                   `json:"resolved"`
	Unresolved       int                   `json:"unresolved"`
	Retracted        int                   `json:"retracted"`
	Overall          GroupStats            `json:"overall"`
	BySeverity       map[string]GroupStats `json:"by_severity"`
	ByDirection      map[string]GroupStats `json:"by_direction"`
	ByDetector       map[string]GroupStats `json:"by_detector"`
}

func (r LedgerReport) String() string {
	var b strings.Builder
	fmt.Fprintf(&b, "Ledger report: %s (since %s, generated %s)\n", r.Path, r.Since, r.Generated.Format(time.RFC3339))
	fmt.Fprintf(&b, "  predictions=%d resolved=%d UNRESOLVED=%d retracted=%d\n", r.TotalPredictions, r.Resolved, r.Unresolved, r.Retracted)
	fmt.Fprintf(&b, "  overall: %s\n", r.Overall)
	keys := func(m map[string]GroupStats) []string {
		ks := make([]string, 0, len(m))
		for k := range m {
			ks = append(ks, k)
		}
		sort.Strings(ks)
		return ks
	}
	for _, k := range keys(r.BySeverity) {
		fmt.Fprintf(&b, "  severity %-9s %s\n", k, r.BySeverity[k])
	}
	for _, k := range keys(r.ByDirection) {
		fmt.Fprintf(&b, "  direction %-6s %s\n", k, r.ByDirection[k])
	}
	for _, k := range keys(r.ByDetector) {
		fmt.Fprintf(&b, "  detector %-14s %s\n", k, r.ByDetector[k])
	}
	return b.String()
}

// resolvedRow is one prediction joined with its (latest) resolution.
type resolvedRow struct {
	pred Prediction
	res  resolutionRecord
}

// Report reads a JSONL ledger file and computes accuracy over the cohort with
// resolutions, honouring `since` as a cutoff on prediction time relative to
// the newest prediction in the file.
func Report(path string, since time.Duration) (LedgerReport, error) {
	rep := LedgerReport{Path: path, Since: since, Generated: time.Now(),
		BySeverity: make(map[string]GroupStats), ByDirection: make(map[string]GroupStats),
		ByDetector: make(map[string]GroupStats)}

	f, err := os.Open(path)
	if err != nil {
		return rep, fmt.Errorf("ledger report: open %s: %w", path, err)
	}
	defer f.Close()

	preds := make(map[string]Prediction)
	var predOrder []string
	resolutions := make(map[string]resolutionRecord)
	retracted := make(map[string]bool)
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 1<<20), 1<<20)
	for sc.Scan() {
		line := sc.Bytes()
		if len(line) == 0 {
			continue
		}
		var head struct {
			Type       string     `json:"type"`
			Prediction Prediction `json:"prediction"`
			ID         string     `json:"id"`
		}
		if err := json.Unmarshal(line, &head); err != nil {
			continue
		}
		switch head.Type {
		case "prediction":
			var pr predictionRecord
			if json.Unmarshal(line, &pr) == nil {
				if _, seen := preds[pr.Prediction.ID]; !seen {
					predOrder = append(predOrder, pr.Prediction.ID)
				}
				preds[pr.Prediction.ID] = pr.Prediction
			}
		case "resolution":
			var rr resolutionRecord
			if json.Unmarshal(line, &rr) == nil {
				resolutions[rr.ID] = rr // last resolution wins
			}
		case "retraction":
			if head.ID != "" {
				retracted[head.ID] = true
			}
		}
	}
	if err := sc.Err(); err != nil {
		return rep, fmt.Errorf("ledger report: scan %s: %w", path, err)
	}

	var maxTS int64
	for _, p := range preds {
		if p.TSMS > maxTS {
			maxTS = p.TSMS
		}
	}
	cutoff := maxTS - since.Milliseconds()

	var rows []resolvedRow
	for _, id := range predOrder {
		if retracted[id] {
			rep.Retracted++
			continue // excluded from every accuracy number, never deleted
		}
		p := preds[id]
		if p.TSMS < cutoff {
			continue
		}
		rep.TotalPredictions++
		if res, ok := resolutions[id]; ok {
			rows = append(rows, resolvedRow{pred: p, res: res})
		}
	}
	rep.Resolved = len(rows)
	rep.Unresolved = rep.TotalPredictions - rep.Resolved

	rep.Overall = summarize(rows)
	sevAcc, dirAcc, detAcc := map[string]*accum{}, map[string]*accum{}, map[string]*accum{}
	for _, row := range rows {
		sev := row.pred.Severity
		dir := "LONG"
		if row.pred.Direction < 0 {
			dir = "SHORT"
		}
		put(sevAcc, sev, row)
		put(dirAcc, dir, row)
		put(detAcc, row.pred.Detector, row)
	}
	rep.BySeverity = finish(sevAcc)
	rep.ByDirection = finish(dirAcc)
	rep.ByDetector = finish(detAcc)
	return rep, nil
}

func put(m map[string]*accum, key string, row resolvedRow) {
	a, ok := m[key]
	if !ok {
		a = &accum{}
		m[key] = a
	}
	a.add(row)
}

func finish(m map[string]*accum) map[string]GroupStats {
	out := make(map[string]GroupStats, len(m))
	for k, a := range m {
		out[k] = a.group()
	}
	return out
}

// outcomeR maps a label to its R-term in percent, as defined by the ledger's
// labelling constants: CORRECT = +TargetPct, WRONG = -StopPct, EXPIRED = MFE
// capped at TargetPct.
func outcomeR(res resolutionRecord) float64 {
	switch res.Label {
	case LabelCorrect:
		if res.TargetPct != 0 {
			return res.TargetPct
		}
		return DefaultTargetPct
	case LabelWrong:
		if res.StopPct != 0 {
			return -res.StopPct
		}
		return -DefaultStopPct
	default: // EXPIRED
		mfe := res.MFEPct
		if t := res.TargetPct; t != 0 && mfe > t {
			mfe = t
		}
		return mfe
	}
}

type accum struct {
	n, correct int
	r          []float64
	mfe        []float64
	mae        []float64
	ttf        []float64
}

func (a *accum) add(row resolvedRow) {
	a.n++
	if row.res.Label == LabelCorrect {
		a.correct++
	}
	a.r = append(a.r, outcomeR(row.res))
	a.mfe = append(a.mfe, row.res.MFEPct)
	a.mae = append(a.mae, row.res.MAEPct)
	a.ttf = append(a.ttf, row.res.TimeToMFESec)
}

func (a *accum) group() GroupStats {
	g := GroupStats{N: a.n, Correct: a.correct,
		Expectancy: mean(a.r), MedMFE: median(a.mfe), MedMAE: median(a.mae), MedTTF: median(a.ttf)}
	if g.N > 0 {
		g.Precision = float64(g.Correct) / float64(g.N)
	}
	return g
}

func mean(xs []float64) float64 {
	if len(xs) == 0 {
		return 0
	}
	s := 0.0
	for _, x := range xs {
		s += x
	}
	return s / float64(len(xs))
}

func summarize(rows []resolvedRow) GroupStats {
	var a accum
	for _, row := range rows {
		a.add(row)
	}
	return a.group()
}

func median(xs []float64) float64 {
	if len(xs) == 0 {
		return 0
	}
	s := append([]float64(nil), xs...)
	sort.Float64s(s)
	m := len(s) / 2
	if len(s)%2 == 1 {
		return s[m]
	}
	return (s[m-1] + s[m]) / 2
}
