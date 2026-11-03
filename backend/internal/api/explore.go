package api

import (
	"fmt"
	"math/rand"
	"time"

	"pumpdump/backend/internal/binance"
)

// Idea search.
//
// The parameter ladder answers "which lookback", but the more interesting
// question is "which combination". This samples blends of the component pool —
// two to five members at a time — scores each idea on the most recent bars, and
// reports it. It never stops on its own: the point is to keep proposing ideas
// until a human says otherwise, so nothing here declares victory.

// ideaPool is the set of components ideas are drawn from.
func ideaPool() []string {
	names := []string{"naive"}
	for _, c := range superComponents() {
		if c.name != "naive" {
			names = append(names, c.name)
		}
	}
	return names
}

// blendOf builds an equal-weight blend of the named components.
func blendOf(bars []binance.Kline, horizon int, names []string) ([]float64, bool) {
	byName := map[string]superComponent{}
	for _, c := range superComponents() {
		byName[c.name] = c
	}
	out := make([]float64, horizon)
	var used int
	for _, n := range names {
		c, ok := byName[n]
		if !ok {
			continue
		}
		path, ok := c.make(bars, horizon)
		if !ok || len(path) != horizon {
			continue
		}
		for i := 0; i < horizon; i++ {
			out[i] += path[i]
		}
		used++
	}
	if used == 0 {
		return nil, false
	}
	for i := range out {
		out[i] /= float64(used)
	}
	return out, true
}

// scoreIdea grades an idea on the most recent horizon bars.
func scoreIdea(bars []binance.Kline, horizon int, names []string) (float64, float64, bool) {
	cut := len(bars) - horizon
	if cut < 80 {
		return 0, 0, false
	}
	path, ok := blendOf(bars[:cut], horizon, names)
	if !ok {
		return 0, 0, false
	}
	var sum float64
	var n int
	for i := 0; i < horizon; i++ {
		actual := bars[cut+i].Close
		if actual <= 0 {
			continue
		}
		sum += absf(path[i]-actual) / actual
		n++
	}
	if n == 0 {
		return 0, 0, false
	}
	mape := 100 * sum / float64(n)
	return mape, 1 - mape/100, true
}

func absf(v float64) float64 {
	if v < 0 {
		return -v
	}
	return v
}

// exploreOnce proposes one idea at random and returns its label and score.
func (e *liveEngine) exploreOnce(symbol string, bars []binance.Kline, horizon int, seed int64) (string, float64, float64, bool) {
	pool := ideaPool()
	if len(pool) < 3 {
		return "", 0, 0, false
	}
	rng := rand.New(rand.NewSource(seed))
	k := 2 + rng.Intn(4) // two to five members
	if k > len(pool) {
		k = len(pool)
	}
	picked := make([]string, 0, k)
	seen := map[string]bool{}
	for len(picked) < k {
		idx := rng.Intn(len(pool))
		if seen[pool[idx]] {
			continue
		}
		seen[pool[idx]] = true
		picked = append(picked, pool[idx])
	}
	mape, r2, ok := scoreIdea(bars, horizon, picked)
	if !ok {
		return "", 0, 0, false
	}
	label := ""
	for i, p := range picked {
		if i > 0 {
			label += "+"
		}
		label += p
	}
	return label, mape, r2, true
}

// recordIdea publishes one attempted idea.
func (e *liveEngine) recordIdea(symbol, label string, mape, r2 float64) {
	e.mu.Lock()
	defer e.mu.Unlock()
	st, ok := e.states[symbol]
	if !ok {
		return
	}
	st.Ideas = append(st.Ideas, tracePoint{Label: label, MAPE: mape, R2: r2})
	if len(st.Ideas) > 30 {
		st.Ideas = st.Ideas[len(st.Ideas)-30:]
	}
	st.Phase = "ideas"
	st.PhaseIndex = len(modelFamilies) + 1
	st.PhaseTotal = len(modelFamilies) + 1
	st.UpdatedMS = time.Now().UnixMilli()
}

var _ = fmt.Sprintf
