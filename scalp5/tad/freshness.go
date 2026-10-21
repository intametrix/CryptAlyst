// freshness.go — the signal-completion freshness bookkeeping for the pacer.
//
// Each per-symbol decision input is tracked as {age_s, stale, src_ok}:
//   - age_s: seconds since the last observation of that input for the symbol.
//   - stale: age_s exceeds the input's max age (the freshness table), or never seen.
//   - src_ok: the input's GLOBAL source topic advanced within srcWin — this is the
//     quiet-vs-broken split: stale + src_ok = quiet market (no demotion beyond the
//     missing evidence), stale + !src_ok = source silent (broken, demote).
package main

import "sync"

type inputState struct {
	AgeS  float64 `json:"age_s"`
	Stale bool    `json:"stale"`
	SrcOK bool    `json:"src_ok"`
}

type freshness struct {
	mu      sync.Mutex
	maxAge  map[string]int64            // per-input max age (ms)
	symSeen map[string]map[string]int64 // per-symbol per-input last-seen (ms)
	topSeen map[string]int64            // per-input global last-seen (ms)
	srcWin  int64                       // global source liveness window (ms)
}

// maxAgeMS is the freshness table (owner/KIMI ruling): each input's max age
// before it reads stale. OI and book_imbalance are the slow 30s poll.
var maxAgeMS = map[string]int64{
	"price":          2_000,
	"ta":             60_000,
	"forecast":       300_000,
	"flow":           30_000,
	"oi":             30_000,
	"book_imbalance": 30_000,
}

func newFreshness() *freshness {
	return &freshness{
		maxAge:  maxAgeMS,
		symSeen: map[string]map[string]int64{},
		topSeen: map[string]int64{},
		srcWin:  120_000,
	}
}

// note records an observation of input for sym at ts (epoch ms). It also advances
// the input's global source clock, which is what src_ok reads.
func (f *freshness) note(input, sym string, ts int64) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.symSeen[sym] == nil {
		f.symSeen[sym] = map[string]int64{}
	}
	if ts > f.symSeen[sym][input] {
		f.symSeen[sym][input] = ts
	}
	if ts > f.topSeen[input] {
		f.topSeen[input] = ts
	}
}

// snapshot computes the per-input freshness for sym at now (epoch ms).
func (f *freshness) snapshot(sym string, now int64) map[string]inputState {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := make(map[string]inputState, len(f.maxAge))
	for input, max := range f.maxAge {
		seen := f.symSeen[sym][input]
		st := inputState{AgeS: -1, Stale: true, SrcOK: now-f.topSeen[input] <= f.srcWin}
		if seen != 0 {
			st.AgeS = float64(now-seen) / 1000.0
			st.Stale = now-seen >= max
		}
		out[input] = st
	}
	return out
}
