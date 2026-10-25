package graph

import "sort"

// sweep.go runs a parameter sweep: the same graph replayed over the same
// history with every combination of a grid. It is CPU-first and correct — the
// foundation the GPU agent later accelerates by running the same enumeration in
// parallel.

// Grid maps a parameter name to the values to try. The cartesian product of
// all parameters is the sweep space.
type Grid map[string][]float64

// SweepResult is one combination's backtest outcome.
type SweepResult struct {
	Params  map[string]float64 `json:"params"`
	HitRate float64            `json:"hit_rate"`
	Equity  float64            `json:"equity"`
	Signals int                `json:"signals"`
	Right   int                `json:"right"`
}

// Sweep enumerates every combination in grid, builds a graph per combination
// via build, replays it over contexts/closes, and returns the results sorted
// by hit rate descending (then equity).
func Sweep(
	build func(params map[string]float64) (*Graph, *VerdictNode),
	grid Grid,
	contexts []Context,
	closes []float64,
	ctx *Context,
	forward int,
) ([]SweepResult, error) {
	var out []SweepResult
	for _, combo := range combos(grid) {
		g, v := build(combo)
		bt := &Backtest{Graph: g, Ctx: ctx, Contexts: contexts, Closes: closes, Verdict: v, Forward: forward}
		res, err := bt.Run()
		if err != nil {
			return nil, err
		}
		out = append(out, SweepResult{
			Params:  combo,
			HitRate: res.HitRate,
			Equity:  res.Equity,
			Signals: res.Signals,
			Right:   res.Right,
		})
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].HitRate != out[j].HitRate {
			return out[i].HitRate > out[j].HitRate
		}
		return out[i].Equity > out[j].Equity
	})
	return out, nil
}

// combos returns the cartesian product of the grid as a slice of param maps.
// Parameter names are sorted so the enumeration order is deterministic.
func combos(grid Grid) []map[string]float64 {
	if len(grid) == 0 {
		return []map[string]float64{{}}
	}
	names := make([]string, 0, len(grid))
	for k := range grid {
		names = append(names, k)
	}
	sort.Strings(names)

	var out []map[string]float64
	var rec func(i int, cur map[string]float64)
	rec = func(i int, cur map[string]float64) {
		if i == len(names) {
			cp := make(map[string]float64, len(cur))
			for k, v := range cur {
				cp[k] = v
			}
			out = append(out, cp)
			return
		}
		for _, v := range grid[names[i]] {
			cur[names[i]] = v
			rec(i+1, cur)
		}
	}
	rec(0, map[string]float64{})
	return out
}
