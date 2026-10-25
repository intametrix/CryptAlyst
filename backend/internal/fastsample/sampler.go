// Package fastsample records a one-second price path for the first minute after
// a detection, so a short-horizon question becomes falsifiable.
//
// Why this exists: the team's prior is that direction over the next ten seconds
// is not learnable from public spot data. A prior is not a measurement. Ten
// seconds is cheap to sample, so the honest move is to measure it and let the
// data settle the argument -- and a clean negative result is worth as much as a
// positive one, because it retires the question permanently.
//
// It is deliberately a SEPARATE package reading the same JSONL ledger: the
// three-second tracker belongs to another agent, and a second writer editing
// the same structs would risk corrupting the record stream that every
// measurement in this project depends on.
//
// It is also deliberately decoupled from the label. The label rule below was
// written down and committed BEFORE any data was collected, because a rule
// chosen after seeing the results is not a test, it is a rationalisation.
package fastsample

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"

	"log"
	"math"
	"os"
	"pumpdump/backend/internal/ledger"
	"strings"
	"sync"
	"time"
)

// LockedLabelBandPct is the no-information band, fixed in advance.
//
// The label is three-way, not binary. Forcing a binary verdict on a move that
// is indistinguishable from noise manufactures signal out of nothing: a coin
// flip recorded as a "win" is worse than no label, because it trains the model
// on its own noise.
const LockedLabelBandPct = 0.10

// SampleInterval is the fine-grained sampling period for the first minute.
const SampleInterval = time.Second

// SampleWindow is how long the fine-grained sampling runs.
const SampleWindow = 60 * time.Second

// LabelHorizon is the horizon the short-horizon label is computed at.
const LabelHorizon = 10 * time.Second

// PriceFetcher supplies a live price. Implemented by the flow engine, so
// sampling costs no exchange weight.
type PriceFetcher interface {
	Price(ctx context.Context, symbol string) (float64, error)
}

// Config controls the sampler.
type Config struct {
	Path     string
	Interval time.Duration
	Window   time.Duration
	// PollInterval is how often the ledger file is rescanned for new
	// predictions.
	PollInterval time.Duration
}

func (c Config) withDefaults() Config {
	if c.Interval <= 0 {
		c.Interval = SampleInterval
	}
	if c.Window <= 0 {
		c.Window = SampleWindow
	}
	if c.PollInterval <= 0 {
		c.PollInterval = 5 * time.Second
	}
	return c
}

// Sampler tails the ledger and samples newly recorded predictions.
type Sampler struct {
	cfg     Config
	market  PriceFetcher
	mu      sync.Mutex
	tracked map[string]bool
	offset  int64
}

// New creates a sampler.
func New(cfg Config, market PriceFetcher) *Sampler {
	return &Sampler{
		cfg:     cfg.withDefaults(),
		market:  market,
		tracked: make(map[string]bool),
	}
}

// prediction is the subset of a ledger prediction record this package needs.
type prediction struct {
	ID         string  `json:"id"`
	Symbol     string  `json:"symbol"`
	Direction  int     `json:"direction"`
	EntryPrice float64 `json:"entry_price"`
	TSMS       int64   `json:"ts_ms"`
}

type record struct {
	Type       string      `json:"type"`
	Prediction *prediction `json:"prediction,omitempty"`
	ID         string      `json:"id,omitempty"`
}

// Run tails the ledger until the context is cancelled.
func (s *Sampler) Run(ctx context.Context) {
	log.Printf("fastsample: 1s path sampling for %s after each detection (ledger %s)",
		s.cfg.Window, s.cfg.Path)
	ticker := time.NewTicker(s.cfg.PollInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			s.scan(ctx)
		}
	}
}

// scan reads any new prediction records and starts sampling them.
func (s *Sampler) scan(ctx context.Context) {
	file, err := os.Open(s.cfg.Path)
	if err != nil {
		return
	}
	defer file.Close()

	s.mu.Lock()
	offset := s.offset
	s.mu.Unlock()
	if offset > 0 {
		if _, err := file.Seek(offset, 0); err != nil {
			return
		}
	}

	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	var consumed int64 = offset
	for scanner.Scan() {
		line := scanner.Bytes()
		consumed += int64(len(line)) + 1
		if len(line) == 0 || line[0] != '{' {
			continue
		}
		var rec record
		if err := json.Unmarshal(line, &rec); err != nil {
			continue
		}
		if rec.Type != "prediction" || rec.Prediction == nil {
			continue
		}
		pred := *rec.Prediction
		if pred.ID == "" || pred.Symbol == "" || pred.EntryPrice <= 0 {
			continue
		}

		s.mu.Lock()
		already := s.tracked[pred.ID]
		if !already {
			s.tracked[pred.ID] = true
		}
		s.offset = consumed
		s.mu.Unlock()

		if already {
			continue
		}
		go s.sample(ctx, pred)
	}
}

// sample records the fine-grained path, then the locked short-horizon label.
func (s *Sampler) sample(ctx context.Context, pred prediction) {
	deadline := time.Now().Add(s.cfg.Window)
	// The prediction carries its own timestamp, so the horizon is measured from
	// the DETECTION, not from when this goroutine happened to start.
	started := time.UnixMilli(pred.TSMS)
	peakHigh, peakLow := pred.EntryPrice, pred.EntryPrice
	emitted := map[string]bool{}

	for time.Now().Before(deadline) {
		select {
		case <-ctx.Done():
			return
		case <-time.After(s.cfg.Interval):
		}

		price, err := s.market.Price(ctx, pred.Symbol)
		if err != nil || price <= 0 {
			continue
		}
		if price > peakHigh {
			peakHigh = price
		}
		if price < peakLow {
			peakLow = price
		}

		s.append(map[string]any{
			"type":    "path_1s",
			"id":      pred.ID,
			"symbol":  pred.Symbol,
			"ts_ms":   time.Now().UnixMilli(),
			"price":   price,
			"mfe_pct": round4((peakHigh - pred.EntryPrice) / pred.EntryPrice * 100),
			"mae_pct": round4((peakLow - pred.EntryPrice) / pred.EntryPrice * 100),
			"horizon": "1s",
		})

		elapsed := time.Since(started)

		// The locked 10s direction-adjusted label (existing consumer) is
		// emitted once, as before.
		if elapsed >= LabelHorizon && !emitted["label_10s"] {
			emitted["label_10s"] = true
			if label, ok := Label10s(pred.EntryPrice, price, pred.Direction); ok {
				s.append(map[string]any{
					"type":       "label_10s",
					"id":         pred.ID,
					"symbol":     pred.Symbol,
					"label":      label,
					"ts_ms":      time.Now().UnixMilli(),
					"price":      price,
					"entry":      pred.EntryPrice,
					"band_pct":   LockedLabelBandPct,
					"direction":  pred.Direction,
					"label_rule": "locked before data collection",
				})
			} else {
				s.append(map[string]any{
					"type":       "label_10s",
					"id":         pred.ID,
					"symbol":     pred.Symbol,
					"label":      "DISCARD",
					"ts_ms":      time.Now().UnixMilli(),
					"price":      price,
					"entry":      pred.EntryPrice,
					"band_pct":   LockedLabelBandPct,
					"direction":  pred.Direction,
					"label_rule": "locked before data collection",
				})
			}
		}

		// Symmetric up/reversal barrier labels at 10/30/60s: the micro-burst
		// outcome the horizon models are trained on. "UP" means price rose by
		// the band first, "REVERSAL" means it fell by the band first, and the
		// no-hit case is simply not recorded (NEITHER).
		for _, h := range []time.Duration{10 * time.Second, 30 * time.Second, 60 * time.Second} {
			key := fmt.Sprintf("label_barrier_%ds", int(h/time.Second))
			if elapsed >= h && !emitted[key] {
				emitted[key] = true
				if label, hit := LabelBarrier(pred.EntryPrice, price, LockedLabelBandPct); hit {
					s.append(map[string]any{
						"type":       key,
						"id":         pred.ID,
						"symbol":     pred.Symbol,
						"label":      label,
						"ts_ms":      time.Now().UnixMilli(),
						"price":      price,
						"entry":      pred.EntryPrice,
						"band_pct":   LockedLabelBandPct,
						"direction":  pred.Direction,
						"label_rule": "symmetric up/reversal barrier, locked before data collection",
					})
				}
			}
		}
	}
}

// LabelBarrier applies the symmetric up/reversal rule used by the micro-burst
// horizon models. It is NOT direction-adjusted: for any alert, a move of
// +band% from entry is "UP" (continuation of the burst's direction) and a move
// of -band% is "REVERSAL". A move inside the band returns hit=false (NEITHER),
// which is simply not recorded so a no-information sample is never forced into
// win or loss.
func LabelBarrier(entry, price, bandPct float64) (string, bool) {
	if entry <= 0 || price <= 0 {
		return "", false
	}
	move := (price - entry) / entry * 100
	switch {
	case move >= bandPct:
		return "UP", true
	case move <= -bandPct:
		return "REVERSAL", true
	default:
		return "NEITHER", false
	}
}

// Label10s applies the locked three-way rule, written down before any data was
// collected:
//
//	direction-adjusted move at alert+10s >= +0.10%  -> "CORRECT"
//	                                          <= -0.10% -> "WRONG"
//	anything between                                -> ok=false (DISCARD)
//
// The middle band is the no-information zone. A binary label here would
// manufacture signal out of noise.
func Label10s(entry, price float64, direction int) (string, bool) {
	if entry <= 0 || price <= 0 {
		return "", false
	}
	move := (price - entry) / entry * 100
	if direction < 0 {
		move = -move
	}
	switch {
	case move >= LockedLabelBandPct:
		return "CORRECT", true
	case move <= -LockedLabelBandPct:
		return "WRONG", true
	default:
		return "DISCARD", false
	}
}

func (s *Sampler) append(payload map[string]any) {
	data, err := json.Marshal(payload)
	if err != nil {
		return
	}
	// Stamp explicit units through the ledger's choke point, so no reader ever
	// has to infer whether mfe_pct is a percentage of entry or a fraction.
	// This package emitted unlabelled percent values, and an unlabelled unit is
	// exactly what produced a x100 misreading of this same data once already.
	if stamped, err := ledger.StampUnits(data); err == nil {
		data = stamped
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	file, err := os.OpenFile(s.cfg.Path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return
	}
	defer file.Close()
	_, _ = file.Write(append(data, '\n'))
}

// Stats reports what the sampler is tracking, for the status line.
func (s *Sampler) Stats() (tracked int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.tracked)
}

func round4(v float64) float64 {
	if math.IsNaN(v) || math.IsInf(v, 0) {
		return 0
	}
	return math.Round(v*10000) / 10000
}

var _ = strings.TrimSpace
