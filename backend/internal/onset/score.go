// Package onset turns a flow candidate into a scored, explainable alert.
//
// Scoring answers one question: how confident are we that a sharp move is
// starting right now? Four capped axes sum to 100, and every axis is echoed
// into the alert so a human can audit it:
//
//	flow burst  30  how abnormal the volume is vs this symbol's own history
//	speed       25  how fast price is moving, in units of its own volatility
//	aggression  25  taker-side imbalance: is someone actually hitting the offer
//	quality     20  liquidity, spread, book depth -- is this a real market
//
// A lateness penalty is then subtracted when the 60s move already dwarfs the
// window move, which is the signature of arriving late rather than early.
package onset

import (
	"math"

	"pumpdump/backend/internal/flow"
)

// Kinds.
const (
	Pump         = "PUMP"
	Dump         = "DUMP"
	ReversalDump = "REVERSAL_DUMP"
	ReversalPump = "REVERSAL_PUMP"
)

// Severity thresholds.
const (
	CriticalScore = 75.0
	AlertScore    = 55.0
)

// Result is a scored candidate.
type Result struct {
	Score        float64
	Severity     string
	Components   map[string]float64
	Reasons      []string
	Penalty      float64
	AggConfirmed bool
}

// SeverityFor maps a score to a severity label.
func SeverityFor(score float64) string {
	switch {
	case score >= CriticalScore:
		return "CRITICAL"
	case score >= AlertScore:
		return "ALERT"
	default:
		return "WATCH"
	}
}

// KindFor maps a direction to a kind.
func KindFor(direction int) string {
	if direction > 0 {
		return Pump
	}
	return Dump
}

// Score evaluates a candidate. `aggQuote` is the taker-side quote volume
// collected for this symbol (0 when no trade-level data has been gathered).
func Score(c flow.Candidate, cfg flow.Config, aggQuote float64) Result {
	direction := c.Direction
	reasons := append([]string{}, c.Reasons...)
	components := make(map[string]float64, 4)

	// --- flow burst (max 30) ---------------------------------------------
	gate := math.Max(cfg.MinBurstMultiple, 0.1)
	ratio := c.BurstMultiple / gate
	burst := 20 * clamp01((ratio-1)/6)
	if cfg.MinBurstZ > 0 {
		burst += 10 * clamp01(c.BurstZ/(2*cfg.MinBurstZ))
	} else {
		burst += 10 * clamp01((ratio-1)/6)
	}
	components["flow"] = math.Min(burst, 30)

	// --- speed (max 25) --------------------------------------------------
	sigmaPoints := 25 * clamp01(c.SigmaMultiple/(3*math.Max(cfg.MinSigmaMultiple, 0.1)))
	move := 0.0
	if c.Change10s != nil {
		move = math.Abs(*c.Change10s)
	}
	movePoints := 25 * clamp01((move*100)/(3*math.Max(cfg.MinMovePct, 0.01)))
	components["speed"] = math.Max(sigmaPoints, movePoints)

	// --- aggression (max 25) ---------------------------------------------
	alignedTaker := c.TakerBuyRatio
	if direction < 0 {
		alignedTaker = 1 - c.TakerBuyRatio
	}
	if aggQuote > 0 {
		components["aggression"] = 25 * clamp01((alignedTaker-0.5)/0.22)
		side := "buy"
		if direction < 0 {
			side = "sell"
		}
		reasons = append(reasons, "taker "+side+" "+pct0(alignedTaker)+" of $"+human(aggQuote)+" traded")
	} else {
		// No trade-level feed yet: where price closed in its own window range
		// is a weaker but usable proxy.
		alignedRange := c.RangePosition
		if direction < 0 {
			alignedRange = 1 - c.RangePosition
		}
		components["aggression"] = 25 * clamp01((alignedRange-0.5)/0.45) * 0.6
		reasons = append(reasons, "no taker feed; window close position "+pct0(alignedRange))
	}

	// --- quality (max 20) ------------------------------------------------
	liquidity := 10 * clamp01(c.QuoteVolume24h/(10*math.Max(cfg.MinAbsFlow, 1)))
	spread := 0.0
	depth := 0.0
	if !c.BookMissing {
		spread = 6 * clamp01(1-c.SpreadPct/math.Max(cfg.MaxSpreadPct, 1e-9))
		depth = 4 * clamp01(c.BookDepthUSD/(20*math.Max(cfg.MinBookDepthUSD, 1)))
	} else {
		// No usable top-of-book: SpreadPct is 0 because the data is absent,
		// not because the book is tight -- absent data earns nothing rather
		// than full spread/depth credit.
		reasons = append(reasons, "no book data; spread/depth unscored")
	}
	components["quality"] = math.Min(liquidity+spread+depth, 20)

	// --- lateness penalty -------------------------------------------------
	penalty := 0.0
	if c.Change60s != nil && move > 0 {
		lateness := math.Abs(*c.Change60s) / move
		if lateness > 3 {
			penalty = math.Min((lateness-3)*4, 20)
			reasons = append(reasons, "late: 60s move already "+signedPct(*c.Change60s))
		}
	}
	if c.Acceleration != nil && *c.Acceleration < 1 {
		penalty += math.Min((1-*c.Acceleration)*8, 8)
		reasons = append(reasons, "flow decelerating ("+f2(*c.Acceleration)+"x prior window)")
	}

	total := 0.0
	for _, v := range components {
		total += v
	}
	score := math.Max(0, math.Min(100, total-penalty))
	return Result{
		Score:        score,
		Severity:     SeverityFor(score),
		Components:   components,
		Reasons:      reasons,
		Penalty:      penalty,
		AggConfirmed: aggQuote > 0 && alignedTaker >= 0.55,
	}
}

func clamp01(v float64) float64 { return math.Max(0, math.Min(1, v)) }

func pct0(fraction float64) string { return f0(fraction*100) + "%" }

func f0(v float64) string { return trimFloat(v, 0) }
func f2(v float64) string { return trimFloat(v, 2) }

func signedPct(fraction float64) string {
	sign := ""
	if fraction > 0 {
		sign = "+"
	}
	return sign + trimFloat(fraction*100, 2) + "%"
}

func trimFloat(v float64, digits int) string {
	return strconvFormat(v, digits)
}
