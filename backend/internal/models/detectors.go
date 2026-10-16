package models

import (
	"context"
	"math"
	"time"
)

// The models. Each one asks a DIFFERENT question about the same market, and they
// are deliberately not variations of one idea -- a set of six models that all
// measure volatility expansion would look like six confirmations while being one
// opinion repeated.
//
//	VolatilityExpansion  the range is expanding from a quiet base
//	VolumeClimax         participation spiked, which is often exhaustion
//	StructureBreak       price is making progress one way, not chopping
//	FlowImbalance        aggressive order flow is one-sided
//	MeanReversion        price is stretched far from its recent range
//	MomentumDivergence   price and flow disagree, which precedes turns
//
// The three the human named for dumps -- volatility, price action, volume -- are
// the first three, and each is reported independently so a dump carried by volume
// alone is not presented as a dump confirmed three ways.

// ------------------------------------------------------------------ 1. VOLATILITY

type VolatilityExpansion struct{}

func (VolatilityExpansion) Name() string            { return "volatility-expansion" }
func (VolatilityExpansion) Interval() time.Duration { return time.Second }
func (VolatilityExpansion) Evaluate(_ context.Context, s Snapshot) []Signal {
	var out []Signal
	for _, r := range s.Rows {
		if r.Price <= 0 || r.SigmaMultiple <= 0 {
			continue
		}
		// The volatility model is symmetric by design: expanding range has no
		// direction, and pretending it does is how a volatility signal gets
		// mistaken for a directional one.
		score := sat(r.SigmaMultiple, 1.0, 3.0)
		if score < 0.4 {
			continue
		}
		dir := "NONE"
		switch {
		case r.Change10s < 0:
			dir = "DUMP"
		case r.Change10s > 0:
			dir = "PUMP"
		}
		out = append(out, Signal{
			Symbol: r.Symbol, Direction: dir, Score: round4(score),
			Reasons: []string{"range expanding"},
			Detail:  map[string]float64{"sigma_multiple": round4(r.SigmaMultiple), "change_10s": round4(r.Change10s)},
		})
	}
	return out
}

// ------------------------------------------------------------------ 2. VOLUME

type VolumeClimax struct{}

func (VolumeClimax) Name() string            { return "volume-climax" }
func (VolumeClimax) Interval() time.Duration { return time.Second }
func (VolumeClimax) Evaluate(_ context.Context, s Snapshot) []Signal {
	var out []Signal
	for _, r := range s.Rows {
		if r.Price <= 0 {
			continue
		}
		// 1.5 was far too low: many symbols sit above 1.5x routinely, so the model
		// reported normal activity as a climax -- 415 signals in 54 ticks against
		// 109 symbols, almost all directionless. 3.0 is the level at which
		// participation is genuinely exceptional rather than merely present.
		score := sat(r.BurstMultiple, 3.0, 8.0)
		if score < 0.4 {
			continue
		}
		dir := "NONE"
		switch {
		case r.TakerBuyRatio < 0.45:
			dir = "DUMP"
		case r.TakerBuyRatio > 0.55:
			dir = "PUMP"
		}
		// A climax with NO direction is not actionable and it drowns the models
		// that are. Balanced flow during a burst is volatility, not a signal, and
		// the volatility-expansion model already reports that case on purpose.
		if dir == "NONE" {
			continue
		}
		out = append(out, Signal{
			Symbol: r.Symbol, Direction: dir, Score: round4(score),
			Reasons: []string{"participation spike"},
			Detail: map[string]float64{
				"burst_multiple": round4(r.BurstMultiple),
				"burst_z":        round4(r.BurstZ),
				"buy_ratio":      round4(r.TakerBuyRatio),
			},
		})
	}
	return out
}

// ------------------------------------------------------------------ 3. STRUCTURE

type StructureBreak struct{}

func (StructureBreak) Name() string            { return "structure-break" }
func (StructureBreak) Interval() time.Duration { return 2 * time.Second }
func (StructureBreak) Evaluate(_ context.Context, s Snapshot) []Signal {
	var out []Signal
	for _, r := range s.Rows {
		if r.Price <= 0 || r.High24h <= r.Low24h {
			continue
		}
		// Price action: where price sits in its range, and whether the short and
		// medium moves AGREE. A single-horizon move is a print; two agreeing is
		// progress.
		agree := r.Change10s != 0 && r.Change60s != 0 &&
			((r.Change10s < 0) == (r.Change60s < 0))
		if !agree {
			continue
		}
		mag := math.Abs(r.Change10s) + math.Abs(r.Change60s)
		score := sat(mag, 0.10, 1.20)
		dir := "PUMP"
		extreme := sat(r.RangePosition, 0.5, 1.0)
		if r.Change10s < 0 {
			dir = "DUMP"
			// A dump closing at its LOW is still going; one mid-range has stalled.
			extreme = sat(1.0-r.RangePosition, 0.5, 1.0)
		}
		score = score * (0.5 + 0.5*extreme)
		if score < 0.4 {
			continue
		}
		out = append(out, Signal{
			Symbol: r.Symbol, Direction: dir, Score: round4(score),
			Reasons: []string{"short and medium moves agree", "closing at a range extreme"},
			Detail: map[string]float64{
				"change_10s": round4(r.Change10s), "change_60s": round4(r.Change60s),
				"range_position": round4(r.RangePosition),
			},
		})
	}
	return out
}

// ------------------------------------------------------------------ 4. FLOW

type FlowImbalance struct{}

func (FlowImbalance) Name() string            { return "flow-imbalance" }
func (FlowImbalance) Interval() time.Duration { return time.Second }
func (FlowImbalance) Evaluate(_ context.Context, s Snapshot) []Signal {
	var out []Signal
	for _, r := range s.Rows {
		if r.Price <= 0 || r.TakerQuote <= 0 {
			continue
		}
		// Measured from taker prints. When the buffer is empty the ratio is 0.5 and
		// this model correctly declines -- which is exactly the state that produced
		// silent zeroes elsewhere, so the guard is deliberate and the reason is
		// named.
		sell := sat(0.5-r.TakerBuyRatio, 0.0, 0.15)
		buy := sat(r.TakerBuyRatio-0.5, 0.0, 0.20)
		score := math.Max(sell, buy)
		if score < 0.4 {
			continue
		}
		dir := "PUMP"
		if sell > buy {
			dir = "DUMP"
		}
		out = append(out, Signal{
			Symbol: r.Symbol, Direction: dir, Score: round4(score),
			Reasons: []string{"aggressive flow one-sided"},
			Detail: map[string]float64{
				"buy_ratio": round4(r.TakerBuyRatio), "taker_quote": round4(r.TakerQuote),
			},
		})
	}
	return out
}

// ------------------------------------------------------------------ 5. MEAN REVERSION

// MeanReversion is the model that exists because of the human's own observation:
// alerts in a high-volatility symbol tend to MEAN REVERT rather than continue.
// It reports the opposite direction to the move, which is the point -- it is the
// counterweight to the momentum models rather than another vote for them.
type MeanReversion struct{}

func (MeanReversion) Name() string            { return "mean-reversion" }
func (MeanReversion) Interval() time.Duration { return 2 * time.Second }
func (MeanReversion) Evaluate(_ context.Context, s Snapshot) []Signal {
	var out []Signal
	for _, r := range s.Rows {
		if r.Price <= 0 || r.SigmaMultiple <= 0 {
			continue
		}
		// Stretched: a large move AND an expanded range AND price at an extreme.
		mag := math.Abs(r.Change10s)
		stretch := sat(mag, 0.30, 1.50)
		vol := sat(r.SigmaMultiple, 1.5, 4.0)
		atExtreme := math.Max(sat(r.RangePosition, 0.7, 1.0), sat(1.0-r.RangePosition, 0.7, 1.0))
		score := (0.45*stretch + 0.35*vol + 0.20*atExtreme) * atExtreme
		if score < 0.4 {
			continue
		}
		// The signal is the REVERSAL direction, deliberately.
		dir := "DUMP"
		if r.Change10s < 0 {
			dir = "PUMP"
		}
		out = append(out, Signal{
			Symbol: r.Symbol, Direction: dir, Score: round4(score),
			Reasons: []string{"overextended against an expanded range", "fade, not continuation"},
			Detail: map[string]float64{
				"change_10s": round4(r.Change10s), "sigma_multiple": round4(r.SigmaMultiple),
				"range_position": round4(r.RangePosition),
			},
		})
	}
	return out
}

// ------------------------------------------------------------------ 6. DIVERGENCE

type MomentumDivergence struct{}

func (MomentumDivergence) Name() string            { return "momentum-divergence" }
func (MomentumDivergence) Interval() time.Duration { return 3 * time.Second }
func (MomentumDivergence) Evaluate(_ context.Context, s Snapshot) []Signal {
	var out []Signal
	for _, r := range s.Rows {
		if r.Price <= 0 || r.TakerQuote <= 0 || r.Change10s == 0 {
			continue
		}
		// Price rose while aggressive flow was SELLING, or fell while it was
		// BUYING. Both are absorption: someone large is taking the other side, and
		// the move is being supplied into.
		priceUp := r.Change10s > 0
		flowUp := r.TakerBuyRatio > 0.5
		if priceUp == flowUp {
			continue
		}
		score := sat(math.Abs(r.Change10s), 0.10, 0.80) *
			sat(math.Abs(r.TakerBuyRatio-0.5), 0.03, 0.15)
		if score < 0.35 {
			continue
		}
		// Divergence implies the price move is not supported, so it fades.
		dir := "DUMP"
		if !priceUp {
			dir = "PUMP"
		}
		out = append(out, Signal{
			Symbol: r.Symbol, Direction: dir, Score: round4(score),
			Reasons: []string{"price and aggressive flow disagree", "absorption"},
			Detail: map[string]float64{
				"change_10s": round4(r.Change10s), "buy_ratio": round4(r.TakerBuyRatio),
			},
		})
	}
	return out
}

// All returns every model, which is what the runner registers.
func All() []Model {
	return []Model{
		VolatilityExpansion{},
		VolumeClimax{},
		StructureBreak{},
		FlowImbalance{},
		MeanReversion{},
		MomentumDivergence{},
	}
}

func sat(v, lo, hi float64) float64 {
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

func round4(v float64) float64 { return math.Round(v*10000) / 10000 }
