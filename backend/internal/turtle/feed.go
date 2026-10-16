package turtle

import "time"

// Watchlist support and the session's own view of the market.
//
// The engine does not read the flow engine directly. It is FED, for two reasons:
// the turtle rules stay testable without a live market, and the feed path is a
// single place where "an alert arrived" and "a price moved" can be logged
// against the session that acted on them.

// SetWatchlist replaces the selected universe. An EMPTY list disables the
// filter entirely, which is the behaviour a test or a manual session wants; it is
// not the same as selecting nothing.
func (e *Engine) SetWatchlist(sel []Scored) {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.watchlist = make(map[string]Scored, len(sel))
	for _, s := range sel {
		e.watchlist[s.Symbol] = s
	}
	e.selected = append([]Scored(nil), sel...)
	e.selectedAt = e.now()
	e.store.Record("watchlist", map[string]any{"selected": sel, "size": len(sel)})
}

// Watchlist returns the symbols the session decided to trade, best first.
func (e *Engine) Watchlist() ([]Scored, time.Time) {
	e.mu.Lock()
	defer e.mu.Unlock()
	return append([]Scored(nil), e.selected...), e.selectedAt
}

// OnAlertForSymbol is the feeder path: it looks the symbol up in the selected
// universe, fills in the volatility and ATR the entry rules need, and defers to
// OnAlert for the decision.
//
// An alert for a symbol NOT on the watchlist is counted as blocked rather than
// ignored, because "we chose not to watch this" is a decision the dashboard
// should be able to show. A silent drop would make a selective system look like a
// broken one.
func (e *Engine) OnAlertForSymbol(a Alert, price, atr, volPct float64) (Order, bool) {
	e.mu.Lock()
	scored, onList := e.watchlist[a.Symbol]
	hasList := len(e.watchlist) > 0
	e.mu.Unlock()

	if hasList && !onList {
		e.mu.Lock()
		e.entriesBlocked++
		o := Order{
			ID: e.nextOrderIDLocked(), Symbol: a.Symbol, Side: sideFor(a.Kind),
			Type: "entry", Status: "rejected", CreatedAt: a.At,
			Reason:    "not on the session watchlist; the symbol was not selected at session start",
			Liquidity: "perfect liquidity assumed; no fill issued",
		}
		e.orders = append(e.orders, o)
		e.mu.Unlock()
		return o, false
	}

	if onList {
		a.VolPct = scored.VolPct
		if a.ATR <= 0 {
			a.ATR = scored.ATR
		}
		if a.DonchianHigh <= 0 {
			a.DonchianHigh = scored.DonchianHigh
		}
		if a.DonchianLow <= 0 {
			a.DonchianLow = scored.DonchianLow
		}
		if a.Price <= 0 {
			a.Price = scored.Price
		}
	} else if volPct > 0 {
		a.VolPct = volPct
	}
	if a.ATR <= 0 {
		a.ATR = atr
	}
	if a.Price <= 0 {
		a.Price = price
	}
	return e.OnAlert(a)
}

// Progress reports how a session is doing, which is the number that answers
// "is this system working" rather than "did it trade".
type Progress struct {
	State       State   `json:"state"`
	Equity      float64 `json:"equity"`
	ReturnPct   float64 `json:"return_pct"`
	Filled      int     `json:"filled"`
	Vetoed      int     `json:"vetoed"`
	Blocked     int     `json:"blocked"`
	Closed      int     `json:"closed"`
	WinRate     float64 `json:"win_rate_pct"`
	Expectancy  float64 `json:"expectancy_usdt"`
	Watchlist   int     `json:"watchlist_size"`
	VetoRatePct float64 `json:"veto_rate_pct"`
}

// Progress summarises the session. Expectancy is the number that matters for the
// alpha goal: a system can win most trades and still lose money, and a win rate
// alone hides that.
func (e *Engine) Progress() Progress {
	e.mu.Lock()
	defer e.mu.Unlock()
	wins, total := 0, 0.0
	for _, t := range e.trades {
		total += t.PnL
		if t.PnL > 0 {
			wins++
		}
	}
	closed := len(e.trades)
	wr, exp := 0.0, 0.0
	if closed > 0 {
		wr = float64(wins) / float64(closed) * 100
		exp = total / float64(closed)
	}
	attempts := e.entriesFilled + e.entriesVetoed + e.entriesBlocked
	vetoRate := 0.0
	if attempts > 0 {
		vetoRate = float64(e.entriesVetoed) / float64(attempts) * 100
	}
	return Progress{
		State: e.state, Equity: round2(e.equityLocked()),
		ReturnPct: round4(safePct(e.equityLocked()-e.cfg.StartingEquity, e.cfg.StartingEquity)),
		Filled:    e.entriesFilled, Vetoed: e.entriesVetoed, Blocked: e.entriesBlocked,
		Closed: closed, WinRate: round4(wr), Expectancy: round2(exp),
		Watchlist: len(e.watchlist), VetoRatePct: round4(vetoRate),
	}
}

func safePct(v, base float64) float64 {
	if base == 0 {
		return 0
	}
	return v / base * 100
}
