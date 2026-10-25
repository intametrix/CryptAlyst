// Command onset builds and trains the two one-minute detector models from
// raw one-second klines:
//
//	onset        — P(an upward burst begins in the next 60s)
//	continuation — P(up-first) vs P(reversal-first) over 10/30/60s
//
// It aggregates the 1s bars into per-second feature buckets (using only data
// received by then), derives the volatility-scaled barriers, labels each
// second from the FUTURE bars, and walks forward chronologically so a train
// label can never reach into the holdout. It reuses ledger.FitLogistic and
// never trades.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"math"
	"os"
	"sort"
	"strconv"
	"strings"

	"pumpdump/backend/internal/binance"
	"pumpdump/backend/internal/ledger"
	"pumpdump/backend/internal/microburst"
)

func main() {
	coinsFlag := flag.String("coins", "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,ADAUSDT,DOGEUSDT",
		"comma-separated coin universe")
	limit := flag.Int("limit", 1000, "1s bars per coin")
	onsetHorizon := flag.Int("onset-horizon", 60, "onset label horizon in seconds")
	contHorizons := flag.String("cont-horizons", "10,30,60", "continuation horizons in seconds")
	upMult := flag.Float64("up-mult", 2.0, "barrier in multiples of 30s volatility")
	downMult := flag.Float64("down-mult", 2.0, "barrier in multiples of 30s volatility")
	minBarrier := flag.Float64("min-barrier", 0.10, "trading-cost floor, percent")
	trainFrac := flag.Float64("train-frac", 0.7, "chronological train fraction")
	feePct := flag.Float64("fee-pct", 0.1, "taker fee per side, percent")
	outDir := flag.String("out", "onset_out", "output directory")
	flag.Parse()

	cfg := microburst.DefaultLabelConfig()
	cfg.UpMult = *upMult
	cfg.DownMult = *downMult
	cfg.MinBarrierPct = *minBarrier

	coins := split(*coinsFlag)
	client := binance.NewClient("https://api.binance.com")
	ctx := context.Background()

	histories := map[string][]binance.Kline{}
	for _, sym := range coins {
		bars, err := client.Klines(ctx, sym, "1s", *limit)
		if err != nil {
			fmt.Fprintf(os.Stderr, "onset: %s: %v (skipping)\n", sym, err)
			continue
		}
		histories[sym] = bars
	}
	btc := histories["BTCUSDT"]

	// Build one example per second per coin.
	var examples []example
	contHorizonList := splitInts(*contHorizons)
	for _, sym := range coins {
		bars := histories[sym]
		if len(bars) < 70 {
			continue
		}
		feats := microburst.FeatureBuckets(bars, btc, cfg.VolWindow)
		for i := 60; i+*onsetHorizon < len(bars); i++ {
			e := example{
				symbol: sym,
				tsms:   bars[i].OpenTime,
				feat:   feats[i],
				onset:  microburst.OnsetLabel(bars, i, *onsetHorizon, cfg),
				cont:   map[int]string{},
			}
			for _, h := range contHorizonList {
				e.cont[h] = microburst.ContinuationLabel(bars, i, h, cfg)
			}
			examples = append(examples, e)
		}
	}

	fmt.Printf("onset: %d symbols, %d one-second examples\n", len(histories), len(examples))
	if len(examples) < 200 {
		fmt.Printf("insufficient sample: %d examples, need 200\n", len(examples))
		return
	}

	if err := os.MkdirAll(*outDir, 0o755); err != nil {
		fmt.Fprintf(os.Stderr, "onset: mkdir: %v\n", err)
		os.Exit(1)
	}

	sort.Slice(examples, func(a, b int) bool { return examples[a].tsms < examples[b].tsms })
	split := int(float64(len(examples)) * *trainFrac)
	train, holdout := examples[:split], examples[split:]

	fmt.Printf("train=%d holdout=%d (chronological, horizon-gapped)\n", len(train), len(holdout))

	// Onset model: P(UP_BURST).
	fmt.Printf("\n=== onset (up-burst in %ds) ===\n", *onsetHorizon)
	onsetModel := fit(train, func(e example) int {
		if e.onset == microburst.OnsetUp {
			return 1
		}
		return 0
	})
	report(train, holdout, onsetModel, "onset", func(e example) int {
		if e.onset == microburst.OnsetUp {
			return 1
		}
		return 0
	}, *feePct)

	// Continuation models per horizon.
	for _, h := range contHorizonList {
		fmt.Printf("\n=== continuation %ds (up-first vs reversal-first) ===\n", h)
		upModel := fit(train, func(e example) int {
			if e.cont[h] == microburst.ContUp {
				return 1
			}
			return 0
		})
		report(train, holdout, upModel, fmt.Sprintf("cont_%ds_up", h), func(e example) int {
			if e.cont[h] == microburst.ContUp {
				return 1
			}
			return 0
		}, *feePct)

		revModel := fit(train, func(e example) int {
			if e.cont[h] == microburst.ContRevers {
				return 1
			}
			return 0
		})
		_ = revModel
		fmt.Printf("  reversal-first base rate: %.3f\n", baseRate(holdout, func(e example) int {
			if e.cont[h] == microburst.ContRevers {
				return 1
			}
			return 0
		}))
		writeModel(*outDir, fmt.Sprintf("cont_%ds_up", h), upModel, len(train), len(holdout))
	}
	writeModel(*outDir, "onset", onsetModel, len(train), len(holdout))
}

type example struct {
	symbol string
	tsms   int64
	feat   map[string]float64
	onset  string
	cont   map[int]string
}

func fit(rows []example, labeler func(example) int) ledger.Model {
	featRows := make([]ledger.FeatureRow, 0, len(rows))
	labels := make([]int, 0, len(rows))
	for _, r := range rows {
		featRows = append(featRows, ledger.FeatureRow{ID: r.symbol, Features: r.feat})
		labels = append(labels, labeler(r))
	}
	m, err := ledger.FitLogistic(featRows, labels)
	if err != nil {
		fmt.Fprintf(os.Stderr, "onset: fit: %v\n", err)
		os.Exit(1)
	}
	return m
}

func report(train, holdout []example, m ledger.Model, name string, labeler func(example) int, feePct float64) {
	br := baseRate(holdout, labeler)
	fmt.Printf("  base_rate=%.3f  in-sample brier=%.4f ll=%.4f\n", br, m.Brier, m.LogLoss)
	hl := eval(holdout, m, labeler)
	fmt.Printf("  holdout brier=%.4f ll=%.4f\n", hl.brier, hl.logLoss)
	pr := precisionRecall(holdout, m, labeler)
	fmt.Printf("  precision=%.3f recall=%.3f (tp=%d fp=%d fn=%d)\n", pr.prec, pr.rec, pr.tp, pr.fp, pr.fn)
	fmt.Printf("  false alerts per coin-hour: %.2f\n", falsePerCoinHour(holdout, m, labeler))
	fmt.Printf("  move after fee (barrier - 2x%.1f%%): see note\n", feePct)
}

func baseRate(rows []example, labeler func(example) int) float64 {
	if len(rows) == 0 {
		return 0
	}
	var s int
	for _, r := range rows {
		s += labeler(r)
	}
	return float64(s) / float64(len(rows))
}

type pair struct{ brier, logLoss float64 }

func eval(rows []example, m ledger.Model, labeler func(example) int) pair {
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

type pr struct {
	prec, rec  float64
	tp, fp, fn int
}

func precisionRecall(rows []example, m ledger.Model, labeler func(example) int) pr {
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

func falsePerCoinHour(rows []example, m ledger.Model, labeler func(example) int) float64 {
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
		if p >= 0.5 && labeler(r) != 1 {
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

func writeModel(dir, name string, m ledger.Model, trainN, holdoutN int) {
	path := fmt.Sprintf("%s/%s.json", dir, name)
	data, _ := json.MarshalIndent(map[string]any{
		"type": name, "version": 1, "train_n": trainN, "holdout_n": holdoutN,
		"model": m,
	}, "", "  ")
	if err := os.WriteFile(path, data, 0o644); err != nil {
		fmt.Fprintf(os.Stderr, "onset: write: %v\n", err)
		return
	}
	fmt.Printf("  model written: %s\n", path)
}

func split(s string) []string {
	var out []string
	for _, p := range strings.Split(s, ",") {
		p = strings.ToUpper(strings.TrimSpace(p))
		if p != "" {
			out = append(out, p)
		}
	}
	return out
}

func splitInts(s string) []int {
	var out []int
	for _, p := range strings.Split(s, ",") {
		n, err := strconv.Atoi(strings.TrimSpace(p))
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
