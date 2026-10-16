package flow

import "testing"

// The baseline must follow the QUIET end of the distribution, not the middle.
//
// This is the whole reason the signal works: a median ratchets upward as a symbol
// gets busier, so the ratio would FALL exactly when accumulation begins and the
// indicator would go blind at the moment it matters.
func TestOrderSizeBaselineIsLowQuantile(t *testing.T) {
	var b orderSizeBaseline
	// A quiet book at 100, then a burst of much larger prints.
	for i := 0; i < 20; i++ {
		b.observe(100)
	}
	for i := 0; i < 20; i++ {
		b.observe(1000)
	}
	ref := b.reference()
	if ref <= 0 {
		t.Fatal("baseline must be measurable after 40 observations")
	}
	// p25 of a half-100 half-1000 set is 100, NOT the median of 550.
	if ref > 200 {
		t.Errorf("baseline = %.2f, want the quiet end near 100; a median would ratchet to ~550 and blind the signal", ref)
	}
}

// Too few prints must report NOT MEASURABLE rather than a confident ratio.
func TestOrderSizeRefusesOnThinEvidence(t *testing.T) {
	s := &symbolFlow{}
	// One enormous print: an average over one print is not an average.
	s.taker = []takerPrint{{TS: 1000, Quote: 999999, Signed: 999999}}
	got := s.OrderSize(60, 2000)
	if got.Measured {
		t.Error("a ratio built on one print must not be reported as measured")
	}
	if got.Ratio != 0 {
		t.Errorf("ratio must be zero when unmeasurable, got %f", got.Ratio)
	}
	if got.Prints != 1 {
		t.Errorf("prints = %d, want 1 so the caller can see why it was refused", got.Prints)
	}
}

// A genuine accumulation signature: many prints, all larger than the symbol's
// usual size. The ratio must exceed 1.
func TestOrderSizeDetectsLargerPrints(t *testing.T) {
	s := &symbolFlow{}
	// Establish a quiet baseline of 100 per print.
	for i := 0; i < 20; i++ {
		s.orderSizeBaseline.observe(100)
	}
	// Now a window of 20 prints at 400 each.
	for i := 0; i < 20; i++ {
		s.taker = append(s.taker, takerPrint{TS: 1900 + int64(i), Quote: 400, Signed: 400})
	}
	got := s.OrderSize(60, 2000)
	if !got.Measured {
		t.Fatalf("expected a measured ratio, got prints=%d baseline=%.2f", got.Prints, got.Baseline)
	}
	if got.Ratio <= 1.5 {
		t.Errorf("ratio = %.3f, want clearly above 1 for 4x larger prints", got.Ratio)
	}
	if got.AvgQuote != 400 {
		t.Errorf("avg = %.2f, want 400", got.AvgQuote)
	}
}

// Prints outside the window must not count, or the measurement silently becomes
// "since the process started" rather than "in the last N seconds".
func TestOrderSizeRespectsWindow(t *testing.T) {
	s := &symbolFlow{}
	for i := 0; i < 20; i++ {
		s.orderSizeBaseline.observe(100)
	}
	// Old prints only.
	for i := 0; i < 20; i++ {
		s.taker = append(s.taker, takerPrint{TS: 100 + int64(i), Quote: 5000, Signed: 5000})
	}
	got := s.OrderSize(60, 100000)
	if got.Prints != 0 {
		t.Errorf("prints = %d, want 0: all prints are outside the 60s window ending at 100000", got.Prints)
	}
}
