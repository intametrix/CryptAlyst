// Package perf measures how each symbol's predictions have actually turned out,
// and turns that record into a correction the online confidence can use.
//
// The problem it solves: a single global accuracy number treats every symbol as
// interchangeable, but they are not. One symbol's pumps may follow through
// reliably and another's may be pure noise that trips the same gates. Applying
// one confidence to both means the reliable one is understated and the noisy one
// is overstated, and the user cannot tell which they are looking at.
//
// Two rules keep this honest:
//
//  1. **Small samples are shrunk, not trusted.** A symbol with 2 resolved
//     predictions and 2 hits has not earned a 100% hit rate; its estimate is
//     pulled toward the global rate by a Beta prior. A correction built on three
//     observations is noise wearing a number's clothes.
//  2. **The correction is bounded and reported.** It never moves confidence by
//     more than a fixed amount, and the basis string says exactly what it did
//     and on how many observations, so a damped confidence is never mistaken for
//     a measured one.
package perf

import (
	"bufio"
	"encoding/json"
	"log"
	"math"
	"os"
	"sync"
	"time"
)

// Record is one graded prediction, as read from the ledger.
type Record struct {
	Symbol   string
	Detector string
	Severity string
	Kind     string
	Resolved bool
	Correct  bool
	// MFE/MAE in percent, for expectancy.
	MFEPct float64
	MAEPct float64
	// HorizonMS is the labelling horizon that produced this outcome. Outcomes
	// from different horizons are NOT comparable -- a 90% move over 15 minutes
	// and the same move over 90 seconds are different events -- so a statistic
	// that pools them describes neither.
	HorizonMS int64
	TSMS      int64
}

// SymbolStats is a symbol's track record.
type SymbolStats struct {
	Symbol      string  `json:"symbol"`
	Predictions int     `json:"predictions"`
	Resolved    int     `json:"resolved"`
	Correct     int     `json:"correct"`
	Unresolved  int     `json:"unresolved"`
	HitRate     float64 `json:"hit_rate"`
	// ShrunkHitRate is the estimate after Beta shrinkage toward the global rate.
	// This is the number a correction should use; HitRate is reported raw so the
	// two can be compared and the shrinkage seen.
	ShrunkHitRate float64 `json:"shrunk_hit_rate"`
	AvgMFEPct     float64 `json:"avg_mfe_pct"`
	AvgMAEPct     float64 `json:"avg_mae_pct"`
	ExpectancyPct float64 `json:"expectancy_pct"`
	LastTSMS      int64   `json:"last_ts_ms"`
	// Basis states what the numbers rest on, so a thin record is visible.
	Basis string `json:"basis"`
}

// Params controls the shrinkage and the correction bounds.
type Params struct {
	// PriorStrength is the weight of the global rate in the Beta prior,
	// expressed as an equivalent number of observations. At 20, a symbol needs
	// ~20 resolutions before its own record outweighs the global average.
	PriorStrength float64
	// MaxAdjustment bounds how far confidence may move, as a fraction.
	MaxAdjustment float64
	// MinResolvedForAdjustment is the point below which no correction is applied
	// at all, only reported.
	MinResolvedForAdjustment int
	// CacheTTL is how long a parsed ledger is reused before re-reading.
	CacheTTL time.Duration
}

// DefaultParams returns starting values.
func DefaultParams() Params {
	return Params{
		PriorStrength:            20,
		MaxAdjustment:            0.25,
		MinResolvedForAdjustment: 5,
		CacheTTL:                 30 * time.Second,
	}
}

// Service reads the ledger and answers per-symbol questions.
type Service struct {
	path   string
	params Params

	mu       sync.RWMutex
	bySymbol map[string]*SymbolStats
	global   SymbolStats
	loadedMS int64
	// malformed counts unparseable ledger lines, so a truncated write cannot
	// silently shrink the sample the statistics rest on.
	malformed int
	// skippedHorizon counts records excluded because they were labelled at a
	// different horizon than the dominant one.
	skippedHorizon int
}

// New creates a service over a ledger path.
func New(path string, params Params) *Service {
	if params.PriorStrength <= 0 {
		params = DefaultParams()
	}
	return &Service{path: path, params: params, bySymbol: map[string]*SymbolStats{}}
}

// Refresh re-reads the ledger if the cache has expired. It is cheap to call on
// every request because it usually does nothing.
func (s *Service) Refresh(nowMS int64) {
	s.mu.RLock()
	fresh := s.loadedMS > 0 && nowMS-s.loadedMS < s.params.CacheTTL.Milliseconds()
	s.mu.RUnlock()
	if fresh {
		return
	}
	s.reload(nowMS)
}

// reload parses the ledger.
//
// It joins predictions to resolutions by id, which is the only correct join: a
// resolution without its prediction has no symbol, and inferring one would
// attribute outcomes to the wrong instrument.
func (s *Service) reload(nowMS int64) {
	records := map[string]Record{}
	order := make([]string, 0, 256)

	file, err := os.Open(s.path)
	if err != nil {
		s.mu.Lock()
		s.loadedMS = nowMS
		s.mu.Unlock()
		return
	}
	defer file.Close()

	malformed := 0
	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for scanner.Scan() {
		line := scanner.Bytes()
		if len(line) == 0 {
			continue
		}
		var envelope struct {
			Type       string  `json:"type"`
			ID         string  `json:"id"`
			Label      string  `json:"label"`
			MFEPct     float64 `json:"mfe_pct"`
			MAEPct     float64 `json:"mae_pct"`
			HorizonMS  int64   `json:"horizon_ms"`
			Prediction *struct {
				ID       string `json:"id"`
				Symbol   string `json:"symbol"`
				Detector string `json:"detector"`
				Kind     string `json:"kind"`
				Severity string `json:"severity"`
				TSMS     int64  `json:"ts_ms"`
			} `json:"prediction"`
		}
		if err := json.Unmarshal(line, &envelope); err != nil {
			// Counted, not silently dropped: an unparseable line in the
			// prediction stream shrinks n for the learner, and a reader that
			// quietly skips records looks identical to one reading a shorter
			// ledger. Same guard as the alert history, same reason.
			malformed++
			continue
		}
		switch envelope.Type {
		case "prediction":
			if envelope.Prediction == nil || envelope.Prediction.ID == "" {
				continue
			}
			id := envelope.Prediction.ID
			records[id] = Record{
				Symbol:   envelope.Prediction.Symbol,
				Detector: envelope.Prediction.Detector,
				Kind:     envelope.Prediction.Kind,
				Severity: envelope.Prediction.Severity,
				TSMS:     envelope.Prediction.TSMS,
			}
			order = append(order, id)
		case "resolution":
			rec, ok := records[envelope.ID]
			if !ok {
				continue
			}
			rec.Resolved = true
			rec.Correct = envelope.Label == "CORRECT"
			rec.MFEPct = envelope.MFEPct
			rec.MAEPct = envelope.MAEPct
			rec.HorizonMS = envelope.HorizonMS
			records[envelope.ID] = rec
		}
	}

	// One horizon only. Pooling regimes would let a short-horizon run silently
	// dilute or inflate a long-horizon statistic, and nothing downstream could
	// tell that it had happened -- the exact failure the config-epoch rule
	// exists to prevent, applied to the horizon instead of the gates.
	dominant := dominantHorizon(records, order)
	skippedHorizon := 0

	bySymbol := map[string]*SymbolStats{}
	global := &SymbolStats{Symbol: "*"}
	for _, id := range order {
		rec := records[id]
		if rec.Symbol == "" {
			continue
		}
		if dominant > 0 && rec.HorizonMS > 0 && rec.HorizonMS != dominant {
			skippedHorizon++
			continue
		}
		stats, ok := bySymbol[rec.Symbol]
		if !ok {
			stats = &SymbolStats{Symbol: rec.Symbol}
			bySymbol[rec.Symbol] = stats
		}
		for _, target := range []*SymbolStats{stats, global} {
			target.Predictions++
			if rec.Resolved {
				target.Resolved++
				if rec.Correct {
					target.Correct++
				}
				target.AvgMFEPct += rec.MFEPct
				target.AvgMAEPct += rec.MAEPct
			} else {
				target.Unresolved++
			}
			if rec.TSMS > target.LastTSMS {
				target.LastTSMS = rec.TSMS
			}
		}
	}

	globalRate := 0.5
	if global.Resolved > 0 {
		globalRate = float64(global.Correct) / float64(global.Resolved)
	}
	finish(global, globalRate)
	for _, stats := range bySymbol {
		finish(stats, globalRate)
	}

	if malformed > 0 {
		log.Printf("perf ledger: skipped %d malformed line(s) in %s "+
			"(partial write or corruption); counts below are from the readable records only",
			malformed, s.path)
	}
	if skippedHorizon > 0 {
		log.Printf("perf ledger: excluded %d record(s) labelled at a different horizon "+
			"than the dominant %dms, because outcomes from different horizons are not comparable",
			skippedHorizon, dominant)
	}

	s.mu.Lock()
	s.bySymbol = bySymbol
	s.global = *global
	s.malformed = malformed
	s.skippedHorizon = skippedHorizon
	s.loadedMS = nowMS
	s.mu.Unlock()
}

// dominantHorizon returns the horizon most resolutions were labelled at, so a
// handful of records from a different configuration cannot contaminate the set.
func dominantHorizon(records map[string]Record, order []string) int64 {
	counts := map[int64]int{}
	for _, id := range order {
		rec := records[id]
		if rec.Resolved && rec.HorizonMS > 0 {
			counts[rec.HorizonMS]++
		}
	}
	best, bestN := int64(0), 0
	for horizon, n := range counts {
		if n > bestN {
			best, bestN = horizon, n
		}
	}
	return best
}

func finish(stats *SymbolStats, globalRate float64) {
	if stats.Resolved > 0 {
		stats.HitRate = float64(stats.Correct) / float64(stats.Resolved)
		stats.AvgMFEPct /= float64(stats.Resolved)
		stats.AvgMAEPct /= float64(stats.Resolved)
	}
	stats.ExpectancyPct = stats.AvgMFEPct + stats.AvgMAEPct
	// Beta shrinkage toward the global rate. With no observations the estimate
	// IS the global rate, which is the honest answer: we know nothing specific.
	const prior = 20.0
	stats.ShrunkHitRate = (float64(stats.Correct) + prior*globalRate) / (float64(stats.Resolved) + prior)
	switch {
	case stats.Resolved == 0:
		stats.Basis = "no resolved predictions for this symbol; estimate is the market-wide rate"
	case stats.Resolved < 10:
		stats.Basis = "thin record: shrinkage dominates, treat as indicative only"
	default:
		stats.Basis = "measured from this symbol's own resolved predictions"
	}
	stats.HitRate = round4(stats.HitRate)
	stats.ShrunkHitRate = round4(stats.ShrunkHitRate)
	stats.AvgMFEPct = round4(stats.AvgMFEPct)
	stats.AvgMAEPct = round4(stats.AvgMAEPct)
	stats.ExpectancyPct = round4(stats.ExpectancyPct)
}

// Symbol returns one symbol's stats.
func (s *Service) Symbol(symbol string) (SymbolStats, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	stats, ok := s.bySymbol[symbol]
	if !ok {
		return SymbolStats{Symbol: symbol, Basis: "no predictions recorded for this symbol yet"}, false
	}
	return *stats, true
}

// SkippedHorizon returns how many records were excluded as a different horizon.
func (s *Service) SkippedHorizon() int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.skippedHorizon
}

// Malformed returns the count of unreadable ledger lines in the last load.
func (s *Service) Malformed() int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.malformed
}

// Global returns market-wide stats.
func (s *Service) Global() SymbolStats {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.global
}

// All returns every symbol with a record, hottest record first.
func (s *Service) All() []SymbolStats {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]SymbolStats, 0, len(s.bySymbol))
	for _, stats := range s.bySymbol {
		out = append(out, *stats)
	}
	return out
}

// Adjustment is the confidence correction for one symbol.
type Adjustment struct {
	Multiplier float64 `json:"multiplier"`
	Applied    bool    `json:"applied"`
	Basis      string  `json:"basis"`
}

// Adjust turns a symbol's record into a bounded multiplier on confidence.
//
// The mapping is deliberately gentle and symmetric: a symbol that hits twice as
// often as the market gets a boost, one that hits half as often gets a cut, and
// both are capped. Confidence is already a heuristic; letting a thin record move
// it far would be replacing one guess with a louder guess.
func (s *Service) Adjust(symbol string) Adjustment {
	stats, ok := s.Symbol(symbol)
	if !ok || stats.Resolved < s.params.MinResolvedForAdjustment {
		return Adjustment{
			Multiplier: 1,
			Applied:    false,
			Basis:      "no per-symbol adjustment: " + stats.Basis,
		}
	}
	global := s.Global()
	if global.ShrunkHitRate <= 0 {
		return Adjustment{Multiplier: 1, Applied: false, Basis: "no adjustment: no market-wide rate yet"}
	}
	ratio := stats.ShrunkHitRate / global.ShrunkHitRate
	// ratio 1.0 -> 1.0; ratio 2.0 -> 1+Max; ratio 0.5 -> 1-Max/2. Gentle on the
	// downside so a cold streak does not silence a symbol entirely.
	delta := 0.0
	if ratio >= 1 {
		delta = math.Min(s.params.MaxAdjustment, (ratio-1)*s.params.MaxAdjustment)
	} else {
		delta = -math.Min(s.params.MaxAdjustment, (1-ratio)*s.params.MaxAdjustment)
	}
	multiplier := 1 + delta
	if multiplier < 0.5 {
		multiplier = 0.5
	}
	if multiplier > 1.5 {
		multiplier = 1.5
	}
	return Adjustment{
		Multiplier: round4(multiplier),
		Applied:    true,
		Basis: "per-symbol record: " + itoa(stats.Resolved) + " resolved, shrunk hit rate " +
			f3(stats.ShrunkHitRate) + " vs market " + f3(global.ShrunkHitRate),
	}
}

func round4(v float64) float64 {
	if math.IsNaN(v) || math.IsInf(v, 0) {
		return 0
	}
	return math.Round(v*10000) / 10000
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var buf [20]byte
	i := len(buf)
	for n > 0 {
		i--
		buf[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		buf[i] = '-'
	}
	return string(buf[i:])
}

func f3(v float64) string {
	return trimFloat(v, 3)
}
