package flow

import "math"

// Average order size, and its ratio against the symbol's own baseline.
//
// THE FINDING THIS COMES FROM. The ACM study of pump-day order statistics reports
// an "Order Size Increase Ratio": average order size on the pump day against the
// 7-day average. It is the most directly actionable feature found in the external
// research, and it measures something this engine could not see before.
//
// WHY IT IS NOT REDUNDANT WITH VOLUME. Every volume gate in this system sums
// QUOTE VALUE, which is blind to how that value arrived. Ten thousand USDT of
// buying is one whale order or five hundred retail orders, and the flow
// measurement scores them identically. They are not the same event:
//
//	LARGE average order size  = few participants moving size. Accumulation.
//	                          The condition that precedes a pump.
//	SMALL average order size  = many participants moving small amounts. Retail
//	                          attention, which is what a pump ATTRACTS, and
//	                          therefore late.
//
// So the ratio is a PRE-PUMP indicator in a way that aggregate volume is not, and
// it is exactly the kind of thing this system's onset bias should be looking at.
//
// BASELINE DISCIPLINE, matching the rest of the engine: measured against the
// symbol's own history rather than an absolute dollar figure, so a thin alt and a
// major pair are judged alike, and floored so a symbol cannot go blind.

// OrderSizeStats is the average-print measurement for one window.
type OrderSizeStats struct {
	// Prints is how many trades the average is built from. An average over three
	// prints is not an average, and the count is returned so a caller can refuse
	// to act on one.
	Prints int `json:"prints"`
	// AvgQuote is quote value per print.
	AvgQuote float64 `json:"avg_quote"`
	// Baseline is the symbol's own typical print size.
	Baseline float64 `json:"baseline_quote"`
	// Ratio is AvgQuote / Baseline. Above 1 means larger prints than usual.
	Ratio float64 `json:"ratio"`
	// Measured is false when there are too few prints for the ratio to mean
	// anything. Reported rather than omitted so "not measurable" is distinguishable
	// from "measured, and normal".
	Measured bool `json:"measured"`
}

// MinPrintsForOrderSize is the floor below which an average order size is noise.
// Twelve is chosen because a single whale print into eleven retail ones still
// moves the average materially, and anything below that is dominated by whichever
// one print happened to be largest.
const MinPrintsForOrderSize = 12

// OrderSize measures average print size over a window against the symbol's own
// baseline.
func (s *symbolFlow) OrderSize(windowSec float64, now int64) OrderSizeStats {
	cutoff := now - int64(windowSec*1000)
	var quote float64
	prints := 0
	for _, p := range s.taker {
		if p.TS < cutoff {
			continue
		}
		quote += p.Quote
		prints++
	}
	out := OrderSizeStats{Prints: prints}
	if prints == 0 {
		return out
	}
	out.AvgQuote = roundTo(quote/float64(prints), 4)

	base := s.orderSizeBaseline.reference()
	if base <= 0 || prints < MinPrintsForOrderSize {
		return out
	}
	out.Baseline = roundTo(base, 4)
	out.Ratio = roundTo(out.AvgQuote/base, 4)
	out.Measured = true
	return out
}

// orderSizeBaseline is a p25-floored running baseline of average print size.
//
// It follows the same discipline as the volume baseline, and for the same reason:
// a MEDIAN ratchets upward as a symbol gets busier, which would make the ratio
// fall exactly when accumulation begins and blind the signal at the moment it
// matters. A p25 floor keeps the reference anchored to the symbol's quiet state,
// which is the state a pump starts from.
type orderSizeBaseline struct {
	samples []float64
}

func (b *orderSizeBaseline) observe(avgQuote float64) {
	if avgQuote <= 0 {
		return
	}
	b.samples = append(b.samples, avgQuote)
	if len(b.samples) > 240 {
		b.samples = b.samples[len(b.samples)-120:]
	}
}

func (b *orderSizeBaseline) reference() float64 {
	n := len(b.samples)
	if n < 8 {
		return 0
	}
	sorted := append([]float64(nil), b.samples...)
	sortFloats(sorted)
	// p25: the quiet end, not the middle.
	idx := int(math.Round(0.25 * float64(n-1)))
	if idx < 0 {
		idx = 0
	}
	if idx >= n {
		idx = n - 1
	}
	return sorted[idx]
}

func (b *orderSizeBaseline) ready() bool { return len(b.samples) >= 8 }

// sortFloats is a small insertion sort: the slice is at most 240 long, called at
// most once per symbol per tick, and an allocation for sort.Float64s would cost
// more than the sort.
func sortFloats(v []float64) {
	for i := 1; i < len(v); i++ {
		for j := i; j > 0 && v[j] < v[j-1]; j-- {
			v[j], v[j-1] = v[j-1], v[j]
		}
	}
}
