// Package heat scores how INTERESTING each symbol currently is, so the UI can
// glow a ticker in proportion to that score.
//
// The hard part is calibration, not computation. A score that is high most of
// the time is decoration: if half the board glows, the glow means nothing and
// the user learns to ignore it. Three decisions follow from that:
//
//  1. ONE channel carries ONE variable. The border brightness encodes a single
//     combined interestingness. The component breakdown exists to explain the
//     glow in a tooltip, not to be plotted.
//
//  2. Either source can light a ticker, because the requirement is "interesting
//     for price/volume action OR a technical-indicator signal". So heat is the
//     MAX of a live-attention term and an indicator-signal term -- not a sum,
//     which would let two mediocre readings masquerade as one strong one.
//
//  3. Only the LIVE term decays, with a short half-life. The kline-derived
//     terms refresh every 60s or 5 minutes, so decaying them would freeze the
//     score at whatever the last survey said and the glow would outlive the
//     move it was reporting.
//
// Two thresholds with hysteresis gate the display, so a symbol sitting on the
// boundary cannot strobe: it lights at AppearAt and only goes dark below
// DisappearBelow. A quiet hour should show zero to three glowing tickers.
package heat

import (
	"math"
	"sort"
	"sync"

	"pumpdump/backend/internal/flow"
)

// Params are the tunable calibration.
type Params struct {
	// Live attention: flow abnormality and price velocity.
	WeightFlow      float64 `json:"weight_flow"`
	WeightMove      float64 `json:"weight_move"`
	BurstMultipleAtFull float64 `json:"burst_multiple_at_full"`
	MoveAtFull10s       float64 `json:"move_at_full_10s"`
	MoveAtFull60s       float64 `json:"move_at_full_60s"`
	// MinFlowUSD scales the flow term down when the money involved is trivial,
	// so a high multiple on dust cannot light a ticker.
	MinFlowUSD float64 `json:"min_flow_usd"`

	// Indicator signal: trend strength, proximity to a structural level, and
	// range/RSI extremes.
	ADXFloor   float64 `json:"adx_floor"`
	ADXAtFull  float64 `json:"adx_at_full"`
	ExtensionAtFull float64 `json:"extension_at_full"`
	RangeProximityAtFull float64 `json:"range_proximity_at_full"`

	// Hysteresis, so the border cannot strobe on the threshold.
	AppearAt         float64 `json:"appear_at"`
	DisappearBelow   float64 `json:"disappear_below"`

	// LiveHalfLifeSec is the decay half-life of the LIVE term only.
	LiveHalfLifeSec float64 `json:"live_half_life_sec"`
}

// DefaultParams returns the starting calibration.
func DefaultParams() Params {
	return Params{
		WeightFlow:           0.60,
		WeightMove:           0.40,
		BurstMultipleAtFull:  12,
		MoveAtFull10s:        0.010,
		MoveAtFull60s:        0.030,
		MinFlowUSD:           25_000,
		ADXFloor:             20,
		ADXAtFull:            38,
		ExtensionAtFull:      1.5,
		RangeProximityAtFull: 0.03,
		AppearAt:             40,
		DisappearBelow:       25,
		LiveHalfLifeSec:      20,
	}
}

// ScreenFacts is the enrichment the pump/dump screen can supply.
type ScreenFacts struct {
	ATRPct        float64
	ClosePosition float64
	VolRatio      float64
	From24hHigh   float64
	From24hLow    float64
}

// TurtleFacts is the enrichment the Turtle survey can supply.
type TurtleFacts struct {
	ADX          float64
	RSI          float64
	ExtensionATR float64
	ATRPct       float64
	BreakoutUp   bool
	BreakoutDown bool
}

// Row is one symbol's heat.
type Row struct {
	Symbol     string             `json:"symbol"`
	Heat       float64            `json:"heat"`
	Live       float64            `json:"live"`
	Indicator  float64            `json:"indicator"`
	Lit        bool               `json:"lit"`
	Source     string             `json:"source"`
	Components map[string]float64 `json:"components"`
	Reasons    []string           `json:"reasons"`
	UpdatedMS  int64              `json:"updated_ms"`
}

// Service computes heat from the flow engine plus cached enrichment.
type Service struct {
	params Params
	engine *flow.Engine

	// Injected accessors keep this package independent of the screen and survey
	// packages, and trivially testable.
	screenFacts func() map[string]ScreenFacts
	turtleFacts func() map[string]TurtleFacts

	mu     sync.Mutex
	stored map[string]Row
}

// New creates a heat service. Either accessor may be nil.
func New(
	engine *flow.Engine,
	params Params,
	screenFacts func() map[string]ScreenFacts,
	turtleFacts func() map[string]TurtleFacts,
) *Service {
	if params.LiveHalfLifeSec <= 0 {
		params.LiveHalfLifeSec = 20
	}
	return &Service{
		params:      params,
		engine:      engine,
		screenFacts: screenFacts,
		turtleFacts: turtleFacts,
		stored:      make(map[string]Row),
	}
}

// Params returns the active calibration.
func (s *Service) Params() Params { return s.params }

// Compute scores every tracked symbol and returns the lit ones, hottest first.
func (s *Service) Compute(now int64, limit int) []Row {
	candidates := s.engine.Snapshot(0, now)

	var screen map[string]ScreenFacts
	if s.screenFacts != nil {
		screen = s.screenFacts()
	}
	var turtle map[string]TurtleFacts
	if s.turtleFacts != nil {
		turtle = s.turtleFacts()
	}

	scored := make(map[string]Row, len(candidates))
	for _, candidate := range candidates {
		row, ok := s.score(candidate, screen[candidate.Symbol], turtle[candidate.Symbol], now)
		if ok {
			scored[candidate.Symbol] = row
		}
	}

	s.mu.Lock()
	// Decay the LIVE term of anything we did not just re-score. Decaying the
	// indicator term would freeze the score at the last survey and let a glow
	// outlive the move it was reporting, so only the live half cools.
	for symbol, previous := range s.stored {
		if _, refreshed := scored[symbol]; refreshed {
			continue
		}
		age := float64(now-previous.UpdatedMS) / 1000.0
		if age <= 0 {
			continue
		}
		decayedLive := previous.Live * math.Pow(0.5, age/s.params.LiveHalfLifeSec)
		heat := math.Max(decayedLive, previous.Indicator)
		if heat < s.params.DisappearBelow {
			delete(s.stored, symbol)
			continue
		}
		previous.Live = decayedLive
		previous.Heat = math.Round(heat*10) / 10
		previous.Lit = true
		s.stored[symbol] = previous
	}
	for symbol, row := range scored {
		s.stored[symbol] = row
	}

	rows := make([]Row, 0, len(s.stored))
	for _, row := range s.stored {
		if row.Lit {
			rows = append(rows, row)
		}
	}
	s.mu.Unlock()

	sort.Slice(rows, func(i, j int) bool { return rows[i].Heat > rows[j].Heat })
	if limit > 0 && len(rows) > limit {
		rows = rows[:limit]
	}
	return rows
}

// score computes one symbol's heat. ok=false means "not lit, report nothing".
func (s *Service) score(
	candidate flow.Candidate,
	screen ScreenFacts,
	turtle TurtleFacts,
	now int64,
) (Row, bool) {
	p := s.params
	components := make(map[string]float64, 5)
	reasons := make([]string, 0, 3)

	// --- live attention: flow abnormality --------------------------------
	flowScore := clamp01(candidate.BurstMultiple / math.Max(p.BurstMultipleAtFull, 0.01))
	if candidate.FlowQuote < p.MinFlowUSD {
		// A high multiple on trivial money is noise, not interest.
		flowScore *= clamp01(candidate.FlowQuote / math.Max(p.MinFlowUSD, 1))
	}
	components["flow"] = flowScore

	// --- live attention: price velocity ----------------------------------
	window := s.engine.Config().WindowSec
	move10 := 0.0
	if candidate.Change10s != nil {
		move10 = math.Abs(*candidate.Change10s)
	}
	move60 := 0.0
	if candidate.Change60s != nil {
		move60 = math.Abs(*candidate.Change60s)
	}
	ratio10 := clamp01(move10 / math.Max(p.MoveAtFull10s, 1e-9))
	ratio60 := clamp01(move60 / math.Max(p.MoveAtFull60s, 1e-9))
	moveScore := math.Max(ratio10, ratio60)
	components["move"] = moveScore

	live := (p.WeightFlow*flowScore + p.WeightMove*moveScore) /
		math.Max(p.WeightFlow+p.WeightMove, 1e-9)

	if flowScore >= 0.55 {
		reasons = append(reasons, trim(candidate.BurstMultiple, 1)+"x flow burst")
	}
	if moveScore >= 0.5 {
		// Name the horizon that actually earned the score, or the tooltip can
		// read "0.00% over 1m" beside a bright border.
		if ratio10 >= ratio60 {
			reasons = append(reasons, formatPct(move10)+" in "+trim(window, 0)+"s")
		} else {
			reasons = append(reasons, formatPct(move60)+" over 1m")
		}
	}

	// --- indicator signal -------------------------------------------------
	indicator := 0.0
	indicatorSource := ""

	if turtle.ADX > 0 || turtle.RSI > 0 {
		trend := clamp01((turtle.ADX - p.ADXFloor) / math.Max(p.ADXAtFull-p.ADXFloor, 1e-9))
		rsiExtreme := clamp01((math.Abs(turtle.RSI-50) - 15) / 25)
		components["trend"] = math.Max(trend, rsiExtreme)
		if math.Max(trend, rsiExtreme) > indicator {
			indicator = math.Max(trend, rsiExtreme)
			indicatorSource = "trend"
		}

		extension := math.Abs(turtle.ExtensionATR)
		level := clamp01(1 - extension/math.Max(p.ExtensionAtFull, 1e-9))
		if turtle.BreakoutUp || turtle.BreakoutDown {
			level = math.Max(level, 0.9)
		}
		components["level"] = level
		if level > indicator {
			indicator = level
			indicatorSource = "level"
		}

		rangeScore := clamp01((math.Abs(turtle.RSI-50) - 20) / 20)
		components["range"] = rangeScore
		if rangeScore > indicator {
			indicator = rangeScore
			indicatorSource = "range"
		}
	} else if screen.ATRPct > 0 || screen.From24hHigh != 0 || screen.From24hLow != 0 {
		proximity := math.Min(math.Abs(screen.From24hHigh), math.Abs(screen.From24hLow))
		rangeScore := clamp01(1 - proximity/math.Max(p.RangeProximityAtFull, 1e-9))
		components["range"] = rangeScore
		if rangeScore > indicator {
			indicator = rangeScore
			indicatorSource = "range"
		}
		if screen.VolRatio > 0 {
			volume := clamp01(screen.VolRatio / 6)
			components["volume_ratio"] = volume
			if volume > indicator {
				indicator = volume
				indicatorSource = "volume"
			}
		}
	}

	if indicatorSource == "level" {
		if turtle.BreakoutUp {
			reasons = append(reasons, "breaking out above the level")
		} else if turtle.BreakoutDown {
			reasons = append(reasons, "breaking down through the level")
		} else {
			reasons = append(reasons, "at the structural level")
		}
	} else if indicatorSource == "trend" && indicator >= 0.6 {
		reasons = append(reasons, "ADX "+trim(turtle.ADX, 0)+" trending")
	} else if indicatorSource == "range" && indicator >= 0.7 {
		reasons = append(reasons, "at a 24h extreme")
	}

	// Either source can light the ticker: the requirement is "interesting for
	// price/volume action OR a technical-indicator signal".
	heat := 100 * math.Max(live, indicator)

	source := "flow"
	switch {
	case indicator > live && indicatorSource != "":
		source = indicatorSource
	case live >= indicator && moveScore > flowScore:
		source = "move"
	}

	s.mu.Lock()
	previous, seenBefore := s.stored[candidate.Symbol]
	s.mu.Unlock()

	// Hysteresis: light at AppearAt, stay lit until DisappearBelow. Without it
	// a symbol sitting on the threshold strobes, which is worse than not
	// showing it at all.
	lit := heat >= p.AppearAt || (seenBefore && previous.Lit && heat >= p.DisappearBelow)
	if !lit {
		return Row{}, false
	}
	if len(reasons) == 0 {
		reasons = append(reasons, "elevated activity")
	}

	return Row{
		Symbol:     candidate.Symbol,
		Heat:       math.Round(heat*10) / 10,
		Live:       math.Round(100*live*10) / 10,
		Indicator:  math.Round(100*indicator*10) / 10,
		Lit:        true,
		Source:     source,
		Components: components,
		Reasons:    reasons,
		UpdatedMS:  now,
	}, true
}

// --------------------------------------------------------------------------
// helpers
// --------------------------------------------------------------------------

func clamp01(v float64) float64 {
	if math.IsNaN(v) || v < 0 {
		return 0
	}
	if v > 1 {
		return 1
	}
	return v
}

func formatPct(fraction float64) string {
	sign := ""
	if fraction > 0 {
		sign = "+"
	}
	return sign + trim(fraction*100, 2) + "%"
}

func trim(v float64, digits int) string {
	if math.IsNaN(v) || math.IsInf(v, 0) {
		return "0"
	}
	factor := math.Pow(10, float64(digits))
	rounded := math.Round(v*factor) / factor
	return strconvFormat(rounded, digits)
}
