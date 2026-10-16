// Package turtle implements an alert-driven Turtle trading session.
//
// The rules are Turtle in structure and alert-driven in trigger:
//
//	ENTRY   an alert fires AND price confirms with a Donchian breakout
//	FILTER  realised volatility must not be in a mean-reverting regime
//	SIZE    one unit risks 1% of equity, sized off N (ATR)
//	STOP    2N adverse from entry
//	EXIT    a 10-period Donchian break the other way, or the stop
//
// WHY THE VOLATILITY FILTER EXISTS, because it is the least obvious part and the
// human asked for it specifically: an alert means "unusual flow just happened".
// In a LOW-volatility symbol that is information -- something new is happening and
// the move can continue. In a HIGH-volatility symbol the same alert is much more
// likely to be the END of an excursion than the start of one, because the symbol
// is already moving violently and alerts cluster at extremes. Chasing those is
// how a Turtle system bleeds: you buy the top of a spike and mean-reversion takes
// the 2N stop. So high realised volatility does not just reduce size, it vetoes
// the entry.
//
// PERFECT LIQUIDITY, stated as an assumption rather than hidden: fills happen at
// the signal price with no slippage, no partial fills and no market impact. That
// makes the PnL an UPPER BOUND on what the strategy could achieve, not a
// forecast. The assumption is recorded on every fill so no reader can mistake the
// number for an executable one.
package turtle

import (
	"sync"
	"time"
)

// State is the session lifecycle.
type State string

const (
	StateIdle    State = "idle"
	StateRunning State = "running"
	StateStopped State = "stopped"
)

// Config is the session's rule set. Exposed so the UI can show the rules the
// running session is actually using rather than describing them from memory.
type Config struct {
	StartingEquity float64 `json:"starting_equity"`
	// RiskPerUnit is the fraction of equity risked per unit (Turtle uses 1%).
	RiskPerUnit float64 `json:"risk_per_unit"`
	// StopN is the stop distance in N (Turtle uses 2).
	StopN float64 `json:"stop_n"`
	// EntryLookback and ExitLookback are Donchian windows in bars.
	EntryLookback int `json:"entry_lookback"`
	ExitLookback  int `json:"exit_lookback"`
	// MaxUnitsPerSymbol caps pyramiding.
	MaxUnitsPerSymbol int `json:"max_units_per_symbol"`
	// VolVetoPct is the realised-volatility level, in percent over the lookback,
	// at or above which entries are vetoed as mean-reversion risk.
	VolVetoPct float64 `json:"vol_veto_pct"`
	// MaxConcurrent caps open positions.
	MaxConcurrent int `json:"max_concurrent"`
}

// DefaultConfig is the starting rule set: 300 USDT, classic Turtle sizing.
func DefaultConfig() Config {
	return Config{
		StartingEquity:    300,
		RiskPerUnit:       0.01,
		StopN:             2,
		EntryLookback:     20,
		ExitLookback:      10,
		MaxUnitsPerSymbol: 1,
		VolVetoPct:        1.0,
		MaxConcurrent:     3,
	}
}

// Position is one open holding.
type Position struct {
	Symbol     string    `json:"symbol"`
	Side       string    `json:"side"` // "long" or "short"
	Units      float64   `json:"units"`
	EntryPrice float64   `json:"entry_price"`
	StopPrice  float64   `json:"stop_price"`
	N          float64   `json:"n"`
	Notional   float64   `json:"notional"`
	OpenedAt   time.Time `json:"opened_at"`
	// AlertID links the position to the alert that triggered it, so the trade
	// list can be read back against the signal that caused it.
	AlertID string `json:"alert_id"`
	// Last is the most recent mark, for unrealised PnL.
	Last float64 `json:"last"`
}

// Trade is a completed round trip.
type Trade struct {
	Symbol     string    `json:"symbol"`
	Side       string    `json:"side"`
	Units      float64   `json:"units"`
	EntryPrice float64   `json:"entry_price"`
	ExitPrice  float64   `json:"exit_price"`
	PnL        float64   `json:"pnl"`
	PnLPct     float64   `json:"pnl_pct"`
	OpenedAt   time.Time `json:"opened_at"`
	ClosedAt   time.Time `json:"closed_at"`
	Reason     string    `json:"reason"`
	AlertID    string    `json:"alert_id"`
	HoldSec    float64   `json:"hold_sec"`
}

// Order is one instruction the engine issued. Kept separately from trades
// because an order can be issued and not filled, and a list that showed only
// fills would hide the entries the volatility filter blocked.
type Order struct {
	ID        string    `json:"id"`
	Symbol    string    `json:"symbol"`
	Side      string    `json:"side"`
	Type      string    `json:"type"` // "entry", "exit", "stop"
	Units     float64   `json:"units"`
	Price     float64   `json:"price"`
	Status    string    `json:"status"` // "filled", "rejected"
	Reason    string    `json:"reason"`
	CreatedAt time.Time `json:"created_at"`
	// Liquidity records the fill assumption, so the number is never mistaken for
	// an executable one.
	Liquidity string `json:"liquidity"`
}

// Session is the whole session state, which is what the dashboard renders.
type Session struct {
	State      State      `json:"state"`
	Config     Config     `json:"config"`
	StartedAt  *time.Time `json:"started_at"`
	StoppedAt  *time.Time `json:"stopped_at"`
	Equity     float64    `json:"equity"`
	Cash       float64    `json:"cash"`
	Realized   float64    `json:"realized_pnl"`
	Unrealized float64    `json:"unrealized_pnl"`
	TotalPnL   float64    `json:"total_pnl"`
	ReturnPct  float64    `json:"return_pct"`
	Positions  []Position `json:"positions"`
	PeakEquity float64    `json:"peak_equity"`
	MaxDrawdn  float64    `json:"max_drawdown_pct"`
	// Counters make the filter's effect visible rather than silent: a vetoed
	// entry that left no trace would look like the engine simply missed it.
	// Watchlist is what the session decided to invest in at start. Exposed
	// because "why did it not trade" is usually answered here, not in the rules.
	Watchlist      []Scored `json:"watchlist"`
	EntriesFilled  int      `json:"entries_filled"`
	EntriesVetoed  int      `json:"entries_vetoed"`
	EntriesBlocked int      `json:"entries_blocked"`
	Note           string   `json:"note"`
}

// Alert is the subset of a pump/dump alert the engine acts on.
type Alert struct {
	ID        string    `json:"event_id"`
	Symbol    string    `json:"symbol"`
	Kind      string    `json:"kind"`     // PUMP, DUMP, ...
	Severity  string    `json:"severity"` // WATCH, ALERT, CRITICAL
	Price     float64   `json:"price"`
	ChangePct float64   `json:"change_pct"`
	At        time.Time `json:"at"`
	// VolPct is the symbol's realised volatility, supplied by the caller so the
	// engine does not need its own price history to apply the veto.
	VolPct float64 `json:"vol_pct"`
	// ATR is N in price terms. Zero means the caller could not supply it and the
	// entry is rejected rather than sized on a guess.
	ATR float64 `json:"atr"`
	// DonchianHigh and DonchianLow are the entry-channel bounds.
	DonchianHigh float64 `json:"donchian_high"`
	DonchianLow  float64 `json:"donchian_low"`
}

// Engine is the session. One instance per process; it is deliberately not
// concurrent-safe across sessions because a "session" is singular by definition
// and two running sessions would make PnL meaningless.
type Engine struct {
	mu    sync.Mutex
	cfg   Config
	state State

	startedAt *time.Time
	stoppedAt *time.Time

	cash       float64
	peakEquity float64
	maxDrawdn  float64

	positions map[string]*Position
	trades    []Trade
	orders    []Order

	entriesFilled  int
	entriesVetoed  int
	entriesBlocked int

	orderSeq int
	now      func() time.Time

	// The selected universe, decided once at session start from the prediction
	// inputs. Empty means "no filter", which is what a test or a manual session
	// wants and is NOT the same as having selected nothing.
	watchlist  map[string]Scored
	selected   []Scored
	selectedAt time.Time

	// store is the append-only session log. Nil-safe: every Record call checks,
	// so a session runs without persistence rather than panicking.
	store *Store
	// book is the book-rule configuration, editable from the settings page.
	book BookConfig
}

// BookConfig returns the live rule set.
func (e *Engine) BookConfig() BookConfig {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.book
}

// SetBookConfig replaces the rule set. Callers must refuse this mid-session --
// see the API handler -- because a PnL produced under two rule sets belongs to
// neither.
func (e *Engine) SetBookConfig(c BookConfig) {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.book = c
}

// ValidateBookConfig rejects rule sets that cannot work, with reasons rather
// than a bare false, so a settings page can show what is wrong.
func ValidateBookConfig(c BookConfig) []string {
	var out []string
	if c.NLookback < 2 {
		out = append(out, "n_lookback must be at least 2")
	}
	if c.RiskPct <= 0 || c.RiskPct > 0.2 {
		out = append(out, "risk_pct must be above 0 and at most 0.2 (20% per unit is not a risk rule)")
	}
	if c.StopN <= 0 {
		out = append(out, "stop_n must be positive")
	}
	if c.AddStepN <= 0 {
		out = append(out, "add_step_n must be positive")
	}
	if c.MaxUnitsPerMarket < 1 {
		out = append(out, "max_units_per_market must be at least 1")
	}
	if c.MaxUnitsCorrelated < c.MaxUnitsPerMarket {
		out = append(out, "max_units_correlated must be at least max_units_per_market, or the per-market cap can never bind")
	}
	if c.MaxUnitsPerDirection < c.MaxUnitsCorrelated {
		out = append(out, "max_units_per_direction must be at least max_units_correlated")
	}
	if c.MaxUnitsTotal < c.MaxUnitsPerDirection {
		out = append(out, "max_units_total must be at least max_units_per_direction")
	}
	if c.CorrelationThreshold <= 0 || c.CorrelationThreshold > 1 {
		out = append(out, "correlation_threshold must be in (0, 1]")
	}
	if c.S1Entry < 2 || c.S1Exit < 1 {
		out = append(out, "system 1 lookbacks must be at least 2 and 1")
	}
	if c.S2Entry <= c.S1Entry {
		out = append(out, "s2_entry must exceed s1_entry, or the two systems are the same system")
	}
	return out
}

// SetStore attaches the durable session log.
func (e *Engine) SetStore(s *Store) {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.store = s
}

// StoreStats reports the attached log, so a dashboard can show recording is
// ALIVE rather than assume it.
func (e *Engine) StoreStats() map[string]any { return e.store.Stats() }

// New builds an idle engine with the given rules.
func New(cfg Config) *Engine {
	if cfg.StartingEquity <= 0 {
		cfg = DefaultConfig()
	}
	return &Engine{
		book:       BookDefaults(),
		cfg:        cfg,
		state:      StateIdle,
		cash:       cfg.StartingEquity,
		peakEquity: cfg.StartingEquity,
		positions:  map[string]*Position{},
		now:        time.Now,
	}
}

// Start begins a session. Restarting resets all state, because a session that
// carried equity over from a previous run could not be compared to a fresh one.
func (e *Engine) Start() Session {
	e.mu.Lock()
	defer e.mu.Unlock()
	now := e.now()
	e.state = StateRunning
	e.startedAt = &now
	e.stoppedAt = nil
	e.cash = e.cfg.StartingEquity
	e.peakEquity = e.cfg.StartingEquity
	e.maxDrawdn = 0
	e.positions = map[string]*Position{}
	e.trades = nil
	e.orders = nil
	e.entriesFilled, e.entriesVetoed, e.entriesBlocked = 0, 0, 0
	e.store.Record("session_start", map[string]any{
		"config": e.cfg, "starting_equity": e.cfg.StartingEquity,
	})
	return e.sessionLocked()
}

// Stop ends the session and closes every open position at its last mark.
func (e *Engine) Stop() Session {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.state != StateRunning {
		return e.sessionLocked()
	}
	for sym, pos := range e.positions {
		e.closeLocked(sym, pos, pos.Last, "session stopped")
	}
	now := e.now()
	e.stoppedAt = &now
	e.state = StateStopped
	e.store.Record("session_stop", map[string]any{
		"final_equity": round2(e.equityLocked()),
		"realized":     round2(e.realizedLocked()),
		"trades":       len(e.trades),
		"filled":       e.entriesFilled, "vetoed": e.entriesVetoed, "blocked": e.entriesBlocked,
	})
	return e.sessionLocked()
}
