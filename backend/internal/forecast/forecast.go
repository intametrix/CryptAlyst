// Package forecast projects where a symbol may revert to after an excursion.
//
// This is deliberately a small, transparent model rather than a learned one.
// A forecast line drawn on a chart is the most visually persuasive thing in the
// whole UI, so it is the easiest place to lie to the user by accident. Three
// rules follow:
//
//  1. **No forecast when there is nothing to revert.** If price is not
//     meaningfully displaced from its recent anchor, the function returns nil.
//     A flat line drawn through a flat market trains people to ignore the line.
//  2. **Every forecast states its basis.** The half-life is either measured
//     from this symbol's OWN prior excursions or it is a documented prior, and
//     the caller is told which, with the sample count. It never presents a
//     prior as if it were an estimate.
//  3. **Every forecast carries a band.** A single line implies precision the
//     model does not have, so the path is published with an ATR-derived
//     envelope and the confidence is a number the caller must display.
//
// The model is exponential decay from the current price toward the pre-move
// anchor. That is the simplest thing that captures "moves like this usually
// give a lot of it back, quickly at first and then slowly", and it is easy for
// a reader to reason about, which matters more here than sophistication.
package forecast

import (
	"math"
	"sort"

	"pumpdump/backend/internal/binance"
)

// Params controls the projection.
type Params struct {
	// AnchorLookbackBars is how far back the reference price is taken from.
	AnchorLookbackBars int
	// MinDisplacementPct is the move from the anchor required before a forecast
	// is worth drawing at all.
	MinDisplacementPct float64
	// ProjectBars is how many bars forward to project.
	ProjectBars int
	// PriorHalfLifeBars is used when this symbol has too little history.
	PriorHalfLifeBars float64
	// MinHistoryForEstimate is the number of prior excursions needed before the
	// measured half-life is used instead of the prior.
	MinHistoryForEstimate int
	// BandATRMultiple sets the envelope width from ATR at the current bar.
	BandATRMultiple float64
}

// DefaultParams returns the starting values.
func DefaultParams() Params {
	return Params{
		AnchorLookbackBars: 60,
		// 1.0% rather than 2.0%: on 1-minute bars a 2% move inside a 60-bar
		// hour is rare enough that the line almost never draws, and a feature
		// nobody ever sees is indistinguishable from a broken one.
		MinDisplacementPct:    1.0,
		ProjectBars:           40,
		PriorHalfLifeBars:     12,
		MinHistoryForEstimate: 3,
		BandATRMultiple:       1.0,
	}
}

// Point is one projected coordinate, with its envelope.
type Point struct {
	T     int64   `json:"t"`
	Value float64 `json:"value"`
	Lower float64 `json:"lower"`
	Upper float64 `json:"upper"`
}

// Forecast is a reversion projection.
type Forecast struct {
	Kind         string  `json:"kind"`
	Direction    int     `json:"direction"` // +1 reverting DOWN after a pump, -1 up after a dump
	Anchor       float64 `json:"anchor"`
	Current      float64 `json:"current"`
	Displacement float64 `json:"displacement_pct"`
	HalfLifeBars float64 `json:"half_life_bars"`
	Basis        string  `json:"basis"`
	Method       string  `json:"method"`
	Confidence   float64 `json:"confidence"`
	BandNote     string  `json:"band_note"`
	Points       []Point `json:"points"`
}

// Compute projects a reversion path, or returns nil when there is no excursion
// worth projecting.
func Compute(bars []binance.Kline, intervalMS int64, p Params) *Forecast {
	if len(bars) < p.AnchorLookbackBars+2 || intervalMS <= 0 {
		return nil
	}
	last := bars[len(bars)-1]
	current := last.Close
	anchorBar := bars[len(bars)-1-p.AnchorLookbackBars]
	anchor := anchorBar.Close
	if anchor <= 0 || current <= 0 {
		return nil
	}

	displacement := (current - anchor) / anchor * 100
	if math.Abs(displacement) < p.MinDisplacementPct {
		// Nothing to revert to. Drawing a line here would be noise.
		return nil
	}
	direction := 1 // reverting down (after a pump)
	if displacement < 0 {
		direction = -1 // reverting up (after a dump)
	}

	halfLife, basis := estimateHalfLife(bars, p)

	// Exponential decay from `current` toward `anchor`.
	path := make([]Point, 0, p.ProjectBars)
	atr := atr(bars, 20)
	band := math.Max(atr*p.BandATRMultiple, current*0.0005)
	for i := 1; i <= p.ProjectBars; i++ {
		decay := math.Pow(0.5, float64(i)/halfLife)
		value := anchor + (current-anchor)*decay
		// The band widens with the square root of the horizon: uncertainty
		// grows with time, but not linearly -- the anchor itself is the
		// strongest constraint the model has.
		widen := band * math.Sqrt(float64(i))
		path = append(path, Point{
			T:     last.OpenTime + intervalMS*int64(i),
			Value: round(value),
			Lower: round(value - widen),
			Upper: round(value + widen),
		})
	}

	return &Forecast{
		Kind:         "revert",
		Direction:    direction,
		Anchor:       round(anchor),
		Current:      round(current),
		Displacement: round(displacement),
		HalfLifeBars: round(halfLife),
		Basis:        basis,
		Method: "exponential decay toward the close " +
			itoa(p.AnchorLookbackBars) + " bars back",
		Confidence: confidence(displacement, halfLife, atr, current, p),
		BandNote:   "envelope is " + f1(p.BandATRMultiple) + "x ATR widening with the square root of the horizon",
		Points:     path,
	}
}

// estimateHalfLife measures how long this symbol's OWN prior excursions took to
// retrace half way, and falls back to a documented prior when it has too few.
//
// Presenting the prior as an estimate would be the single easiest way to
// mislead a reader, so the returned basis string says exactly which was used
// and how many observations backed it.
func estimateHalfLife(bars []binance.Kline, p Params) (float64, string) {
	lookback := p.AnchorLookbackBars
	observations := 0
	total := 0.0
	threshold := p.MinDisplacementPct / 100

	for start := 0; start+lookback+1 < len(bars); start++ {
		base := bars[start].Close
		if base <= 0 {
			continue
		}
		// Was there an excursion from `start` that later retraced half way?
		peakDelta := 0.0
		peakIndex := -1
		for j := start + 1; j < len(bars) && j <= start+lookback; j++ {
			delta := (bars[j].Close - base) / base
			if math.Abs(delta) > math.Abs(peakDelta) {
				peakDelta = delta
				peakIndex = j
			}
		}
		if peakIndex < 0 || math.Abs(peakDelta) < threshold {
			continue
		}
		halfTarget := base + (bars[peakIndex].Close-base)*0.5
		for k := peakIndex + 1; k < len(bars); k++ {
			reached := (peakDelta > 0 && bars[k].Close <= halfTarget) ||
				(peakDelta < 0 && bars[k].Close >= halfTarget)
			if reached {
				total += float64(k - peakIndex)
				observations++
				break
			}
		}
	}

	if observations >= p.MinHistoryForEstimate && total > 0 {
		halfLife := total / float64(observations)
		if halfLife < 1 {
			halfLife = 1
		}
		return halfLife, "measured from " + itoa(observations) + " prior excursions on this symbol"
	}
	return p.PriorHalfLifeBars,
		"prior " + f0(p.PriorHalfLifeBars) + " bars (only " + itoa(observations) + " prior excursions found, need " + itoa(p.MinHistoryForEstimate) + ")"
}

// confidence is a deliberately modest 0..1 number.
//
// It is NOT a probability that price will revert. It is a statement about how
// much this model should be trusted, and it is capped below 1.0 because a
// transparent two-parameter decay has no business claiming near-certainty.
func confidence(displacement, halfLife, atr, current float64, p Params) float64 {
	// Bigger displacement from the anchor -> more to revert -> more confident.
	magnitude := math.Min(1.0, math.Abs(displacement)/(p.MinDisplacementPct*3))
	// A shorter half-life relative to the horizon -> the model expects the move
	// to resolve within the drawn window, which is the case it is for.
	speed := math.Min(1.0, p.PriorHalfLifeBars/math.Max(halfLife, 0.5))
	// High volatility relative to price means the band dominates the signal.
	noise := 1.0
	if current > 0 && atr > 0 {
		noise = 1.0 - math.Min(1.0, (atr/current)*8)
	}
	raw := 0.5*magnitude + 0.3*speed + 0.2*noise
	return round(math.Max(0.05, math.Min(0.75, raw)))
}

func atr(bars []binance.Kline, period int) float64 {
	if len(bars) < 2 {
		return 0
	}
	n := period
	if len(bars) < n+1 {
		n = len(bars) - 1
	}
	trs := make([]float64, 0, n)
	for i := len(bars) - n; i < len(bars); i++ {
		prev := bars[i-1].Close
		trs = append(trs, math.Max(bars[i].High-bars[i].Low,
			math.Max(math.Abs(bars[i].High-prev), math.Abs(bars[i].Low-prev))))
	}
	if len(trs) == 0 {
		return 0
	}
	sort.Float64s(trs)
	mid := len(trs) / 2
	if len(trs)%2 == 1 {
		return trs[mid]
	}
	return (trs[mid-1] + trs[mid]) / 2
}

func round(v float64) float64 {
	if math.IsNaN(v) || math.IsInf(v, 0) {
		return 0
	}
	// Enough significant digits for a sub-cent token and a six-figure coin.
	scale := math.Pow(10, math.Max(0, 6-math.Floor(math.Log10(math.Abs(v)+1e-12))))
	return math.Round(v*scale) / scale
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

func f0(v float64) string { return trimFloat(v, 0) }
func f1(v float64) string { return trimFloat(v, 1) }

func trimFloat(v float64, digits int) string {
	scale := math.Pow(10, float64(digits))
	rounded := math.Round(v*scale) / scale
	return strconvFormat(rounded, digits)
}
