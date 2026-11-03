package api

import (
	"context"
	"strconv"
	"sync"
	"time"

	"pumpdump/backend/internal/binance"
	"pumpdump/backend/internal/forecastlab"
)

// The forecast lab keeps iterating rather than answering once. Every run
// re-searches the hyperparameter grid on the newest bars and records whether the
// winner improved, so the panel can show a search that is still converging
// instead of a single frozen result.

// labTrack is the running state of the search for one symbol/interval/horizon.
type labTrack struct {
	Symbol     string           `json:"symbol"`
	Interval   string           `json:"interval"`
	Horizon    int              `json:"horizon"`
	Iterations int              `json:"iterations"`
	Best       string           `json:"best"`
	BestMAPE   float64          `json:"best_mape"`
	Fetches    int              `json:"fetches"`
	Reused     int              `json:"reused"`
	BestMAE    float64          `json:"best_mae"`
	Models     int              `json:"models"`
	StartedMS  int64            `json:"started_ms"`
	LastMS     int64            `json:"last_ms"`
	History    []labImprovement `json:"history"`
}

// labImprovement is one recorded step where the winner got better.
type labImprovement struct {
	MS        int64   `json:"ms"`
	Iteration int     `json:"iteration"`
	Model     string  `json:"model"`
	MAPE      float64 `json:"mape"`
	PrevMAPE  float64 `json:"prev_mape"`
	PrevModel string  `json:"prev_model"`
}

const (
	labHistoryLimit = 40
	labRefreshEvery = 20 * time.Second
)

var (
	labMu     sync.Mutex
	labTracks = map[string]*labTrack{}
	labOnce   sync.Once

	// labSeriesCache holds the bars a search already has. The chart cache is a
	// 300ms liveness cache, so without this every iteration would re-download a
	// window that cannot have changed: an hourly bar only moves when it closes.
	seriesMu    sync.Mutex
	seriesCache = map[string]labSeriesEntry{}
)

// labSeriesEntry is a cached window plus the moment it stops being valid.
type labSeriesEntry struct {
	series     []forecastlab.Point
	validUntil int64
}

func labKey(symbol, interval string, horizon int) string {
	return symbol + "|" + interval + "|" + strconv.Itoa(horizon)
}

// record folds one completed study into the tracker.
func (s *Server) recordStudy(study forecastlab.Study, horizon, windows int) *labTrack {
	if len(study.Scores) == 0 {
		return nil
	}
	best := study.Scores[0]
	key := labKey(study.Symbol, study.Interval, horizon)
	now := time.Now().UnixMilli()

	labMu.Lock()
	defer labMu.Unlock()
	t, ok := labTracks[key]
	if !ok {
		t = &labTrack{
			Symbol: study.Symbol, Interval: study.Interval, Horizon: horizon,
			StartedMS: now, BestMAPE: best.MAPE, Best: best.Model, BestMAE: best.MAE,
			Models:  len(study.Scores),
			History: []labImprovement{}, // never null: clients call .length on it
		}
		labTracks[key] = t
	}
	t.Iterations++
	t.Models = len(study.Scores)
	t.LastMS = now

	if best.MAPE < t.BestMAPE-1e-9 {
		t.History = append(t.History, labImprovement{
			MS: now, Iteration: t.Iterations, Model: best.Model, MAPE: best.MAPE,
			PrevMAPE: t.BestMAPE, PrevModel: t.Best,
		})
		if len(t.History) > labHistoryLimit {
			t.History = t.History[len(t.History)-labHistoryLimit:]
		}
		t.Best, t.BestMAPE, t.BestMAE = best.Model, best.MAPE, best.MAE
	}
	return t
}

// labSweep re-runs the search for one key so the tuning continues without a
// browser attached.
func (s *Server) labSweep(ctx context.Context, t labTrack) {
	if s.deps.Market == nil {
		return
	}
	series, fetched := s.labSeries(ctx, t.Symbol, t.Interval, 400)
	if len(series) < 60 {
		return
	}
	study := forecastlab.Evaluate(t.Symbol, t.Interval, series, t.Horizon, 8, 40)
	s.recordStudyWith(study, t.Horizon, fetched)
}

// startLabSweeper keeps the tracked searches moving in the background. It is
// started once, lazily, the first time a study is recorded.
func (s *Server) startLabSweeper() {
	labOnce.Do(func() {
		go func() {
			tick := time.NewTicker(labRefreshEvery)
			defer tick.Stop()
			for range tick.C {
				labMu.Lock()
				snapshot := make([]labTrack, 0, len(labTracks))
				for _, t := range labTracks {
					snapshot = append(snapshot, *t)
				}
				labMu.Unlock()

				for _, t := range snapshot {
					ctx, cancel := context.WithTimeout(context.Background(), 25*time.Second)
					s.labSweep(ctx, t)
					cancel()
				}
			}
		}()
	})
}

// trackerSnapshot returns the current search state for a key, or nil.
func trackerSnapshot(symbol, interval string, horizon int) *labTrack {
	labMu.Lock()
	defer labMu.Unlock()
	t, ok := labTracks[labKey(symbol, interval, horizon)]
	if !ok {
		return nil
	}
	cp := *t
	cp.History = append([]labImprovement(nil), t.History...)
	return &cp
}

// fetchKlines reads bars through the shared cache when one is configured.
func (s *Server) fetchKlines(ctx context.Context, symbol, interval string, limit int) ([]binance.Kline, error) {
	if s.deps.KlinesCache != nil {
		return s.deps.KlinesCache.Get(ctx, s.deps.Market, klinesKey{symbol, interval, limit})
	}
	return s.deps.Market.Klines(ctx, symbol, interval, limit)
}

// labSeries returns the bars for a search, reusing a cached window until the
// next bar for that interval closes. fetched reports whether upstream was hit,
// so the panel can show how much work the search avoided.
func (s *Server) labSeries(ctx context.Context, symbol, interval string, limit int) ([]forecastlab.Point, bool) {
	key := symbol + "|" + interval
	now := time.Now().UnixMilli()

	seriesMu.Lock()
	if e, ok := seriesCache[key]; ok && now < e.validUntil && len(e.series) > 0 {
		out := e.series
		seriesMu.Unlock()
		return out, false
	}
	seriesMu.Unlock()

	if s.deps.Market == nil {
		return nil, false
	}
	rows, err := s.fetchKlines(ctx, symbol, interval, limit)
	if err != nil || len(rows) < 2 {
		return nil, true
	}
	pts := make([]forecastlab.Point, 0, len(rows))
	for _, k := range rows {
		pts = append(pts, forecastlab.Point{T: k.OpenTime, V: k.Close})
	}

	// Valid until the next bar closes (plus a small grace for the exchange to
	// publish it). An hourly window is therefore downloaded once an hour.
	ms := int64(intervalMillis[interval])
	if ms <= 0 {
		ms = 60_000
	}
	validUntil := (now/ms + 1) * ms
	if validUntil <= now {
		validUntil = now + ms
	}

	seriesMu.Lock()
	seriesCache[key] = labSeriesEntry{series: pts, validUntil: validUntil}
	seriesMu.Unlock()
	return pts, true
}

// recordStudyWith folds a study in and accounts for the data it used.
func (s *Server) recordStudyWith(study forecastlab.Study, horizon int, fetched bool) *labTrack {
	t := s.recordStudy(study, horizon, 8)
	if t == nil {
		return nil
	}
	labMu.Lock()
	if fetched {
		t.Fetches++
	} else {
		t.Reused++
	}
	labMu.Unlock()
	return t
}

// storeLabSeries primes the search cache after an explicit download.
func (s *Server) storeLabSeries(symbol, interval string, pts []forecastlab.Point) {
	if len(pts) == 0 {
		return
	}
	now := time.Now().UnixMilli()
	ms := int64(intervalMillis[interval])
	if ms <= 0 {
		ms = 60_000
	}
	seriesMu.Lock()
	seriesCache[symbol+"|"+interval] = labSeriesEntry{
		series:     pts,
		validUntil: (now/ms + 1) * ms,
	}
	seriesMu.Unlock()
}
