// Package ledger implements a prediction-outcome ledger for pump/dump
// detection signals. Every PUMP/DUMP signal is recorded as an open prediction;
// a background tracker samples prices, computes direction-adjusted MFE/MAE,
// and assigns a fixed-horizon label (CORRECT / WRONG / EXPIRED). All events
// are appended to a JSONL file so the record survives restarts and can be
// analysed offline by the learner (cmd/learner) and the Report function in
// metrics.go.
package ledger

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"sort"
	"sync"
	"time"

	"pumpdump/backend/internal/detect"
)

// Default labelling constants; see DESIGN.md.
const (
	DefaultSampleInterval = 3 * time.Second
	DefaultHorizon        = 15 * time.Minute
	DefaultTargetPct      = 1.5 // +H% win threshold
	DefaultStopPct        = 1.0 // -S% fail threshold
	DefaultMaxTracked     = 512

	// pathEpsilonPct is the minimum MFE/MAE change (in percentage points)
	// that triggers a new {"type":"path"} record.
	pathEpsilonPct = 0.05
)

// Labels assigned at resolution.
const (
	LabelCorrect = "CORRECT"
	LabelWrong   = "WRONG"
	LabelExpired = "EXPIRED"
)

// PriceFetcher is the market-data surface the tracker needs. The binance
// client can be adapted to it, e.g.:
//
//	type marketAdapter struct{ c *binance.Client }
//	func (m marketAdapter) Price(ctx context.Context, symbol string) (float64, error) {
//		ks, err := m.c.Klines(ctx, symbol, "1m", 1)
//		if err != nil || len(ks) == 0 { return 0, err }
//		return ks[len(ks)-1].Close, nil
//	}
type PriceFetcher interface {
	Price(ctx context.Context, symbol string) (float64, error)
}

// Config controls a JSONLRecorder. Zero fields fall back to the defaults.
type Config struct {
	Path           string        // JSONL ledger file.
	SampleInterval time.Duration // default 3s
	Horizon        time.Duration // default 15m
	TargetPct      float64       // default 1.5
	StopPct        float64       // default 1.0
	MaxTracked     int           // default 512, oldest evicted
}

func (c Config) withDefaults() Config {
	if c.SampleInterval <= 0 {
		c.SampleInterval = DefaultSampleInterval
	}
	if c.Horizon <= 0 {
		c.Horizon = DefaultHorizon
	}
	if c.TargetPct <= 0 {
		c.TargetPct = DefaultTargetPct
	}
	if c.StopPct <= 0 {
		c.StopPct = DefaultStopPct
	}
	if c.MaxTracked <= 0 {
		c.MaxTracked = DefaultMaxTracked
	}
	return c
}

// Prediction is an open (or resolved) directional bet derived from one
// detect.Signal. Features are the flat numeric snapshot captured at alert
// time (Signal.Metrics plus Signal.Components with a "component_" prefix).
type Prediction struct {
	ID         string             `json:"id"`
	Detector   string             `json:"detector"`
	Kind       string             `json:"kind"`
	Symbol     string             `json:"symbol"`
	Direction  int                `json:"direction"`
	Severity   string             `json:"severity"`
	Score      float64            `json:"score"`
	EntryPrice float64            `json:"entry_price"`
	TSMS       int64              `json:"ts_ms"`
	Features   map[string]float64 `json:"features,omitempty"`
}

// Recorder is the integration surface: the scanner calls Record for every
// emitted signal and runs Track in a background goroutine.
type Recorder interface {
	Record(sig detect.Signal)
	Track(ctx context.Context)
}

// JSONL file record shapes.
type predictionRecord struct {
	Type       string     `json:"type"` // "prediction"
	Prediction Prediction `json:"prediction"`
	HorizonMS  int64      `json:"horizon_ms"`
	TargetPct  float64    `json:"target_pct"`
	StopPct    float64    `json:"stop_pct"`
	TSMS       int64      `json:"ts_ms"`
}

type pathRecord struct {
	Type   string  `json:"type"` // "path"
	ID     string  `json:"id"`
	TSMS   int64   `json:"ts_ms"`
	Price  float64 `json:"price"`
	MFEPct float64 `json:"mfe_pct"`
	MAEPct float64 `json:"mae_pct"`
}

type resolutionRecord struct {
	Type         string  `json:"type"` // "resolution"
	ID           string  `json:"id"`
	Label        string  `json:"label"`
	MFEPct       float64 `json:"mfe_pct"`
	MAEPct       float64 `json:"mae_pct"`
	TimeToMFESec float64 `json:"time_to_mfe_sec"`
	ResolvedMS   int64   `json:"resolved_ms"`
	TargetPct    float64 `json:"target_pct"`
	StopPct      float64 `json:"stop_pct"`
	HorizonMS    int64   `json:"horizon_ms"`
}

// trackedState is the in-memory tracking state of one open prediction.
type trackedState struct {
	pred        Prediction
	mfe         float64 // direction-adjusted, in fraction (0.01 = 1%)
	mae         float64
	stopHit     bool
	stopAtMS    int64
	lastPathMFE float64 // last logged MFE, for change detection
	lastPathMAE float64 // last logged MAE
	mfeAtMS     int64   // when MFE first reached its max
	resolved    bool
	label       string
}

// JSONLRecorder appends ledger records to a JSONL file and tracks open
// predictions to their fixed-horizon label.
type JSONLRecorder struct {
	cfg    Config
	market PriceFetcher

	mu    sync.Mutex
	open  map[string]*trackedState // predictions still being tracked
	f     *os.File
	w     *bufio.Writer
	order []string // insertion order of open ids, for oldest-first eviction
}

// New creates a JSONLRecorder, opening (creating if needed) the ledger file
// and reloading any not-yet-resolved predictions from it so a restart neither
// loses open predictions nor double-counts them.
func New(path string, market PriceFetcher, cfg Config) (*JSONLRecorder, error) {
	if path == "" {
		return nil, errors.New("ledger: empty path")
	}
	if market == nil {
		return nil, errors.New("ledger: nil PriceFetcher")
	}
	cfg = cfg.withDefaults()
	r := &JSONLRecorder{
		cfg:    cfg,
		market: market,
		open:   make(map[string]*trackedState),
	}
	if err := r.reload(path); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return nil, fmt.Errorf("ledger: open %s: %w", path, err)
	}
	r.f = f
	r.w = bufio.NewWriter(f)
	return r, nil
}

// reload replays an existing JSONL file: predictions without a resolution
// record become open tracked state again (MFE/MAE restart from zero — the
// price path before the restart is already on disk for diagnostics).
func (r *JSONLRecorder) reload(path string) error {
	raw, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return fmt.Errorf("ledger: read %s: %w", path, err)
	}
	sc := bufio.NewScanner(bytes.NewReader(raw))
	sc.Buffer(make([]byte, 0, 1<<20), 1<<20)
	resolved := make(map[string]bool)
	var preds []predictionRecord
	for sc.Scan() {
		line := sc.Bytes()
		if len(line) == 0 {
			continue
		}
		var head struct {
			Type       string     `json:"type"`
			Prediction Prediction `json:"prediction"`
			ID         string     `json:"id"`
			Label      string     `json:"label"`
		}
		if err := json.Unmarshal(line, &head); err != nil {
			continue // tolerate a torn/corrupt line rather than losing everything
		}
		switch head.Type {
		case "prediction":
			preds = append(preds, mustPredictionRecord(line))
		case "resolution":
			if head.ID != "" {
				resolved[head.ID] = true
			}
		}
	}
	for _, pr := range preds {
		if resolved[pr.Prediction.ID] {
			continue
		}
		r.open[pr.Prediction.ID] = &trackedState{pred: pr.Prediction}
		r.order = append(r.order, pr.Prediction.ID)
	}
	return nil
}

func mustPredictionRecord(line []byte) predictionRecord {
	var pr predictionRecord
	_ = json.Unmarshal(line, &pr)
	return pr
}

// Record registers an open prediction for PUMP/DUMP signals and appends the
// prediction record immediately. All other kinds pass unrecorded.
func (r *JSONLRecorder) Record(sig detect.Signal) {
	if sig.Kind != "PUMP" && sig.Kind != "DUMP" {
		return
	}
	p := Prediction{
		ID:         sig.ID,
		Detector:   sig.Detector,
		Kind:       sig.Kind,
		Symbol:     sig.Symbol,
		Direction:  sig.Direction,
		Severity:   sig.Severity,
		Score:      sig.Score,
		EntryPrice: sig.Price,
		TSMS:       sig.TSMS,
		Features:   extractFeatures(sig),
	}
	r.mu.Lock()
	if _, dup := r.open[p.ID]; !dup {
		r.open[p.ID] = &trackedState{pred: p}
		r.order = append(r.order, p.ID)
		for len(r.order) > r.cfg.MaxTracked {
			delete(r.open, r.order[0])
			r.order = r.order[1:]
		}
	}
	err := r.write(predictionRecord{
		Type:       "prediction",
		Prediction: p,
		HorizonMS:  r.cfg.Horizon.Milliseconds(),
		TargetPct:  r.cfg.TargetPct,
		StopPct:    r.cfg.StopPct,
		TSMS:       time.Now().UnixMilli(),
	})
	// Always log one baseline path sample at registration.
	if err == nil {
		err = r.write(pathRecord{Type: "path", ID: p.ID, TSMS: p.TSMS, Price: p.EntryPrice})
	}
	r.mu.Unlock()
	if err != nil {
		fmt.Fprintf(os.Stderr, "ledger: write prediction: %v\n", err)
	}
}

// extractFeatures flattens every numeric metric value plus the score
// components into one feature map.
func extractFeatures(sig detect.Signal) map[string]float64 {
	feat := make(map[string]float64, len(sig.Metrics)+len(sig.Components))
	for k, v := range sig.Metrics {
		if f, ok := toFloat(v); ok {
			feat[k] = f
		}
	}
	for k, v := range sig.Components {
		feat["component_"+k] = v
	}
	return feat
}

func toFloat(v any) (float64, bool) {
	switch n := v.(type) {
	case float64:
		return n, true
	case float32:
		return float64(n), true
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	case bool:
		if n {
			return 1, true
		}
		return 0, true
	case nil:
		return 0, false
	default:
		return 0, false
	}
}

// Track samples prices for every open prediction on SampleInterval and
// resolves predictions at horizon or stop-out. It blocks until ctx is done.
func (r *JSONLRecorder) Track(ctx context.Context) {
	t := time.NewTicker(r.cfg.SampleInterval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case now := <-t.C:
			r.tick(ctx, now.UnixMilli())
		}
	}
}

func (r *JSONLRecorder) tick(ctx context.Context, nowMS int64) {
	r.mu.Lock()
	ids := make([]string, 0, len(r.order))
	for _, id := range r.order {
		if st, ok := r.open[id]; ok && !st.resolved {
			ids = append(ids, id)
		}
	}
	r.mu.Unlock()

	type fetchResult struct {
		id    string
		price float64
		err   error
	}
	results := make([]fetchResult, 0, len(ids))
	for _, id := range ids {
		st := r.get(id)
		if st == nil {
			continue
		}
		px, err := r.market.Price(ctx, st.pred.Symbol)
		results = append(results, fetchResult{id: id, price: px, err: err})
	}

	r.mu.Lock()
	defer r.mu.Unlock()
	for _, res := range results {
		if res.err != nil {
			continue
		}
		st, ok := r.open[res.id]
		if !ok {
			continue
		}
		r.updateLocked(st, res.id, res.price, nowMS)
	}
	if err := r.w.Flush(); err != nil {
		fmt.Fprintf(os.Stderr, "ledger: flush: %v\n", err)
	}
}

func (r *JSONLRecorder) get(id string) *trackedState {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.open[id]
}

// updateLocked applies one price sample: updates MFE/MAE, logs a path record
// on material change, resolves at stop-out or horizon. Caller holds r.mu.
func (r *JSONLRecorder) updateLocked(st *trackedState, id string, price float64, nowMS int64) {
	entry := st.pred.EntryPrice
	if entry <= 0 || price <= 0 {
		return
	}
	d := float64(st.pred.Direction)
	if d == 0 {
		d = 1
	}
	move := (price - entry) / entry * d
	fav, adv := move, -move
	if fav < 0 {
		fav = 0
	}
	if adv < 0 {
		adv = 0
	}
	if fav > st.mfe {
		st.mfe = fav
		if st.mfeAtMS == 0 {
			st.mfeAtMS = nowMS
		}
	}
	if adv > st.mae {
		st.mae = adv
	}

	// Path record only when MFE or MAE moved by >= pathEpsilonPct.
	if math.Abs(st.mfe-st.lastPathMFE) >= pathEpsilonPct/100 || math.Abs(st.mae-st.lastPathMAE) >= pathEpsilonPct/100 {
		_ = r.write(pathRecord{
			Type:   "path",
			ID:     id,
			TSMS:   nowMS,
			Price:  price,
			MFEPct: st.mfe * 100,
			MAEPct: st.mae * 100,
		})
		st.lastPathMFE = st.mfe
		st.lastPathMAE = st.mae
	}

	target := r.cfg.TargetPct / 100
	stop := r.cfg.StopPct / 100
	horizonAt := st.pred.TSMS + r.cfg.Horizon.Milliseconds()

	if !st.stopHit && st.mae >= stop {
		st.stopHit = true
		st.stopAtMS = nowMS
		// Stop-out resolves the label only if the target was not already hit.
		if st.mfe < target {
			r.resolveLocked(st, id, LabelWrong, nowMS)
			return
		}
	}
	if nowMS >= horizonAt && !st.resolved {
		if st.mfe >= target {
			r.resolveLocked(st, id, LabelCorrect, nowMS)
		} else {
			r.resolveLocked(st, id, LabelExpired, nowMS)
		}
	}
}

// resolveLocked appends the resolution record. The prediction stays in the
// open map so its MFE/MAE path continues to be logged until horizon for
// diagnostics; its label never changes after this point.
func (r *JSONLRecorder) resolveLocked(st *trackedState, id, label string, nowMS int64) {
	st.resolved = true
	st.label = label
	var ttm float64
	if st.mfeAtMS > 0 {
		ttm = float64(st.mfeAtMS-st.pred.TSMS) / 1000
	}
	_ = r.write(resolutionRecord{
		Type:         "resolution",
		ID:           id,
		Label:        label,
		MFEPct:       st.mfe * 100,
		MAEPct:       st.mae * 100,
		TimeToMFESec: ttm,
		ResolvedMS:   nowMS,
		TargetPct:    r.cfg.TargetPct,
		StopPct:      r.cfg.StopPct,
		HorizonMS:    r.cfg.Horizon.Milliseconds(),
	})
}

func (r *JSONLRecorder) write(rec any) error {
	b, err := json.Marshal(rec)
	if err != nil {
		return err
	}
	if _, err := r.w.Write(append(b, '\n')); err != nil {
		return err
	}
	return r.w.Flush()
}

// Close flushes and closes the ledger file.
func (r *JSONLRecorder) Close() error {
	r.mu.Lock()
	defer r.mu.Unlock()
	if err := r.w.Flush(); err != nil {
		return err
	}
	return r.f.Close()
}

// SortOpenIDs returns the open prediction ids oldest-first (test aid).
func (r *JSONLRecorder) SortOpenIDs() []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	out := append([]string(nil), r.order...)
	sort.Strings(out)
	return out
}
