package flow

import "testing"

// TestScoreWindow encodes the pattern the detector exists to find, and the two
// cases it must reject.
//
// The first version of scoreWindow failed the "quiet then big buys" case, and the
// reason was structural rather than arithmetic: the baseline is symmetric, so
// heavy buying RAISES total flow, and asking one window to be both below-normal
// in total and far above normal in buying is a contradiction. Quietness is
// therefore measured on the PRIOR period, which is what "not doing a lot and
// THEN big buys" actually means.
func TestScoreWindow(t *testing.T) {
	const rate = 1000.0 // $1000/s baseline; expected buy over 20s = $10,000

	cases := []struct {
		name          string
		buy, sell     float64
		prior         float64
		wantQuiet     bool
		wantZeroScore bool
	}{
		// Prior period dead ($100 over 20s against $20,000 normal) and this window
		// buys 4x the normal buy rate. The pattern.
		{"quiet then big buys", 40000, 0, 100, true, false},
		// Quiet before, but no buying now. A quiet symbol is not a pump.
		{"quiet, no buying", 0, 200, 100, true, true},
		// Already busy before the window. This is the case most often mistaken for
		// a pump, and it is a move already in progress.
		{"busy with big buys", 40000, 40000, 40000, false, true},
		// Normal activity, balanced, normal prior.
		{"normal balanced", 10000, 10000, 20000, false, true},
	}

	for _, c := range cases {
		surge, quiet, score := scoreWindow(c.buy, c.sell, c.prior, rate, 20)
		if c.wantZeroScore && score != 0 {
			t.Errorf("%s: want zero score, got %.4f (surge=%.3f quiet=%.3f)", c.name, score, surge, quiet)
		}
		if !c.wantZeroScore && score <= 0 {
			t.Errorf("%s: want a positive score, got 0 (surge=%.3f quiet=%.3f)", c.name, surge, quiet)
		}
		if c.wantQuiet && quiet == 0 {
			t.Errorf("%s: expected a quiet factor > 0, got 0", c.name)
		}
		if !c.wantQuiet && quiet != 0 {
			t.Errorf("%s: expected quiet factor 0 (busy beforehand), got %.3f", c.name, quiet)
		}
	}
}

// A pattern present across all four horizons must outrank the same strength at
// 20s alone, because the longer horizons are the least likely to be noise.
func TestReadPrecursorWeightsLongHorizons(t *testing.T) {
	all := []PrecursorWindow{
		{Seconds: 20, Score: 0.7}, {Seconds: 30, Score: 0.7},
		{Seconds: 45, Score: 0.7}, {Seconds: 60, Score: 0.7},
	}
	one := []PrecursorWindow{
		{Seconds: 20, Score: 0.7}, {Seconds: 30, Score: 0},
		{Seconds: 45, Score: 0}, {Seconds: 60, Score: 0},
	}
	allScore, allLabel := readPrecursor(all)
	oneScore, _ := readPrecursor(one)
	if allScore <= oneScore {
		t.Errorf("all-horizon score %.4f must exceed single-horizon %.4f", allScore, oneScore)
	}
	if allLabel != "accumulation across all horizons" {
		t.Errorf("want the sustained label, got %q", allLabel)
	}
}

// An unassessable symbol must say so rather than report a confident zero.
func TestReadPrecursorEmpty(t *testing.T) {
	score, label := readPrecursor(nil)
	if score != 0 || label != "not measurable" {
		t.Errorf("empty windows: got %.4f %q, want 0 not measurable", score, label)
	}
}
