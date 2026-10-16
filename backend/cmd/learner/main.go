// Command learner is an offline accuracy-reporting and gate-tuning tool. It
// reads a mixed ledger file that may contain alert records
// ({"type":"alert","event_id":...}) and/or ledger package records
// ({"type":"prediction"|"path"|"resolution"}), joins them into predictions,
// and reports hit rate and expectancy per severity, direction and overall.
// With 30+ resolved predictions and -param flags it additionally runs a
// threshold coordinate search. It never touches the network and exits 0
// unless the ledger is unreadable.
package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"
	"time"

	"pumpdump/backend/internal/ledger"
)

// runCalibration fits the logistic model on resolved rows. Fewer than
// ledger.MinCalibrationN resolved rows is a refusal: exact message, no file,
// success exit. Otherwise the fit summary is printed (in-sample metrics
// labelled as such) and the calibration file is written atomically,
// replacing any existing file wholesale.
func runCalibration(resolvedRows []*prediction, out string) {
	if len(resolvedRows) < ledger.MinCalibrationN {
		fmt.Printf("insufficient sample: %d resolved, need %d for calibration\n",
			len(resolvedRows), ledger.MinCalibrationN)
		return
	}
	rows := make([]ledger.FeatureRow, 0, len(resolvedRows))
	labels := make([]int, 0, len(resolvedRows))
	for _, p := range resolvedRows {
		// Label mapping (documented in ledger/calibrate.go): CORRECT=1,
		// WRONG=0, EXPIRED=0.
		label := 0
		if p.label == "CORRECT" {
			label = 1
		}
		rows = append(rows, ledger.FeatureRow{ID: p.id, Features: p.features})
		labels = append(labels, label)
	}
	m, err := ledger.FitLogistic(rows, labels)
	if err != nil {
		fmt.Fprintf(os.Stderr, "learner: calibrate: %v\n", err)
		os.Exit(1)
	}
	fmt.Printf("\ncalibration: n=%d base_rate=%.3f (IN-SAMPLE, not holdout)\n", m.N, m.BaseRate)
	fmt.Printf("brier=%.4f log_loss=%.4f\n", m.Brier, m.LogLoss)
	fmt.Println("coefficients (standardized):")
	fmt.Printf("  %-22s %10s\n", "(intercept)", fmt.Sprintf("%+.4f", m.Intercept))
	for i, f := range m.Features {
		fmt.Printf("  %-22s %+.4f\n", f, m.Weights[i])
	}
	fmt.Println("reliability (10 buckets, in-sample):")
	fmt.Printf("  %6s %6s %10s %10s %6s\n", "lo", "hi", "pred", "obs", "n")
	for _, b := range m.Reliability {
		fmt.Printf("  %.1f  %.1f  %10.3f %10.3f %6d\n", b.Lo, b.Hi, b.Predicted, b.Observed, b.Count)
	}
	if err := ledger.WriteCalibration(out, m); err != nil {
		fmt.Fprintf(os.Stderr, "learner: write calibration: %v\n", err)
		os.Exit(1)
	}
	fmt.Printf("calibration written: %s\n", out)
}

const minSamples = 30

// directionFor maps a kind to a trade direction. Unknown kinds are
// unrecorded (0).
func directionFor(kind string) int {
	switch kind {
	case "PUMP", "REVERSAL_PUMP":
		return 1
	case "DUMP", "REVERSAL_DUMP":
		return -1
	}
	return 0
}

// alertRecord mirrors the AlertBuffer's JSON lines.
type alertRecord struct {
	EventID  string         `json:"event_id"`
	TSMS     int64          `json:"ts_ms"`
	Detector string         `json:"detector"`
	Symbol   string         `json:"symbol"`
	Kind     string         `json:"kind"`
	Severity string         `json:"severity"`
	Score    float64        `json:"score"`
	Price    float64        `json:"price"`
	Metrics  map[string]any `json:"metrics"`
	// Alias some flow-onset fields in case a variant payload carries them.
	ID string `json:"id"`
}

// ledgerPrediction mirrors the ledger package's prediction line.
type ledgerPrediction struct {
	Prediction struct {
		ID         string             `json:"id"`
		Detector   string             `json:"detector"`
		Kind       string             `json:"kind"`
		Symbol     string             `json:"symbol"`
		Direction  int                `json:"direction"`
		Severity   string             `json:"severity"`
		Score      float64            `json:"score"`
		EntryPrice float64            `json:"price"`
		TSMS       int64              `json:"ts_ms"`
		Features   map[string]float64 `json:"features"`
	} `json:"prediction"`
}

// ledgerResolution mirrors the ledger package's resolution line.
type ledgerResolution struct {
	ID        string  `json:"id"`
	Label     string  `json:"label"`
	MFEPct    float64 `json:"mfe_pct"`
	MAEPct    float64 `json:"mae_pct"`
	TargetPct float64 `json:"target_pct"`
	StopPct   float64 `json:"stop_pct"`
}

// prediction is the joined in-memory view of one event.
type prediction struct {
	id        string
	detector  string
	symbol    string
	direction int
	severity  string
	score     float64
	price     float64
	tsms      int64
	features  map[string]float64
	// resolution, when present.
	label  string
	mfe    float64
	mae    float64
	target float64
	stop   float64
}

func (p prediction) resolved() bool { return p.label != "" }

// expectancy is the R-term in percent: CORRECT -> +target, WRONG -> -stop,
// EXPIRED -> mfe capped at +target.
func (p prediction) expectancy() float64 {
	switch p.label {
	case "CORRECT":
		return p.target
	case "WRONG":
		return -p.stop
	default: // EXPIRED
		if p.mfe > p.target {
			return p.target
		}
		return p.mfe
	}
}

// store accumulates predictions, deduped by id with a symbol+time proximity
// fallback (alert event_id and ledger signal ID can differ for the same
// event).
type store struct {
	byID      map[string]*prediction
	order     []*prediction
	short     []*shortLabel
	retracted map[string]bool
}

func newStore() *store {
	return &store{byID: map[string]*prediction{}, retracted: map[string]bool{}}
}

func (s *store) findByProximity(symbol string, ts int64) *prediction {
	for _, p := range s.order {
		if p.symbol == symbol && p.tsms-ts <= 2000 && p.tsms-ts >= -2000 {
			return p
		}
	}
	return nil
}

func (s *store) register(p *prediction) {
	if p.id != "" {
		if _, dup := s.byID[p.id]; dup {
			return
		}
	}
	if existing := s.findByProximity(p.symbol, p.tsms); existing != nil {
		if existing.id == "" {
			existing.id = p.id
		}
		if s.byID != nil && p.id != "" {
			s.byID[p.id] = existing
		}
		for k, v := range p.features {
			if _, ok := existing.features[k]; !ok {
				existing.features[k] = v
			}
		}
		return
	}
	s.order = append(s.order, p)
	if p.id != "" {
		s.byID[p.id] = p
	}
}

func (s *store) attachResolution(id string, r ledgerResolution) {
	if p, ok := s.byID[id]; ok {
		p.label, p.mfe, p.mae = r.Label, r.MFEPct, r.MAEPct
		if r.TargetPct != 0 {
			p.target = r.TargetPct
		}
		if r.StopPct != 0 {
			p.stop = r.StopPct
		}
	}
}

func toFeatures(metrics map[string]any) map[string]float64 {
	feat := make(map[string]float64, len(metrics))
	for k, v := range metrics {
		switch n := v.(type) {
		case float64:
			feat[k] = n
		case float32:
			feat[k] = float64(n)
		case int:
			feat[k] = float64(n)
		case int64:
			feat[k] = float64(n)
		case bool:
			if n {
				feat[k] = 1
			} else {
				feat[k] = 0
			}
		}
	}
	return feat
}

// load reads a mixed alert/ledger file into deduped predictions.
func load(path string, target, stop float64) (*store, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	st := newStore()
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 1<<20), 1<<20)
	lineNo := 0
	for sc.Scan() {
		lineNo++
		line := sc.Bytes()
		if len(line) == 0 {
			continue
		}
		var head struct {
			Type string `json:"type"`
			ID   string `json:"id"`
		}
		if err := json.Unmarshal(line, &head); err != nil {
			return nil, fmt.Errorf("line %d: unparseable: %w", lineNo, err)
		}
		switch head.Type {
		case "prediction":
			var lp ledgerPrediction
			if err := json.Unmarshal(line, &lp); err != nil {
				return nil, fmt.Errorf("line %d: %w", lineNo, err)
			}
			d := lp.Prediction.Direction
			if d == 0 {
				d = directionFor(lp.Prediction.Kind)
			}
			if d == 0 {
				continue
			}
			st.register(&prediction{
				id: lp.Prediction.ID, detector: lp.Prediction.Detector,
				symbol: lp.Prediction.Symbol, direction: d,
				severity: lp.Prediction.Severity, score: lp.Prediction.Score,
				price: lp.Prediction.EntryPrice, tsms: lp.Prediction.TSMS,
				features: lp.Prediction.Features, target: target, stop: stop,
			})
		case "resolution":
			var lr ledgerResolution
			if err := json.Unmarshal(line, &lr); err != nil {
				return nil, fmt.Errorf("line %d: %w", lineNo, err)
			}
			st.attachResolution(lr.ID, lr)
		case "retraction":
			if head.ID != "" {
				st.retracted[head.ID] = true
			}
		case "label_10s":
			var sl struct {
				ID        string  `json:"id"`
				Label     string  `json:"label"`
				BandPct   float64 `json:"band_pct"`
				Direction int     `json:"direction"`
				Entry     float64 `json:"entry"`
				Price     float64 `json:"price"`
				MFEPct    float64 `json:"mfe_pct"`
				MAEPct    float64 `json:"mae_pct"`
			}
			if err := json.Unmarshal(line, &sl); err != nil {
				return nil, fmt.Errorf("line %d: %w", lineNo, err)
			}
			st.short = append(st.short, &shortLabel{
				id: sl.ID, label: sl.Label, band: sl.BandPct,
				direction: sl.Direction, entry: sl.Entry, price: sl.Price,
				mfe: sl.MFEPct, mae: sl.MAEPct,
				hasExcursions: sl.MFEPct != 0 || sl.MAEPct != 0,
			})
		case "", "alert":
			var a alertRecord
			if err := json.Unmarshal(line, &a); err != nil {
				return nil, fmt.Errorf("line %d: %w", lineNo, err)
			}
			d := directionFor(a.Kind)
			if d == 0 || a.Symbol == "" {
				continue
			}
			id := a.EventID
			if id == "" {
				id = a.ID
			}
			st.register(&prediction{
				id: id, detector: a.Detector, symbol: a.Symbol, direction: d,
				severity: a.Severity, score: a.Score, price: a.Price,
				tsms: a.TSMS, features: toFeatures(a.Metrics),
				target: target, stop: stop,
			})
		default:
			// path and unknown record types carry nothing to register.
		}
	}
	if err := sc.Err(); err != nil {
		return nil, err
	}
	// Retractions may appear after the retracted records; purge them so every
	// report path (15min, 10s, calibration) excludes contaminated ids. The
	// records stay in the file — this is exclusion, not deletion.
	if len(st.retracted) > 0 {
		kept := st.order[:0]
		for _, p := range st.order {
			if st.retracted[p.id] {
				delete(st.byID, p.id)
				continue
			}
			kept = append(kept, p)
		}
		st.order = kept
		labels := st.short[:0]
		for _, l := range st.short {
			if !st.retracted[l.id] {
				labels = append(labels, l)
			}
		}
		st.short = labels
	}
	return st, nil
}

// cohort is one reporting group.
type cohort struct {
	n, resolved, hits int
	exp               float64
}

func (c cohort) String() string {
	if c.resolved == 0 {
		return fmt.Sprintf("n=%d resolved=0", c.n)
	}
	return fmt.Sprintf("n=%d resolved=%d hit=%.1f%% exp=%+.2f%%",
		c.n, c.resolved, 100*float64(c.hits)/float64(c.resolved), c.exp/float64(c.resolved))
}

func accumulate(ps []*prediction, keep func(*prediction) bool) cohort {
	var c cohort
	for _, p := range ps {
		if keep != nil && !keep(p) {
			continue
		}
		c.n++
		if p.resolved() {
			c.resolved++
			c.exp += p.expectancy()
			if p.label == "CORRECT" {
				c.hits++
			}
		}
	}
	return c
}

func main() {
	ledgerPath := flag.String("ledger", "", "path to ledger/alerts JSONL file (required)")
	since := flag.Duration("since", 720*time.Hour, "only use predictions newer than this before the newest")
	target := flag.Float64("target", 1.5, "win threshold %% for expectancy")
	stop := flag.Float64("stop", 1.0, "fail threshold %% for expectancy")
	holdout := flag.Float64("holdout", 0.25, "unused: retained for flag compatibility")
	calibrate := flag.Bool("calibrate", false, "fit logistic calibration and write -out")
	calOut := flag.String("out", "", "calibration output path (default calibration.json, or calibration_10s.json with -short)")
	shortMode := flag.Bool("short", false, "short-horizon (10s) report and fit from label_10s records")
	auditMode := flag.Bool("audit", false, "scan for contaminated resolutions; write a copy with retractions to -out")
	feePct := flag.Float64("fee_pct", 0.1, "taker fee per side, in percent, for v2 net expectancy")
	var params paramFlags
	flag.Var(&params, "param", "gate parameter name=min:max:step (repeatable)")
	flag.Parse()
	_ = holdout

	if *ledgerPath == "" {
		fmt.Fprintln(os.Stderr, "-ledger is required")
		os.Exit(1)
	}
	if *auditMode {
		outPath := *calOut
		if outPath == "" {
			outPath = "ledger_retracted.jsonl"
		}
		res, impossible, contra, dups, nonmono, retracted, err := runAudit(*ledgerPath, outPath)
		if err != nil {
			fmt.Fprintf(os.Stderr, "learner: audit: %v\n", err)
			os.Exit(1)
		}
		fmt.Printf("audit: %s\n", *ledgerPath)
		fmt.Printf("  resolutions=%d impossible=%d contradictions=%d duplicate_resolutions=%d\n",
			res, impossible, contra, dups)
		fmt.Printf("  non_monotone_path_records=%d (restart artifact, informational)\n", nonmono)
		fmt.Printf("  retracted=%d -> %s\n", retracted, outPath)
		return
	}
	st, err := load(*ledgerPath, *target, *stop)
	if err != nil {
		fmt.Fprintf(os.Stderr, "learner: %v\n", err)
		os.Exit(1)
	}

	all := st.order
	if len(all) > 0 && *since > 0 {
		newest := all[0].tsms
		for _, p := range all {
			if p.tsms > newest {
				newest = p.tsms
			}
		}
		cutoff := newest - since.Milliseconds()
		kept := all[:0]
		for _, p := range all {
			if p.tsms >= cutoff {
				kept = append(kept, p)
			}
		}
		all = kept
	}

	unresolved := 0
	for _, p := range all {
		if !p.resolved() {
			unresolved++
		}
	}
	resolved := len(all) - unresolved

	fmt.Printf("ledger: %s\n", *ledgerPath)
	fmt.Printf("registered=%d  resolved=%d  UNRESOLVED=%d\n", len(all), resolved, unresolved)

	if resolved == 0 && !*shortMode {
		fmt.Printf("insufficient sample: %d resolved, need %d\n", resolved, minSamples)
		return
	}

	if resolved > 0 {
		fmt.Printf("overall:            %s\n", accumulate(all, nil))
		for _, sev := range []string{"CRITICAL", "ALERT", "WATCH"} {
			fmt.Printf("severity %-9s %s\n", sev+":", accumulate(all, func(p *prediction) bool { return p.severity == sev }))
		}
		fmt.Printf("direction LONG:     %s\n", accumulate(all, func(p *prediction) bool { return p.direction > 0 }))
		fmt.Printf("direction SHORT:    %s\n", accumulate(all, func(p *prediction) bool { return p.direction < 0 }))
		printV2_15min(all, *feePct)
	}

	out := *calOut
	if out == "" {
		if *shortMode {
			out = "calibration_10s.json"
		} else {
			out = "calibration.json"
		}
	}

	if *calibrate {
		var resolvedRows []*prediction
		for _, p := range all {
			if p.resolved() {
				resolvedRows = append(resolvedRows, p)
			}
		}
		runCalibration(resolvedRows, out)
		return
	}

	if *shortMode {
		computeShort(st, out, *feePct)
		return
	}

	if resolved < minSamples {
		fmt.Printf("insufficient sample: %d resolved, need %d\n", resolved, minSamples)
		return
	}

	// Champion vs challenger: threshold coordinate search over the resolved
	// rows (chronological split is unnecessary for a single summary gate
	// sweep; the report above is the acceptance bar, this is a bonus).
	var resolvedRows []*prediction
	for _, p := range all {
		if p.resolved() {
			resolvedRows = append(resolvedRows, p)
		}
	}
	if len(params) == 0 {
		return
	}
	champExp := accumulate(resolvedRows, nil).exp / float64(resolved)
	fmt.Printf("\nchampion (no gate): expectancy=%+.3f%% n=%d\n", champExp, resolved)
	bestVals := make([]float64, len(params))
	bestExp := champExp
	for pass := 0; pass < 2; pass++ {
		for i, p := range params {
			expAt := func(v float64) (float64, int) {
				sub := accumulate(resolvedRows, func(r *prediction) bool {
					f, ok := r.features[p.name]
					return ok && f >= v
				})
				if sub.resolved == 0 {
					return 0, 0
				}
				return sub.exp / float64(sub.resolved), sub.resolved
			}
			bestV := bestVals[i]
			bestE, _ := expAt(bestV)
			for v := p.min; v <= p.max+1e-9; v += p.step {
				e, n := expAt(v)
				if n < minSamples {
					continue
				}
				if e > bestE {
					bestV, bestE = v, e
				}
			}
			bestVals[i] = bestV
			if bestE > bestExp {
				bestExp = bestE
			}
		}
	}
	fmt.Println("challenger sweep (train = all resolved rows):")
	for i, p := range params {
		v := 0.0
		if i < len(bestVals) {
			v = bestVals[i]
		}
		sub := accumulate(resolvedRows, func(r *prediction) bool {
			f, ok := r.features[p.name]
			return ok && f >= v
		})
		e := 0.0
		if sub.resolved > 0 {
			e = sub.exp / float64(sub.resolved)
		}
		fmt.Printf("  %-20s gate>=%-8.3g exp=%+.3f%% n=%d\n", p.name, v, e, sub.resolved)
	}
	if bestExp > champExp {
		fmt.Printf("DECISION: PROMOTE challenger (expectancy %+.3f%% > %+.3f%%)\n", bestExp, champExp)
	} else {
		fmt.Printf("DECISION: KEEP CHAMPION (challenger %+.3f%% did not beat %+.3f%%)\n", bestExp, champExp)
	}
}

type gateParam struct {
	name     string
	min, max float64
	step     float64
}

type paramFlags []gateParam

func (p *paramFlags) String() string { return fmt.Sprint(*p) }

func (p *paramFlags) Set(s string) error {
	parts := strings.Split(s, "=")
	if len(parts) != 2 {
		return fmt.Errorf("param %q: want name=min:max:step", s)
	}
	nums := strings.Split(parts[1], ":")
	if len(nums) != 3 {
		return fmt.Errorf("param %q: want name=min:max:step", s)
	}
	var f [3]float64
	for i, n := range nums {
		if _, err := fmt.Sscanf(n, "%g", &f[i]); err != nil {
			return fmt.Errorf("param %q: bad number %q", s, n)
		}
	}
	if f[2] <= 0 {
		return fmt.Errorf("param %q: step must be > 0", s)
	}
	*p = append(*p, gateParam{name: parts[0], min: f[0], max: f[1], step: f[2]})
	return nil
}
