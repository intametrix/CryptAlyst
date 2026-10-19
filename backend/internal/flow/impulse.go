package flow

import "math"

// The impulse read: volatility, price action and volume, scored separately and
// then combined, with the DUMP side deliberately more sensitive than the pump side.
//
// WHY THE ASYMMETRY IS NOT A HACK. It is the most robust empirical fact about
// these markets and it is worth stating plainly: fear moves faster than greed.
// A dump is typically sharper, higher-volatility and more one-sided than the pump
// that preceded it, and it takes less net selling to move price down than it takes
// net buying to move it up, because the book thins as it falls. A detector tuned
// symmetrically is therefore systematically LATE on dumps -- it waits for a
// magnitude that a pump reaches slowly and a dump reaches in seconds.
//
// So the dump thresholds are lower, the volume term counts SELL dominance rather
// than a symmetric imbalance, and the volatility term is weighted more heavily
// because an expanding range is the single most reliable dump signature.
//
// THE THREE FACTORS, and why each is separate rather than folded into one number:
//
//	VOLATILITY   the range is expanding. Computed against the symbol's OWN recent
//	             dispersion, so a thin alt and a major pair are judged alike.
//	PRICE ACTION where price sits in its recent range, and whether the move is
//	             making progress in one direction (structure) or chopping.
//	VOLUME       is participation expanding, and is it one-sided.
//
// They are reported separately so a reader can see WHICH one fired. A dump score
// of 0.8 driven entirely by volume is a different proposition from one driven by
// all three, and collapsing them into a single number would hide that.

// ImpulseConfig holds the thresholds. Exposed so the asymmetry is visible and
// tunable rather than buried in constants.
type ImpulseConfig struct {
	// VolatilityExpansion is the sigma multiple at which the volatility term
	// saturates. Dumps reach it sooner, hence the separate value.
	PumpVolExpansion float64 `json:"pump_vol_expansion"`
	DumpVolExpansion float64 `json:"dump_vol_expansion"`
	// VolumeExpansion is the burst multiple at which the volume term saturates.
	PumpVolExpansionMult float64 `json:"pump_volume_expansion"`
	DumpVolExpansionMult float64 `json:"dump_volume_expansion"`
	// SideDominance is the taker imbalance at which the volume term saturates.
	// 0.65 means 65/35 one-sided.
	PumpSideDominance float64 `json:"pump_side_dominance"`
	DumpSideDominance float64 `json:"dump_side_dominance"`
	// MinScore is the combined score at which an impulse is worth reporting.
	PumpMinScore float64 `json:"pump_min_score"`
	DumpMinScore float64 `json:"dump_min_score"`
}

// DefaultImpulseConfig encodes the asymmetry.
//
// Every dump threshold is LOOSER than its pump counterpart. That is the whole
// point: dumps are detected earlier and on less evidence, because the cost of
// being late on a dump is larger than the cost of a false positive.
func DefaultImpulseConfig() ImpulseConfig {
	return ImpulseConfig{
		PumpVolExpansion:     3.0,
		DumpVolExpansion:     2.0, // reached sooner
		PumpVolExpansionMult: 4.0,
		DumpVolExpansionMult: 2.5, // less participation needed
		PumpSideDominance:    0.70,
		DumpSideDominance:    0.62, // thinner books fall on less selling
		PumpMinScore:         0.55,
		DumpMinScore:         0.45, // reported on weaker evidence
	}
}

// Impulse is one symbol's three-factor read.
type Impulse struct {
	Symbol string  `json:"symbol"`
	Price  float64 `json:"price"`

	// The three factors, each 0..1 and each reported separately.
	Volatility  float64 `json:"volatility_score"`
	PriceAction float64 `json:"price_action_score"`
	Volume      float64 `json:"volume_score"`

	// VolatilityDetail, PriceActionDetail and VolumeDetail carry the raw inputs so
	// the score can be audited rather than trusted.
	SigmaMultiple float64 `json:"sigma_multiple"`
	BurstMultiple float64 `json:"burst_multiple"`
	BuyRatio      float64 `json:"buy_ratio"`
	RangePosition float64 `json:"range_position"`
	ChangePct     float64 `json:"change_pct"`

	// DumpScore and PumpScore. Both are always computed -- a symbol can be
	// interesting in both directions at different strengths, and forcing a single
	// signed number would hide a genuine two-sided signal.
	DumpScore float64  `json:"dump_score"`
	PumpScore float64  `json:"pump_score"`
	Direction string   `json:"direction"` // DUMP, PUMP or NONE
	Score     float64  `json:"score"`
	Fired     bool     `json:"fired"`
	FiredBy   []string `json:"fired_by"`
	Note      string   `json:"note"`
}

// ImpulseConfigDefault is the package default, used when a caller supplies none.
var ImpulseConfigDefault = DefaultImpulseConfig()

// ReadImpulse scores one candidate on the three factors.
//
// Inputs come from what the engine already measures: Sigma10s and the baseline
// give volatility expansion, BurstMultiple gives participation expansion,
// TakerBuyRatio gives one-sidedness, and RangePosition plus Change10s give price
// action.
func ReadImpulse(c Candidate, cfg ImpulseConfig) Impulse {
	im := Impulse{
		Symbol: c.Symbol, Price: c.Price,
		SigmaMultiple: c.SigmaMultiple,
		BurstMultiple: c.BurstMultiple,
		BuyRatio:      c.TakerBuyRatio,
		RangePosition: c.RangePosition,
		ChangePct:     moveOr(c.Change10s),
	}

	// --- VOLATILITY ---------------------------------------------------------
	// An expanding range. Reported on its own because it is the earliest of the
	// three to move: dispersion rises before price has travelled far, which is
	// exactly the onset window this system exists to catch.
	im.Volatility = saturate(im.SigmaMultiple, 1.0, cfg.DumpVolExpansion)

	// --- VOLUME -------------------------------------------------------------
	// Participation expanding, multiplied by how one-sided it is. Both must be
	// present: a burst with balanced flow is volatility, not direction.
	participation := saturate(im.BurstMultiple, 1.2, cfg.DumpVolExpansionMult)
	// One-sidedness, measured from 0.5 (balanced) toward the dominance threshold.
	sellSide := saturate(0.5-im.BuyRatio, 0.0, cfg.DumpSideDominance-0.5)
	buySide := saturate(im.BuyRatio-0.5, 0.0, cfg.PumpSideDominance-0.5)
	im.Volume = participation * math.Max(sellSide, buySide)

	// --- PRICE ACTION -------------------------------------------------------
	// Two things at once: how far price has already travelled, and whether it is
	// closing at an extreme of its range. A move that has travelled far but sits
	// mid-range has stalled; one closing at the low is still going.
	travel := saturate(math.Abs(im.ChangePct), 0.05, 0.60)
	// RangePosition is 0 at the recent low, 1 at the high. A dump closes low.
	dumpExtreme := saturate(1.0-im.RangePosition, 0.5, 1.0)
	pumpExtreme := saturate(im.RangePosition, 0.5, 1.0)
	movingDown := im.ChangePct < 0
	movingUp := im.ChangePct > 0

	im.PriceAction = travel // the directional extreme is applied per-side below

	// --- COMBINE, with the asymmetry ----------------------------------------
	// Weights: volatility carries the most on the dump side, because an expanding
	// range is the most reliable dump signature and the earliest to appear.
	dumpVolW, dumpVolW2 := 0.45, 0.30
	dumpPriceW, dumpVoluW := 0.25, 0.30
	if movingDown {
		im.DumpScore = dumpVolW*im.Volatility + dumpPriceW*(travel*dumpExtreme) + dumpVoluW*im.Volume
	}
	im.PumpScore = dumpVolW2*im.Volatility + dumpPriceW*(travel*pumpExtreme) + dumpVoluW*im.Volume
	if movingUp {
		// Recompute the pump side with its own, stricter volatility normalisation.
		pumpVol := saturate(im.SigmaMultiple, 1.0, cfg.PumpVolExpansion)
		im.PumpScore = dumpVolW2*pumpVol + dumpPriceW*(travel*pumpExtreme) + dumpVoluW*im.Volume
	}

	// Report the stronger side, which is what the caller acts on.
	switch {
	case im.DumpScore >= im.PumpScore:
		im.Direction, im.Score = "DUMP", im.DumpScore
		im.Fired = im.DumpScore >= cfg.DumpMinScore
	default:
		im.Direction, im.Score = "PUMP", im.PumpScore
		im.Fired = im.PumpScore >= cfg.PumpMinScore
	}

	// Name which factors carried it, so a reader can see whether this is a
	// three-factor dump or a volume print with nothing behind it.
	if im.Volatility >= 0.5 {
		im.FiredBy = append(im.FiredBy, "volatility")
	}
	if im.PriceAction >= 0.5 {
		im.FiredBy = append(im.FiredBy, "price action")
	}
	if im.Volume >= 0.5 {
		im.FiredBy = append(im.FiredBy, "volume")
	}

	im.Note = "volatility, price action and volume scored separately; dump thresholds are looser than pump thresholds because fear moves faster than greed and thin books fall on less selling"
	return im
}

// saturate maps v onto 0..1 over [lo, hi], clamped.
func saturate(v, lo, hi float64) float64 {
	if hi <= lo {
		return 0
	}
	if v <= lo {
		return 0
	}
	if v >= hi {
		return 1
	}
	return (v - lo) / (hi - lo)
}
