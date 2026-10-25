package detect

// BullsAndBears is a directional view over the live flow engine. It does not
// detect a single burst; it scores every tracked symbol as a bull (upward
// pressure) or a bear (downward pressure) from three explainable axes — flow
// burst, recent price move, and taker aggression — and emits a signal when the
// score crosses a threshold. It is deliberately a view, not a trade: it names
// the bull and the bear, and leaves the decision to the reader.

import (
	"fmt"
	"math"
	"sync"

	"pumpdump/backend/internal/flow"
	"pumpdump/backend/internal/onset"
)

// BullsAndBears default threshold.
const defaultBullBearScore = 60.0

// BullsAndBears wraps the flow engine and emits bull/bear signals.
type BullsAndBears struct {
	engine   *flow.Engine
	minScore float64

	mu           sync.Mutex
	last         map[string]int64
	signals      int64
	lastSignalMS int64
}

// NewBullsAndBears creates the detector.
func NewBullsAndBears(engine *flow.Engine, minScore float64) *BullsAndBears {
	if minScore <= 0 {
		minScore = defaultBullBearScore
	}
	return &BullsAndBears{engine: engine, minScore: minScore, last: map[string]int64{}}
}

// Name implements Detector.
func (b *BullsAndBears) Name() string { return "bulls-and-bears" }

// Title implements Detector.
func (b *BullsAndBears) Title() string { return "BullsAndBears" }

// Description implements Detector.
func (b *BullsAndBears) Description() string {
	return "Directional bull/bear view from flow burst, recent price move and taker aggression."
}

// Batch implements Detector.
func (b *BullsAndBears) Batch() bool { return false }

// Observe implements Detector. The flow engine is fed by the FlowOnset detector,
// so this model only reads its snapshot on Evaluate.
func (b *BullsAndBears) Observe(ev Event) {}

// Params implements Detector.
func (b *BullsAndBears) Params() map[string]any {
	return map[string]any{"min_score": b.minScore}
}

// Evaluate implements Detector.
func (b *BullsAndBears) Evaluate(now int64) []Signal {
	candidates := b.engine.Snapshot(60, now)
	out := make([]Signal, 0, 8)
	for _, c := range candidates {
		bull, bear, score := bullBearScore(c)
		if score < b.minScore {
			continue
		}
		dir := 1
		kind := onset.Pump
		if bear > bull {
			dir = -1
			kind = onset.Dump
		}
		// One signal per symbol per cooldown window so a steady bull does not
		// repeat every scan.
		if !b.allow(c.Symbol, now) {
			continue
		}

		b.mu.Lock()
		b.signals++
		b.lastSignalMS = now
		b.mu.Unlock()

		change := 0.0
		if c.Change10s != nil {
			change = *c.Change10s
		}
		out = append(out, Signal{
			ID: fmt.Sprintf("bulls-and-bears|%s|%s|%d", c.Symbol, kind, now),
			Summary: fmt.Sprintf("%s %s bull=%.0f bear=%.0f (flow %.1fx, %+.2f%%/10s)",
				kind, c.Symbol, bull, bear, c.BurstMultiple, change*100),
			Detector:   b.Name(),
			Kind:       kind,
			Symbol:     c.Symbol,
			Direction:  dir,
			Severity:   onset.SeverityFor(score),
			Score:      score,
			Price:      c.Price,
			TSMS:       now,
			Reasons:    bullBearReasons(c, bull, bear),
			Components: map[string]float64{"bull": bull, "bear": bear},
			Metrics: map[string]any{
				"burst_multiple":   c.BurstMultiple,
				"burst_z":          c.BurstZ,
				"change_10s":       c.Change10s,
				"change_60s":       c.Change60s,
				"taker_buy_ratio":  c.TakerBuyRatio,
				"flow_quote":       c.FlowQuote,
				"quote_volume_24h": c.QuoteVolume24h,
				"sigma_multiple":   c.SigmaMultiple,
			},
		})
	}
	return out
}

// allow enforces a per-symbol cooldown.
func (b *BullsAndBears) allow(symbol string, now int64) bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	if last, ok := b.last[symbol]; ok && now-last < 60_000 {
		return false
	}
	b.last[symbol] = now
	return true
}

// bullBearScore scores a candidate as bull and bear on a 0-100 scale each.
func bullBearScore(c flow.Candidate) (bull, bear, total float64) {
	burst := clamp01(c.BurstMultiple / 8)
	move := 0.0
	if c.Change10s != nil {
		move = *c.Change10s
	}
	moveUp := clamp01(math.Max(0, move) / 0.005)
	moveDown := clamp01(math.Max(0, -move) / 0.005)
	takerBull := clamp01((c.TakerBuyRatio - 0.5) / 0.2)
	takerBear := clamp01((0.5 - c.TakerBuyRatio) / 0.2)

	bull = 100 * (0.5*burst + 0.3*moveUp + 0.2*takerBull)
	bear = 100 * (0.5*burst + 0.3*moveDown + 0.2*takerBear)
	total = math.Max(bull, bear)
	return bull, bear, total
}

func bullBearReasons(c flow.Candidate, bull, bear float64) []string {
	var r []string
	if bull >= bear {
		r = append(r, fmt.Sprintf("bull %.0f: flow %.1fx", bull, c.BurstMultiple))
	} else {
		r = append(r, fmt.Sprintf("bear %.0f: flow %.1fx", bear, c.BurstMultiple))
	}
	if c.TakerBuyRatio > 0.5 {
		r = append(r, fmt.Sprintf("taker %.0f%% buy", c.TakerBuyRatio*100))
	} else {
		r = append(r, fmt.Sprintf("taker %.0f%% sell", c.TakerBuyRatio*100))
	}
	return r
}

func clamp01(v float64) float64 {
	if v < 0 {
		return 0
	}
	if v > 1 {
		return 1
	}
	return v
}
