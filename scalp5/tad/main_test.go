package main

import "testing"

// The incremental gate: a quiet symbol (bar open time unchanged) is skipped, a
// live symbol (bar open time advanced) is recomputed. This is the owner ruling
// that TA must be per-symbol, driven by that symbol's own data, not a batch sweep.
func TestBarAdvanced(t *testing.T) {
	cases := []struct {
		lastT, newT int64
		want        bool
	}{
		{0, 100, true},    // first sighting -> compute
		{100, 100, false}, // same bar -> quiet, skip
		{100, 99, false},  // stale/out-of-order -> skip
		{100, 101, true},  // a new bar closed -> recompute
	}
	for _, c := range cases {
		if got := barAdvanced(c.lastT, c.newT); got != c.want {
			t.Fatalf("barAdvanced(%d, %d) = %v, want %v", c.lastT, c.newT, got, c.want)
		}
	}
}

// analyseFromBars must not panic on a short series, and must produce signals on a
// full series. A 60-bar synthetic up-trend yields at least one signal.
func TestAnalyseFromBars(t *testing.T) {
	short := make([]bar, 40)
	for i := range short {
		short[i] = bar{T: int64(i), O: 100, H: 101, L: 99, C: 100.5, V: 1}
	}
	if got := analyseFromBars("BTCUSDT", short); got != nil {
		t.Fatalf("40 bars should analyse to nil, got %d signals", len(got))
	}

	full := make([]bar, 80)
	for i := range full {
		full[i] = bar{T: int64(i), O: 100 + float64(i), H: 101 + float64(i),
			L: 99 + float64(i), C: 100.5 + float64(i), V: 1}
	}
	sigs := analyseFromBars("BTCUSDT", full)
	if len(sigs) == 0 {
		t.Fatalf("80 bars should produce signals, got none")
	}
}
