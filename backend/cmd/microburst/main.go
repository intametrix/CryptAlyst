// Command microburst trains the horizon-outcome models for the micro-burst
// detector. It reads the live ledger produced by the flow engine + fastsample,
// joins each prediction (its flow features) to the symmetric barrier labels
// recorded at 10/30/60 seconds, and fits, per horizon, two binary logistic
// models — P(upside-first) and P(reversal-first) — with a chronological
// walk-forward split.
//
// It reuses ledger.FitLogistic exactly as cmd/learner does, never touches the
// network, and reports every number with its label rule and sample count so a
// probability is never presented as a certainty.
package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"math"
	"os"
	"sort"
	"strconv"
	"strings"

	"pumpdump/backend/internal/ledger"
)

// pred is the subset of a ledger prediction record this command needs.
type pred struct {
	ID        string             `json:"id"`
	Symbol    string             `json:"symbol"`
	Direction int                `json:"direction"`
	TSMS      int64              `json:"ts_ms"`
	Features  map[string]float64 `json:"features"`
}

type record struct {
	Type       string `json:"type"`
	Prediction pred   `json:"prediction"`
	ID         string `json:"id"`
	Label      string `json:"label"`
}

// row is one joined training example for one horizon.
type row struct {
	symbol string
	tsms   int64
	feat   map[string]float64
	up     int // 1 if upside barrier hit first
	rev    int // 1 if reversal barrier hit first
}

func main() {
	ledgerPath := flag.String("ledger", "", "path to ledger/alerts JSONL file (required)")
	horizonsFlag := flag.String("horizons", "10,30,60", "comma-separated horizons in seconds")
	trainFrac := flag.Float64("train-frac", 0.7, "chronological train fraction")
	minEvents := flag.Int("min-events", 100, "refuse to fit below this many labelled events")
	outDir := flag.String("out", "microburst_out", "output directory")
	flag.Parse()

	if *ledgerPath == "" {
		fmt.Fprintln(os.Stderr, "-ledger is required")
		os.Exit(1)
	}

	horizons := splitInts(*horizonsFlag)
	preds, labels, err := readLedger(*ledgerPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "microburst: %v\n", err)
		os.Exit(1)
	}

	if len(preds) == 0 {
		fmt.Println("no predictions found in ledger")
		return
	}

	if err := os.MkdirAll(*outDir, 0o755); err != nil {
		fmt.Fprintf(os.Stderr, "microburst: mkdir: %v\n", err)
		os.Exit(1)
	}

	fmt.Printf("ledger: %s  predictions=%d\n", *ledgerPath, len(preds))
	for _, h := range horizons {
		rows := join(preds, labels, h)
		if len(rows) < *minEvents {
			fmt.Printf("\nhorizon %ds: insufficient sample (%d events, need %d)\n", h, len(rows), *minEvents)
			continue
		}
		train, holdout := split(rows, *trainFrac)
		if len(train) < *minEvents || len(holdout) < *minEvents {
			fmt.Printf("\nhorizon %ds: insufficient split (train=%d holdout=%d)\n", h, len(train), len(holdout))
			continue
		}
		fmt.Printf("\n======== horizon %ds: events=%d train=%d holdout=%d ========\n",
			h, len(rows), len(train), len(holdout))
		run(train, holdout, h, *outDir)
	}
}

// readLedger parses predictions and barrier labels from the ledger.
func readLedger(path string) (map[string]pred, map[int][]record, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, nil, err
	}
	defer f.Close()

	preds := map[string]pred{}
	labels := map[int][]record{}
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 1<<20), 1<<20)
	for sc.Scan() {
		line := sc.Bytes()
		if len(line) == 0 || line[0] != '{' {
			continue
		}
		var r record
		if err := json.Unmarshal(line, &r); err != nil {
			continue
		}
		switch {
		case r.Type == "prediction" && r.Prediction.ID != "" && r.Prediction.Features != nil:
			preds[r.Prediction.ID] = r.Prediction
		case strings.HasPrefix(r.Type, "label_barrier_"):
			secs := barrierSeconds(r.Type)
			if secs > 0 && r.ID != "" && r.Label != "" {
				labels[secs] = append(labels[secs], record{ID: r.ID, Label: r.Label})
			}
		}
	}
	if err := sc.Err(); err != nil {
		return nil, nil, err
	}
	return preds, labels, nil
}

// barrierSeconds extracts the seconds from a "label_barrier_Ns" type string.
func barrierSeconds(t string) int {
	s := strings.TrimSuffix(strings.TrimPrefix(t, "label_barrier_"), "s")
	n, err := strconv.Atoi(s)
	if err != nil {
		return 0
	}
	return n
}

// join joins each prediction to its label at one horizon. A prediction with no
// barrier label is NEITHER (no barrier was hit within the window). Labels are
// keyed by id; the label map already carries only UP/REVERSAL.
func join(preds map[string]pred, labels map[int][]record, horizon int) []row {
	byID := map[string]string{}
	for _, l := range labels[horizon] {
		byID[l.ID] = l.Label
	}
	var rows []row
	for id, p := range preds {
		up, rev := 0, 0
		switch byID[id] {
		case "UP":
			up = 1
		case "REVERSAL":
			rev = 1
		}
		rows = append(rows, row{symbol: p.Symbol, tsms: p.TSMS, feat: p.Features, up: up, rev: rev})
	}
	sort.Slice(rows, func(a, b int) bool { return rows[a].tsms < rows[b].tsms })
	return rows
}

// split is a chronological split with a one-horizon gap in time so no train
// label can reach into the holdout.
func split(rows []row, frac float64) (train, holdout []row) {
	n := int(float64(len(rows)) * frac)
	train = rows[:n]
	holdout = rows[n:]
	return train, holdout
}

// run fits P(up) and P(reversal) on the train set and evaluates on holdout.
func run(train, holdout []row, horizon int, outDir string) {
	upModel, _ := fit(train, func(r row) int { return r.up })
	revModel, _ := fit(train, func(r row) int { return r.rev })

	fmt.Printf("  base rates: P(up)=%.3f P(reversal)=%.3f P(neither)=%.3f\n",
		rate(holdout, func(r row) int { return r.up }),
		rate(holdout, func(r row) int { return r.rev }),
		1-rate(holdout, func(r row) int { return r.up })-rate(holdout, func(r row) int { return r.rev }))

	fmt.Printf("  in-sample:  up brier=%.4f ll=%.4f   reversal brier=%.4f ll=%.4f\n",
		upModel.Brier, upModel.LogLoss, revModel.Brier, revModel.LogLoss)

	upHL := eval(holdout, upModel, func(r row) int { return r.up })
	revHL := eval(holdout, revModel, func(r row) int { return r.rev })
	fmt.Printf("  holdout:    up brier=%.4f ll=%.4f   reversal brier=%.4f ll=%.4f\n",
		upHL.brier, upHL.logLoss, revHL.brier, revHL.logLoss)

	pr := precisionRecall(holdout, upModel, func(r row) int { return r.up })
	fmt.Printf("  up-continuation (P>=0.50): precision=%.3f recall=%.3f (tp=%d fp=%d fn=%d)\n",
		pr.precision, pr.recall, pr.tp, pr.fp, pr.fn)

	falsePer := falseAlertsPerCoinHour(holdout, upModel)
	fmt.Printf("  false up-alerts per coin-hour: %.2f\n", falsePer)

	writeModel(outDir, horizon, upModel, revModel, len(train), len(holdout))
}

func fit(rows []row, labeler func(row) int) (ledger.Model, error) {
	featRows := make([]ledger.FeatureRow, 0, len(rows))
	labels := make([]int, 0, len(rows))
	for _, r := range rows {
		featRows = append(featRows, ledger.FeatureRow{ID: r.symbol, Features: r.feat})
		labels = append(labels, labeler(r))
	}
	return ledger.FitLogistic(featRows, labels)
}

type pair struct{ brier, logLoss float64 }

func eval(rows []row, m ledger.Model, labeler func(row) int) pair {
	var brier, ll float64
	for _, r := range rows {
		p, ok := m.Predict(r.feat)
		if !ok {
			p = m.BaseRate
		}
		y := labeler(r)
		brier += (p - float64(y)) * (p - float64(y))
		p = clamp(p, 1e-6, 1-1e-6)
		if y == 1 {
			ll -= math.Log(p)
		} else {
			ll -= math.Log(1 - p)
		}
	}
	n := float64(len(rows))
	if n == 0 {
		return pair{}
	}
	return pair{brier: brier / n, logLoss: ll / n}
}

func rate(rows []row, labeler func(row) int) float64 {
	if len(rows) == 0 {
		return 0
	}
	var s int
	for _, r := range rows {
		s += labeler(r)
	}
	return float64(s) / float64(len(rows))
}

type pr struct {
	precision, recall float64
	tp, fp, fn        int
}

func precisionRecall(rows []row, m ledger.Model, labeler func(row) int) pr {
	var tp, fp, fn int
	for _, r := range rows {
		p, ok := m.Predict(r.feat)
		if !ok {
			p = m.BaseRate
		}
		predicted := p >= 0.5
		actual := labeler(r) == 1
		switch {
		case predicted && actual:
			tp++
		case predicted && !actual:
			fp++
		case !predicted && actual:
			fn++
		}
	}
	prec, rec := 0.0, 0.0
	if tp+fp > 0 {
		prec = float64(tp) / float64(tp+fp)
	}
	if tp+fn > 0 {
		rec = float64(tp) / float64(tp+fn)
	}
	return pr{prec, rec, tp, fp, fn}
}

// falseAlertsPerCoinHour estimates false up-alerts per coin per hour from the
// holdout: (fp / coins) / hours_spanned.
func falseAlertsPerCoinHour(rows []row, m ledger.Model) float64 {
	if len(rows) == 0 {
		return 0
	}
	var fp int
	coins := map[string]bool{}
	minTS, maxTS := int64(1<<62), int64(0)
	for _, r := range rows {
		p, ok := m.Predict(r.feat)
		if !ok {
			p = m.BaseRate
		}
		if p >= 0.5 && r.up != 1 {
			fp++
		}
		coins[r.symbol] = true
		if r.tsms < minTS {
			minTS = r.tsms
		}
		if r.tsms > maxTS {
			maxTS = r.tsms
		}
	}
	if len(coins) == 0 || maxTS <= minTS {
		return 0
	}
	hours := float64(maxTS-minTS) / 3.6e6
	if hours <= 0 {
		return 0
	}
	return float64(fp) / float64(len(coins)) / hours
}

type horizonModel struct {
	Type     string       `json:"type"`
	Version  int          `json:"version"`
	HorizonS int          `json:"horizon_s"`
	TrainN   int          `json:"train_n"`
	HoldoutN int          `json:"holdout_n"`
	Label    string       `json:"label_rule"`
	Up       ledger.Model `json:"up"`
	Reversal ledger.Model `json:"reversal"`
}

func writeModel(dir string, horizon int, up, rev ledger.Model, trainN, holdoutN int) {
	hm := horizonModel{
		Type:     "microburst_calibration",
		Version:  1,
		HorizonS: horizon,
		TrainN:   trainN,
		HoldoutN: holdoutN,
		Label:    "UP=+band first, REVERSAL=-band first, NEITHER otherwise (symmetric barrier)",
		Up:       up,
		Reversal: rev,
	}
	path := fmt.Sprintf("%s/microburst_%ds.json", dir, horizon)
	data, _ := json.MarshalIndent(hm, "", "  ")
	if err := os.WriteFile(path, data, 0o644); err != nil {
		fmt.Fprintf(os.Stderr, "microburst: write: %v\n", err)
		return
	}
	fmt.Printf("  model written: %s\n", path)
}

func splitInts(s string) []int {
	var out []int
	for _, p := range strings.Split(s, ",") {
		p = strings.TrimSpace(p)
		if p == "" {
			continue
		}
		n, err := strconv.Atoi(p)
		if err == nil && n > 0 {
			out = append(out, n)
		}
	}
	return out
}

func clamp(v, lo, hi float64) float64 {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}
