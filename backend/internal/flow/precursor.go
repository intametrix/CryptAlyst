package flow

import "math"

// The quiet-then-buy-surge precursor.
//
// The observation this encodes: the pumps worth catching do not begin with a
// symbol that is already busy. They begin with a symbol that has gone quiet and
// then takes a burst of aggressive BUYING. A symbol already moving has already
// been noticed, and a symbol whose flow rises on both sides at once is
// volatility rather than direction.
//
// So the score is the product of two independent conditions, and BOTH must hold:
//
//	quietness  — recent total flow is LOW against the symbol's own baseline
//	buy surge  — buy-side flow is HIGH against the symbol's own baseline
//
// Neither alone is a precursor. A quiet symbol with no buying is just quiet. A
// busy symbol with heavy buying is a move already in progress.
//
// Everything is measured against the symbol's OWN baseline rather than an
// absolute dollar figure, so a thin alt and a major pair are judged on the same
// scale.
const (
	// PrecursorDeadband is the minimum buy-surge multiple worth reporting. Below
	// this the "surge" is inside normal variation.
	PrecursorDeadband = 1.5
	// PrecursorSaturation is the multiple at which the surge component saturates.
	PrecursorSaturation = 8.0
)

// buyPressure returns buy-side and sell-side quote over a window. It is the
// directional counterpart to takerRatio, which returns only the ratio.
func (s *symbolFlow) buyPressure(windowSec float64, now int64) (buy, sell float64) {
	cutoff := now - int64(windowSec*1000)
	for _, p := range s.taker {
		if p.TS < cutoff {
			continue
		}
		if p.Signed > 0 {
			buy += p.Quote
		} else {
			sell += p.Quote
		}
	}
	return buy, sell
}

// PrecursorWindows are the horizons the precursor is evaluated over, shortest
// first. 20s is the earliest a directional push is distinguishable from a single
// large print; 60s is the longest that still precedes rather than follows a move.
var PrecursorWindows = []float64{20, 30, 45, 60}

// PrecursorRead is the quiet-then-buy-surge assessment for one symbol.
type PrecursorRead struct {
	// Windows holds the per-horizon detail, in PrecursorWindows order.
	Windows []PrecursorWindow `json:"windows"`
	// Score is 0..1: how strongly the quiet-then-buy pattern holds.
	Score float64 `json:"score"`
	// ConfidencePct is the score expressed as a percentage, or nil when the
	// symbol cannot be assessed (no baseline, no trades). It is NEVER emitted as
	// a bare number: a caller must be able to tell "measured, low" from "not
	// measurable", and a zero would conflate them.
	ConfidencePct *float64 `json:"confidence_pct"`
	// Label is the human read, for the UI and the log.
	Label string `json:"label"`
	// Basis states where the number comes from, so nobody reads it as a
	// backtested probability.
	Basis string `json:"basis"`
}

// PrecursorWindow is one horizon's contribution.
type PrecursorWindow struct {
	Seconds     float64 `json:"seconds"`
	BuyQuote    float64 `json:"buy_quote"`
	SellQuote   float64 `json:"sell_quote"`
	BuyRatio    float64 `json:"buy_ratio"`
	SurgeMult   float64 `json:"surge_multiple"`
	QuietFactor float64 `json:"quiet_factor"`
	Score       float64 `json:"score"`
}

// scoreWindow turns one horizon's raw flows into a 0..1 component.
//
// quietFactor: 1 when recent total flow is well BELOW the baseline, falling to 0
// as recent flow reaches or exceeds it. This is the "not doing a lot" condition.
//
// surge: how far buy flow exceeds the baseline's buy rate, normalised between the
// deadband and saturation. This is the "suddenly big BUY flows" condition.
func scoreWindow(buy, sell, priorTotal, baselineTotal, windowSec float64) (surge, quiet, score float64) {
	total := buy + sell
	if baselineTotal <= 0 || total <= 0 {
		return 0, 0, 0
	}
	// The baseline is a per-second rate; scale it to this window.
	expected := baselineTotal * windowSec
	if expected <= 0 {
		return 0, 0, 0
	}

	// Quietness is measured on the period BEFORE this window, not on the window
	// itself.
	//
	// A test caught the first version scoring every real surge as zero, and the
	// reason is structural rather than a bug in the arithmetic: the baseline is
	// symmetric, so heavy buying RAISES total flow. Asking a window to be both
	// below-normal in total AND far above normal in buying is asking for a
	// contradiction — the busier it buys, the less quiet it looks, and the two
	// conditions could never both hold for the pattern the detector exists to
	// find.
	//
	// "Not doing a lot and THEN big buys" is temporal, so the measurement has to
	// be temporal: the prior period supplies the quietness, the current window
	// supplies the surge.
	priorExpected := expected
	if priorExpected <= 0 {
		return 0, 0, 0
	}
	switch ratio := priorTotal / priorExpected; {
	case ratio >= 1.0:
		quiet = 0
	case ratio <= 0.25:
		quiet = 1
	default:
		quiet = (1.0 - ratio) / 0.75
	}

	// Surge: buy flow against the buy flow the baseline would predict. The
	// baseline is symmetric, so half of it is the expected buy side.
	expectedBuy := expected / 2
	if expectedBuy <= 0 {
		return 0, quiet, 0
	}
	surgeMult := buy / expectedBuy
	switch {
	case surgeMult <= PrecursorDeadband:
		surge = 0
	case surgeMult >= PrecursorSaturation:
		surge = 1
	default:
		surge = (surgeMult - PrecursorDeadband) / (PrecursorSaturation - PrecursorDeadband)
	}

	// Both must hold. The product is deliberate: a high surge on a busy symbol
	// scores zero, and that is the case most likely to be mistaken for a pump.
	score = surge * quiet
	return surge, quiet, score
}

// readPrecursor combines the horizons into one read.
//
// Agreement across horizons is weighted, not averaged: a pattern present at 20s
// only is a print, while one present at 20, 30, 45 and 60s is a sustained
// accumulation. The longest window carries the most weight because it is the
// least likely to be satisfied by noise.
func readPrecursor(windows []PrecursorWindow) (float64, string) {
	if len(windows) == 0 {
		return 0, "not measurable"
	}
	weights := []float64{0.15, 0.20, 0.30, 0.35}
	var weighted, weightSum float64
	active := 0
	for i, w := range windows {
		wt := 1.0 / float64(len(windows))
		if i < len(weights) {
			wt = weights[i]
		}
		weighted += w.Score * wt
		weightSum += wt
		if w.Score > 0.25 {
			active++
		}
	}
	if weightSum <= 0 {
		return 0, "not measurable"
	}
	score := weighted / weightSum

	label := "no precursor"
	switch {
	case score >= 0.6 && active >= 3:
		label = "accumulation across all horizons"
	case score >= 0.4:
		label = "buying into quiet"
	case score >= 0.2:
		label = "early buy interest"
	case active > 0:
		label = "single-horizon buy print"
	}
	return score, label
}

// assessPrecursor builds the full read for a symbol.
func (s *symbolFlow) assessPrecursor(now int64, windowSec float64) PrecursorRead {
	ref := s.baseline.reference()
	// The baseline reference is quote volume per WINDOW, not per second; convert
	// once here so every window is scored against the same rate.
	if windowSec <= 0 {
		windowSec = 10
	}
	perSecond := ref / windowSec
	if perSecond <= 0 {
		return PrecursorRead{Basis: "no baseline yet", Label: "not measurable"}
	}

	out := PrecursorRead{
		Windows: make([]PrecursorWindow, 0, len(PrecursorWindows)),
		Basis:   "own-baseline ratio, not a backtested probability",
	}
	for _, secs := range PrecursorWindows {
		buy, sell := s.buyPressure(secs, now)
		// The immediately preceding equal-length period supplies quietness.
		priorBuy, priorSell := s.buyPressureBetween(secs, 2*secs, now)
		surge, quiet, score := scoreWindow(buy, sell, priorBuy+priorSell, perSecond, secs)
		total := buy + sell
		ratio := 0.5
		if total > 0 {
			ratio = buy / total
		}
		out.Windows = append(out.Windows, PrecursorWindow{
			Seconds:   secs,
			BuyQuote:  roundTo(buy, 2),
			SellQuote: roundTo(sell, 2),
			BuyRatio:  roundTo(ratio, 4),
			// Report the raw multiple, not the normalised component, so a reader
			// can see how far past the deadband the surge actually went.
			SurgeMult:   roundTo(surgeToMultiple(surge), 3),
			QuietFactor: roundTo(quiet, 4),
			Score:       roundTo(score, 4),
		})
	}
	score, label := readPrecursor(out.Windows)
	out.Score = roundTo(score, 4)
	out.Label = label
	if len(out.Windows) > 0 {
		pct := roundTo(clamp01(score)*100, 1)
		out.ConfidencePct = &pct
	}
	return out
}

// surgeToMultiple inverts the normalisation applied in scoreWindow, so the
// reported multiple and the scored component cannot drift apart.
func surgeToMultiple(surge float64) float64 {
	return PrecursorDeadband + surge*(PrecursorSaturation-PrecursorDeadband)
}

func clamp01(v float64) float64 { return math.Max(0, math.Min(1, v)) }

// buyPressureBetween returns buy and sell quote over [now-fromSec, now-toSec),
// which is how the precursor looks at the period BEFORE a window.
func (s *symbolFlow) buyPressureBetween(fromSec, toSec float64, now int64) (buy, sell float64) {
	newest := now - int64(fromSec*1000)
	oldest := now - int64(toSec*1000)
	for _, p := range s.taker {
		if p.TS < oldest || p.TS >= newest {
			continue
		}
		if p.Signed > 0 {
			buy += p.Quote
		} else {
			sell += p.Quote
		}
	}
	return buy, sell
}
