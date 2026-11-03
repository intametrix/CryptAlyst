package api

import (
	"context"
	"math"
	"sort"
	"strings"

	"pumpdump/backend/internal/binance"
)

// Testing every model, not just the volume family.
//
// The GPU runner covers the volume models, but the registry holds sixteen more —
// the moving averages, the trend and analogue models, ARIMA, the regression. Those
// are cheap enough to score on the platform, so they are: the optimiser should not
// be choosing between four candidates when sixteen are available.

// localModels lists the models the platform scores itself.
func localModels() []string {
	out := []string{}
	for name := range getRegistry() {
		switch name {
		case "vwap", "vwdrift", "vwtrend", "hybrid", "super", "timestral", "mlr":
			continue // the agent sweeps the volume family; timestral is agent-only
		}
		out = append(out, name)
	}
	sort.Strings(out)
	return out
}

// scoreModel walk-forwards one model over one window and returns its error.
func scoreModel(method string, bars []binance.Kline, horizon, lookback, step int) (mape, r2 float64, n int, ok bool) {
	if len(bars) < 60+horizon {
		return 0, 0, 0, false
	}
	var absErr, sumY, sumY2 float64
	var sse, sst float64
	for cut := 60; cut+horizon <= len(bars); cut += step {
		pts, ok := modelPath(method, bars[:cut], horizon, lookback)
		if !ok || len(pts) != horizon {
			continue
		}
		for i := 0; i < horizon; i++ {
			actual := bars[cut+i].Close
			if actual == 0 {
				continue
			}
			diff := pts[i] - actual
			absErr += math.Abs(diff) / actual
			sse += diff * diff
			sumY += actual
			sumY2 += actual * actual
			n++
		}
	}
	if n == 0 {
		return 0, 0, 0, false
	}
	mape = absErr / float64(n) * 100
	// R² against the mean of the same observations.
	mean := sumY / float64(n)
	sst = sumY2 - float64(n)*mean*mean
	if sst > 0 {
		r2 = 1 - sse/sst
	}
	return mape, r2, n, true
}

// localModelSweep scores every platform-side model over the phase grid and
// returns a payload shaped like the agent's, so record() needs no special case.
func (s *Server) localModelSweep(ctx context.Context, symbol, interval string) (map[string]any, error) {
	bars, err := s.barsFor(ctx, symbol, interval, 900)
	if err != nil || len(bars) < 90 {
		return nil, err
	}
	rows := []map[string]any{}
	for _, method := range localModels() {
		for _, cell := range phaseCells {
			barsN, _ := cell["bars"].(int)
			horizon, _ := cell["horizon"].(int)
			if barsN <= 0 || horizon <= 0 || barsN > len(bars) {
				continue
			}
			window := bars[len(bars)-barsN:]
			mape, r2, n, ok := scoreModel(method, window, horizon, 0, 5)
			if !ok {
				continue
			}
			rows = append(rows, map[string]any{
				"method": method, "lookback": 0, "bars": barsN, "horizon": horizon,
				"r2": r2, "mape_pct": mape, "tests": n,
				"family": modelFamily(method), "source": "platform",
			})
		}
	}
	if len(rows) == 0 {
		return nil, nil
	}
	sort.SliceStable(rows, func(i, j int) bool {
		a, _ := rows[i]["r2"].(float64)
		b, _ := rows[j]["r2"].(float64)
		return a > b
	})
	if len(rows) > 40 {
		rows = rows[:40]
	}
	// The engine's idea search and labels expect a method name it can render.
	best := rows[0]
	name, _ := best["method"].(string)
	return map[string]any{"results": rows, "series": []any{}, "local": strings.ToUpper(name)}, nil
}
