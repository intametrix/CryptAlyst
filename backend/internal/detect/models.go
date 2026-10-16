package detect

import (
	"fmt"
	"math"
	"strings"
	"sync"
	"time"

	"pumpdump/backend/internal/flow"
	"pumpdump/backend/internal/onset"
	"pumpdump/backend/internal/screen"
	"pumpdump/backend/internal/turtle"
)

// --------------------------------------------------------------------------
// flow-onset: the streaming, 1-second onset detector
// --------------------------------------------------------------------------

// FlowOnset wraps the sub-minute flow engine as a pluggable detector.
//
// It owns the three concerns that are specific to this model and do not belong
// in the transport layer: cooldown (so a developing pump does not alert on
// every consecutive second), escalation (asking for trade-level detail for a
// symbol that just tripped) and scoring.
type FlowOnset struct {
	engine   *flow.Engine
	cfg      flow.Config
	cooldown time.Duration

	// escalation bookkeeping
	hotTTL      time.Duration
	hotMax      int
	hot         map[string]time.Time
	escalations []EscalationRequest

	mu sync.Mutex
	// last is keyed "symbol|kind" and holds epoch milliseconds, matching the
	// `now` the registry passes to Evaluate. Cooldown lives in the detector
	// rather than the transport because it is model-specific rate limiting.
	last map[string]int64
}

// NewFlowOnset wraps an engine.
func NewFlowOnset(engine *flow.Engine, cooldown time.Duration, hotTTL time.Duration, hotMax int) *FlowOnset {
	if cooldown < 0 {
		cooldown = 0
	}
	if hotTTL <= 0 {
		hotTTL = 180 * time.Second
	}
	if hotMax < 1 {
		hotMax = 12
	}
	return &FlowOnset{
		engine:   engine,
		cfg:      engine.Config(),
		cooldown: cooldown,
		hotTTL:   hotTTL,
		hotMax:   hotMax,
		hot:      make(map[string]time.Time),
		last:     make(map[string]int64),
	}
}

// Name implements Detector.
func (f *FlowOnset) Name() string { return "flow-onset" }

// Title implements Detector.
func (f *FlowOnset) Title() string { return "Flow onset (1s)" }

// Description implements Detector.
func (f *FlowOnset) Description() string {
	return "Detects the START of a sharp move by measuring how much quote volume " +
		"has flowed in the last few seconds relative to the symbol's own baseline, " +
		"confirmed by price velocity, tick coverage and optionally trade-level taker imbalance."
}

// Batch implements Detector.
func (f *FlowOnset) Batch() bool { return false }

// Params implements Detector.
func (f *FlowOnset) Params() map[string]any {
	c := f.cfg
	return map[string]any{
		"window_sec":              c.WindowSec,
		"min_burst_multiple":      c.MinBurstMultiple,
		"min_burst_z":             c.MinBurstZ,
		"min_abs_flow_usd":        c.MinAbsFlow,
		"min_move_pct":            c.MinMovePct,
		"min_sigma_multiple":      c.MinSigmaMultiple,
		"min_coverage":            c.MinCoverage,
		"max_spread_pct":          c.MaxSpreadPct,
		"min_book_depth_usd":      c.MinBookDepthUSD,
		"max_late_pct_60s":        c.MaxLatePct60s,
		"require_acceleration":    c.RequireAcceleration,
		"min_trades":              c.MinTrades,
		"baseline_floor_fraction": c.BaselineFloorFraction,
		"cooldown_sec":            f.cooldown.Seconds(),
	}
}

// Observe implements Detector.
func (f *FlowOnset) Observe(ev Event) {
	switch ev.Type {
	case EventTicker:
		f.engine.IngestTicker(ev.Raw)
	case EventAggTrade:
		f.engine.IngestAggTrade(ev.Raw)
	}
}

// Evaluate implements Detector.
func (f *FlowOnset) Evaluate(now int64) []Signal {
	// Keep the liquidity floor current with the freshest 24h volumes before
	// judging anything against it.
	f.engine.ApplyFloors()

	out := make([]Signal, 0, 8)
	for _, candidate := range f.engine.Detect(now) {
		kind := onset.KindFor(candidate.Direction)
		if !f.allow(candidate.Symbol, kind, now) {
			continue
		}
		// Ask for trade-level detail on a fresh symbol so the NEXT alert for
		// it can judge taker-side aggression properly.
		f.requestEscalation(candidate.Symbol)

		result := onset.Score(candidate, f.cfg, candidate.TakerQuote)
		f.mark(candidate.Symbol, kind, now)

		move := 0.0
		if candidate.Change10s != nil {
			move = *candidate.Change10s
		}
		out = append(out, Signal{
			// The cooldown already rate-limits this model, so the ID is unique
			// per alert; the registry's dedup is a safety net rather than the
			// primary mechanism here.
			ID: fmt.Sprintf("flow-onset|%s|%s|%d", candidate.Symbol, kind, now),
			Summary: fmt.Sprintf("%s %s %+.2f%%/%gs on %.1fx flow ($%s)",
				kind, candidate.Symbol, move*100, f.cfg.WindowSec,
				candidate.BurstMultiple, humanUSD(candidate.FlowQuote)),
			Detector:   f.Name(),
			Kind:       kind,
			Symbol:     candidate.Symbol,
			Direction:  candidate.Direction,
			Severity:   result.Severity,
			Score:      result.Score,
			Price:      candidate.Price,
			TSMS:       now,
			Reasons:    result.Reasons,
			Components: result.Components,
			Metrics: map[string]any{
				"window_sec":       f.cfg.WindowSec,
				"flow_quote":       candidate.FlowQuote,
				"baseline_quote":   candidate.BaselineQuote,
				"coverage":         candidate.Coverage,
				"burst_multiple":   candidate.BurstMultiple,
				"burst_z":          candidate.BurstZ,
				"change_10s":       candidate.Change10s,
				"change_30s":       candidate.Change30s,
				"change_60s":       candidate.Change60s,
				"sigma_10s":        candidate.Sigma10s,
				"sigma_multiple":   candidate.SigmaMultiple,
				"acceleration":     candidate.Acceleration,
				"range_position":   candidate.RangePosition,
				"spread_pct":       candidate.SpreadPct,
				"book_depth_usd":   candidate.BookDepthUSD,
				"quote_volume_24h": candidate.QuoteVolume24h,
				"trades_10s":       candidate.Trades10s,
				"trades_multiple":  candidate.TradesMultiple,
				"taker_buy_ratio":  candidate.TakerBuyRatio,
				"taker_quote":      candidate.TakerQuote,
				"penalty":          result.Penalty,
				"agg_confirmed":    result.AggConfirmed,
			},
		})
	}
	return out
}

// Escalations implements Escalating.
func (f *FlowOnset) Escalations() []EscalationRequest {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := f.escalations
	f.escalations = nil
	return out
}

// Reset implements the resetter contract used by Registry.SetEnabled.
//
// It clears volatile state only. The cooldown table is intentionally KEPT: a
// cooldown only suppresses, so preserving it is harmless, whereas clearing it
// would make every symbol that had been suppressed fire the moment the detector
// is re-enabled. Escalation slots are released because holding them while
// disabled would starve the transport for no benefit.
func (f *FlowOnset) Reset() {
	f.mu.Lock()
	f.hot = make(map[string]time.Time)
	f.escalations = nil
	f.mu.Unlock()
}

// Engine exposes the wrapped engine for the API's flow leaderboard.
func (f *FlowOnset) Engine() *flow.Engine { return f.engine }

func (f *FlowOnset) allow(symbol, kind string, now int64) bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	at, ok := f.last[symbol+"|"+kind]
	if !ok {
		return true
	}
	return now-at >= f.cooldown.Milliseconds()
}

func (f *FlowOnset) mark(symbol, kind string, now int64) {
	f.mu.Lock()
	f.last[symbol+"|"+kind] = now
	f.mu.Unlock()
}

func (f *FlowOnset) requestEscalation(symbol string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if _, already := f.hot[symbol]; already {
		f.hot[symbol] = time.Now().Add(f.hotTTL)
		return
	}
	if len(f.hot) >= f.hotMax {
		// Evict the soonest to expire so the stream count stays bounded.
		var oldest string
		var oldestAt time.Time
		for sym, at := range f.hot {
			if oldest == "" || at.Before(oldestAt) {
				oldest, oldestAt = sym, at
			}
		}
		delete(f.hot, oldest)
	}
	f.hot[symbol] = time.Now().Add(f.hotTTL)
	f.escalations = append(f.escalations, EscalationRequest{
		Symbol: symbol,
		Stream: "aggTrade",
		TTL:    f.hotTTL,
	})
}

// ExpiredEscalations returns symbols whose escalation has lapsed so the
// transport can drop their extra streams.
func (f *FlowOnset) ExpiredEscalations() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	now := time.Now()
	out := make([]string, 0, 4)
	for symbol, at := range f.hot {
		if now.After(at) {
			out = append(out, symbol)
			delete(f.hot, symbol)
		}
	}
	return out
}

// --------------------------------------------------------------------------
// pump-screen: the periodic 5/15/60-minute batch detector
// --------------------------------------------------------------------------

// PumpScreen exposes the REST-based pump/dump screen as a batch detector.
type PumpScreen struct {
	screener *screen.Screener
}

// NewPumpScreen wraps a screener.
func NewPumpScreen(s *screen.Screener) *PumpScreen { return &PumpScreen{screener: s} }

// Name implements Detector.
func (p *PumpScreen) Name() string { return "pump-screen" }

// Title implements Detector.
func (p *PumpScreen) Title() string { return "Pump/dump screen (5-60m)" }

// Description implements Detector.
func (p *PumpScreen) Description() string {
	return "Periodic sweep of 1-minute klines across the market, scoring multi-horizon " +
		"moves backed by abnormal volume and taker-side aggression, including post-pump reversals."
}

// Batch implements Detector.
func (p *PumpScreen) Batch() bool { return true }

// Params implements Detector.
func (p *PumpScreen) Params() map[string]any {
	t := p.screener.Thresholds()
	return map[string]any{
		"min_move_5m":          t.MinMove5m,
		"min_move_15m":         t.MinMove15m,
		"min_vol_ratio":        t.MinVolRatio,
		"min_vol_z":            t.MinVolZ,
		"min_recent_usd":       t.MinRecentUSD,
		"min_quote_volume_24h": t.MinQuoteVolume24h,
		"reversal_prior_pct":   t.ReversalPriorPct,
		"reversal_fast_pct":    t.ReversalFastPct,
	}
}

// Observe implements Detector. The screen is batch, so live events are ignored.
func (p *PumpScreen) Observe(Event) {}

// Evaluate implements Detector: it republishes the most recent screen result,
// flagged as batch with the age of its snapshot.
func (p *PumpScreen) Evaluate(now int64) []Signal {
	rows, generatedMS, _ := p.screener.Cached()
	if len(rows) == 0 {
		return nil
	}
	out := make([]Signal, 0, len(rows))
	for _, row := range rows {
		direction := 1
		if row.Direction() < 0 {
			direction = -1
		}
		severity := "WATCH"
		switch {
		case row.Score >= 75:
			severity = "CRITICAL"
		case row.Score >= 55:
			severity = "ALERT"
		}
		ret5 := 0.0
		if row.Ret5m != nil {
			ret5 = *row.Ret5m
		}
		out = append(out, Signal{
			// Keyed on the snapshot timestamp: this detector republishes its
			// whole cached result every tick, so the snapshot is the natural
			// identity and the registry emits each row exactly once.
			ID: fmt.Sprintf("pump-screen|%s|%s|%d", row.Symbol, row.Kind, generatedMS),
			Summary: fmt.Sprintf("%s %s %+.2f%%/5m on %.1fx volume ($%s in 5m)",
				row.Kind, row.Symbol, ret5*100, row.VolRatio,
				humanUSD(row.RecentQuoteVolume)),
			Detector:   p.Name(),
			Kind:       string(row.Kind),
			Symbol:     row.Symbol,
			Direction:  direction,
			Severity:   severity,
			Score:      row.Score,
			Price:      row.Price,
			TSMS:       generatedMS,
			Batch:      true,
			SnapshotMS: generatedMS,
			Reasons:    row.Reasons,
			Metrics: map[string]any{
				"ret_5m":              row.Ret5m,
				"ret_15m":             row.Ret15m,
				"ret_60m":             row.Ret60m,
				"vol_ratio":           row.VolRatio,
				"vol_z":               row.VolZ,
				"recent_quote_volume": row.RecentQuoteVolume,
				"quote_volume_24h":    row.QuoteVolume24h,
				"taker_buy_ratio":     row.TakerBuyRatio,
				"close_position":      row.ClosePosition,
				"atr_pct":             row.ATRPct,
				"from_24h_high":       row.From24hHigh,
				"from_24h_low":        row.From24hLow,
			},
		})
	}
	return out
}

// --------------------------------------------------------------------------
// turtle-breakout: the periodic structural batch detector
// --------------------------------------------------------------------------

// TurtleBreakout exposes the Turtle survey as a batch detector.
type TurtleBreakout struct {
	survey *turtle.Survey
}

// NewTurtleBreakout wraps a survey. A nil survey yields a detector that never
// signals, which keeps registration unconditional.
func NewTurtleBreakout(s *turtle.Survey) *TurtleBreakout {
	return &TurtleBreakout{survey: s}
}

// Name implements Detector.
func (t *TurtleBreakout) Name() string { return "turtle-breakout" }

// Title implements Detector.
func (t *TurtleBreakout) Title() string { return "Turtle breakout (1d/4h)" }

// Description implements Detector.
func (t *TurtleBreakout) Description() string {
	return "Structural Donchian breakouts with Wilder ATR sizing, a volume-confirmation gate, " +
		"an ATR band and a latched BTC regime gate, scored across timeframes."
}

// Batch implements Detector.
func (t *TurtleBreakout) Batch() bool { return true }

// Params implements Detector.
func (t *TurtleBreakout) Params() map[string]any {
	if t.survey == nil {
		return map[string]any{"enabled": false, "reason": "survey disabled at startup"}
	}
	p := t.survey.Params()
	return map[string]any{
		"entry_period":              p.EntryPeriod,
		"entry_period_slow":         p.EntryPeriodSlow,
		"exit_period":               p.ExitPeriod,
		"atr_period":                p.ATRPeriod,
		"stop_atr_multiple":         p.StopATR,
		"risk_per_unit":             p.RiskPerUnit,
		"max_units_correlated":      p.MaxUnitsCorrelated,
		"max_units_total":           p.MaxUnitsTotal,
		"volume_confirm_mult":       p.VolumeConfirmMult,
		"volume_median_floor_usd":   p.VolumeMedianFloorUSD,
		"volume_absolute_floor_usd": p.VolumeAbsoluteFloorUSD,
		"min_atr_pct":               p.MinATRPct,
		"max_atr_pct":               p.MaxATRPct,
		"adx_min":                   p.ADXMin,
		"btc_gate_period":           p.BTCGatePeriod,
	}
}

// Observe implements Detector. The survey is batch, so live events are ignored.
func (t *TurtleBreakout) Observe(Event) {}

// Evaluate implements Detector: it republishes the most recent survey setups.
func (t *TurtleBreakout) Evaluate(now int64) []Signal {
	if t.survey == nil {
		return nil
	}
	payload, ok := t.survey.Current().(map[string]any)
	if !ok {
		return nil
	}
	rawSetups, ok := payload["setups"].([]map[string]any)
	if !ok {
		return nil
	}
	snapshotMS := t.survey.LastMS()
	out := make([]Signal, 0, len(rawSetups))
	for _, setup := range rawSetups {
		score, _ := setup["score"].(float64)
		direction, _ := setup["direction"].(int)
		severity := "WATCH"
		switch {
		case score >= 75:
			severity = "CRITICAL"
		case score >= 55:
			severity = "ALERT"
		}
		reasons, _ := setup["reasons"].([]string)
		atrPct := asFloat(setup["atr_pct"])
		// A single self-describing kind rather than a bare BREAKOUT plus a
		// direction field: consumers otherwise have to combine two values to
		// know what happened, and every one of them gets it wrong once.
		kind := "BREAKOUT_LONG"
		if direction < 0 {
			kind = "BREAKOUT_SHORT"
		}
		out = append(out, Signal{
			// Keyed on the survey snapshot, for the same reason as the screen.
			ID: fmt.Sprintf("turtle-breakout|%s|%d", fmt.Sprint(setup["symbol"]), snapshotMS),
			Summary: fmt.Sprintf("%s %s score %.0f (%s, ATR %.2f%%)",
				strings.ReplaceAll(kind, "_", " "), fmt.Sprint(setup["symbol"]),
				score, fmt.Sprint(setup["timeframe"]), atrPct*100),
			Detector:   t.Name(),
			Kind:       kind,
			Symbol:     fmt.Sprint(setup["symbol"]),
			Direction:  direction,
			Severity:   severity,
			Score:      score,
			Price:      asFloat(setup["close"]),
			TSMS:       snapshotMS,
			Batch:      true,
			SnapshotMS: snapshotMS,
			Reasons:    reasons,
			Metrics: map[string]any{
				"timeframe":        setup["timeframe"],
				"atr":              setup["atr"],
				"atr_pct":          setup["atr_pct"],
				"adx14":            setup["adx"],
				"rsi14":            setup["rsi"],
				"extension_up_atr": setup["extension_up_atr"],
				"group":            setup["group"],
				"accepted":         setup["accepted"],
				"reject_reason":    setup["reject_reason"],
			},
		})
	}
	return out
}

// humanUSD renders a dollar amount compactly for signal summaries.
func humanUSD(v float64) string {
	abs := math.Abs(v)
	switch {
	case abs >= 1e9:
		return fmt.Sprintf("%.2fB", v/1e9)
	case abs >= 1e6:
		return fmt.Sprintf("%.2fM", v/1e6)
	case abs >= 1e3:
		return fmt.Sprintf("%.1fK", v/1e3)
	default:
		return fmt.Sprintf("%.0f", v)
	}
}

func asFloat(v any) float64 {
	switch n := v.(type) {
	case float64:
		return n
	case float32:
		return float64(n)
	case int:
		return float64(n)
	case int64:
		return float64(n)
	default:
		// Never return NaN: encoding/json REFUSES to marshal NaN and would
		// fail the entire response, so an unknown shape degrades to zero.
		return 0
	}
}
