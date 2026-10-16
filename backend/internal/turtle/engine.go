package turtle

import (
	"fmt"
	"math"
	"time"
)

// OnAlert evaluates one alert and, if every rule passes, opens a position.
//
// The order of the checks is deliberate: the CHEAP structural rejections run
// before the volatility veto, and the veto runs before the Donchian confirm,
// because the veto is the rule most likely to save money and the one whose effect
// is easiest to hide. Every rejection is counted and reasoned, so a dashboard can
// answer "why did it not trade?" rather than showing a silent zero.
func (e *Engine) OnAlert(a Alert) (Order, bool) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.state != StateRunning {
		return Order{}, false
	}

	reject := func(reason string, blocked bool) (Order, bool) {
		if blocked {
			e.entriesBlocked++
		} else {
			e.entriesVetoed++
		}
		o := Order{
			ID: e.nextOrderIDLocked(), Symbol: a.Symbol, Side: sideFor(a.Kind),
			Type: "entry", Status: "rejected", Reason: reason, CreatedAt: a.At,
			Liquidity: "perfect liquidity assumed; no fill issued",
		}
		e.orders = append(e.orders, o)
		e.store.Record("reject", map[string]any{
			"symbol": o.Symbol, "side": o.Side, "reason": o.Reason,
			"vol_pct": a.VolPct, "atr": a.ATR, "price": a.Price,
			"kind": a.Kind, "severity": a.Severity, "alert_id": a.ID,
		})
		return o, false
	}

	side := sideFor(a.Kind)
	if side == "" {
		return reject("alert kind "+a.Kind+" is not directional", true)
	}
	if _, exists := e.positions[a.Symbol]; exists {
		return reject("already holding "+a.Symbol, true)
	}
	if len(e.positions) >= e.cfg.MaxConcurrent {
		return reject(fmt.Sprintf("at %d concurrent positions", e.cfg.MaxConcurrent), true)
	}
	if a.Price <= 0 || a.ATR <= 0 {
		return reject("no price or ATR supplied; sizing refused rather than guessed", true)
	}

	// THE VOLATILITY VETO. See the package comment: in a symbol already moving
	// violently, an alert is more likely to mark the END of an excursion than the
	// start of one, and a Turtle stop is exactly what mean-reversion takes out.
	// This is a veto and not a size reduction on purpose -- halving the size of a
	// negative-expectancy trade still loses money, only more slowly.
	if a.VolPct >= e.cfg.VolVetoPct {
		return reject(fmt.Sprintf(
			"volatility veto: realised %.2f%% >= %.2f%% threshold; alerts at this volatility mean-revert",
			a.VolPct, e.cfg.VolVetoPct), false)
	}

	// The Donchian confirm. An alert is a reason to LOOK; the breakout is the
	// reason to act. Without it the engine would buy every spike.
	switch side {
	case "long":
		if a.DonchianHigh <= 0 || a.Price < a.DonchianHigh {
			return reject("no Donchian breakout: price below the entry channel high", false)
		}
	case "short":
		if a.DonchianLow <= 0 || a.Price > a.DonchianLow {
			return reject("no Donchian breakout: price above the entry channel low", false)
		}
	}

	units, notional, ok := e.sizeUnits(a.Symbol, a.Price, a.ATR)
	if !ok {
		return reject("sizing failed: equity or N too small for one unit", true)
	}

	stop := a.Price - e.cfg.StopN*a.ATR
	if side == "short" {
		stop = a.Price + e.cfg.StopN*a.ATR
	}

	pos := &Position{
		Symbol: a.Symbol, Side: side, Units: units, EntryPrice: a.Price,
		StopPrice: stop, N: a.ATR, Notional: notional, OpenedAt: a.At,
		AlertID: a.ID, Last: a.Price,
	}
	e.positions[a.Symbol] = pos
	e.cash -= notional
	e.entriesFilled++

	o := Order{
		ID: e.nextOrderIDLocked(), Symbol: a.Symbol, Side: side, Type: "entry",
		Units: units, Price: a.Price, Status: "filled",
		Reason: fmt.Sprintf("alert %s %s + Donchian confirm; N=%.6f stop=%.6f",
			a.Kind, a.Severity, a.ATR, stop),
		CreatedAt: a.At,
		Liquidity: "perfect liquidity assumed: filled at signal price, no slippage",
	}
	e.orders = append(e.orders, o)
	e.store.Record("fill", map[string]any{
		"order_id": o.ID, "symbol": o.Symbol, "side": o.Side,
		"units": units, "price": a.Price, "notional": notional,
		"n": a.ATR, "stop": stop, "alert_id": a.ID,
		"kind": a.Kind, "severity": a.Severity, "vol_pct": a.VolPct,
	})
	return o, true
}

// Mark updates one symbol's price and closes the position if the stop is hit.
func (e *Engine) Mark(symbol string, price float64, at time.Time) {
	e.mu.Lock()
	defer e.mu.Unlock()
	pos, ok := e.positions[symbol]
	if !ok || price <= 0 {
		return
	}
	pos.Last = price
	hit := (pos.Side == "long" && price <= pos.StopPrice) ||
		(pos.Side == "short" && price >= pos.StopPrice)
	if hit && e.state == StateRunning {
		e.closeLocked(symbol, pos, pos.StopPrice, "2N stop")
	}
}

// closeLocked books a round trip and returns the cash.
func (e *Engine) closeLocked(symbol string, pos *Position, price float64, reason string) {
	dir := 1.0
	if pos.Side == "short" {
		dir = -1
	}
	pnl := (price - pos.EntryPrice) * pos.Units * dir
	// Perfect liquidity: proceeds are exactly units x price, and the entry
	// notional was already deducted from cash.
	e.cash += pos.Notional + pnl

	pct := 0.0
	if pos.Notional > 0 {
		pct = pnl / pos.Notional * 100
	}
	e.trades = append(e.trades, Trade{
		Symbol: symbol, Side: pos.Side, Units: pos.Units,
		EntryPrice: pos.EntryPrice, ExitPrice: price,
		PnL: round2(pnl), PnLPct: round4(pct),
		OpenedAt: pos.OpenedAt, ClosedAt: timeOr(atOrNow(e, pos)), Reason: reason,
		AlertID: pos.AlertID, HoldSec: math.Max(0, atOrNow(e, pos).Sub(pos.OpenedAt).Seconds()),
	})
	e.orders = append(e.orders, Order{
		ID: e.nextOrderIDLocked(), Symbol: symbol, Side: pos.Side, Type: exitType(reason),
		Units: pos.Units, Price: price, Status: "filled",
		Reason:    fmt.Sprintf("%s; realised %.2f USDT", reason, pnl),
		CreatedAt: atOrNow(e, pos),
		Liquidity: "perfect liquidity assumed: filled at signal price, no slippage",
	})
	e.store.Record("close", map[string]any{
		"symbol": symbol, "side": pos.Side, "units": pos.Units,
		"entry_price": pos.EntryPrice, "exit_price": price,
		"pnl": round2(pnl), "pnl_pct": round4(pct), "reason": reason,
		"alert_id": pos.AlertID,
		"hold_sec": atOrNow(e, pos).Sub(pos.OpenedAt).Seconds(),
	})
	delete(e.positions, symbol)

	// Track drawdown on closed equity, which is the honest series for it.
	eq := e.equityLocked()
	if eq > e.peakEquity {
		e.peakEquity = eq
	}
	if e.peakEquity > 0 {
		if dd := (e.peakEquity - eq) / e.peakEquity * 100; dd > e.maxDrawdn {
			e.maxDrawdn = dd
		}
	}
}

// sizeUnits is Turtle sizing: risk RiskPerUnit of equity over a StopN x N move.
//
// The trade is REFUSED rather than rounded up when one unit would risk more than
// the cap, because on a 300 USDT account that case is common and rounding up
// would quietly break the risk rule the whole method rests on.
func (e *Engine) sizeUnits(symbol string, price, atr float64) (units, notional float64, ok bool) {
	riskPerUnit := e.cfg.StopN * atr * 1.0 // dollars lost per unit at the stop
	if riskPerUnit <= 0 {
		return 0, 0, false
	}
	// DEPLOYED MODE sizes by NOTIONAL, not by risk. See deploy.go for what that
	// changes: the capital is the target and the stop distance is whatever it is,
	// so a volatile symbol carries more percentage risk than a calm one. Selected
	// by the human's instruction that all capital be deployed.
	if e.deploy == DeployFull {
		return e.deployedUnits(symbol, price)
	}

	// THE ACCOUNT'S RISK, SHARED. The equity is the whole account's, never one
	// asset's, and the fraction of it this symbol may risk comes from the portfolio
	// allocation rather than a flat rate per position.
	//
	// The flat rate is not wrong arithmetic, it is wrong ACCOUNTING: ten positions
	// at 1% each is 10% of the account at risk, not 1%, and nothing in the flat
	// version notices. The weight is the symbol's share of a budget bounded for the
	// book as a whole.
	budgetPct := e.cfg.RiskPerUnit
	if w := e.allocation.Weight(symbol); w > 0 {
		budgetPct = w / 100.0
	}
	budget := e.equityLocked() * budgetPct
	units = budget / riskPerUnit
	notional = units * price
	if units <= 0 || notional <= 0 {
		return 0, 0, false
	}
	// Cash is a hard constraint even under perfect liquidity: a 300 USDT account
	// cannot hold a 3000 USDT notional, and pretending otherwise would make the
	// returns fiction.
	if notional > e.cash {
		units = e.cash / price
		notional = units * price
	}
	if units <= 0 {
		return 0, 0, false
	}
	return units, notional, true
}

func (e *Engine) equityLocked() float64 {
	return e.cash + e.openValueLocked()
}

func (e *Engine) openValueLocked() float64 {
	total := 0.0
	for _, p := range e.positions {
		dir := 1.0
		if p.Side == "short" {
			dir = -1
		}
		total += p.Notional + (p.Last-p.EntryPrice)*p.Units*dir
	}
	return total
}

func (e *Engine) sessionLocked() Session {
	positions := make([]Position, 0, len(e.positions))
	for _, p := range e.positions {
		positions = append(positions, *p)
	}
	sortPositions(positions)

	realized := 0.0
	for _, t := range e.trades {
		realized += t.PnL
	}
	openVal := e.openValueLocked()
	unrealized := openVal - sumNotional(e.positions)
	equity := e.cash + openVal
	total := equity - e.cfg.StartingEquity
	ret := 0.0
	if e.cfg.StartingEquity > 0 {
		ret = total / e.cfg.StartingEquity * 100
	}
	return Session{
		State: e.state, Config: e.cfg,
		StartedAt: e.startedAt, StoppedAt: e.stoppedAt,
		Equity: round2(equity), Cash: round2(e.cash),
		Realized: round2(realized), Unrealized: round2(unrealized),
		TotalPnL: round2(total), ReturnPct: round4(ret),
		Positions: positions, PeakEquity: round2(e.peakEquity), MaxDrawdn: round4(e.maxDrawdn),
		Watchlist:     append([]Scored(nil), e.selected...),
		EntriesFilled: e.entriesFilled, EntriesVetoed: e.entriesVetoed,
		EntriesBlocked: e.entriesBlocked,
		Note:           "perfect liquidity assumed: fills at the signal price with no slippage or market impact, so this PnL is an upper bound and not an executable result",
	}
}

// Session returns the current session state.
func (e *Engine) Session() Session {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.sessionLocked()
}

// Trades and Orders return copies, newest first, so a caller cannot mutate
// engine state through the returned slice.
func (e *Engine) Trades() []Trade {
	e.mu.Lock()
	defer e.mu.Unlock()
	out := make([]Trade, len(e.trades))
	copy(out, e.trades)
	for i, j := 0, len(out)-1; i < j; i, j = i+1, j-1 {
		out[i], out[j] = out[j], out[i]
	}
	return out
}

func (e *Engine) Orders() []Order {
	e.mu.Lock()
	defer e.mu.Unlock()
	out := make([]Order, len(e.orders))
	copy(out, e.orders)
	for i, j := 0, len(out)-1; i < j; i, j = i+1, j-1 {
		out[i], out[j] = out[j], out[i]
	}
	return out
}

// ------------------------------------------------------------------ helpers

func sideFor(kind string) string {
	switch kind {
	case "PUMP", "BREAKOUT_UP", "REVERSAL_UP":
		return "long"
	case "DUMP", "BREAKOUT_DOWN", "REVERSAL_DOWN":
		return "short"
	}
	return ""
}

func exitType(reason string) string {
	if reason == "2N stop" {
		return "stop"
	}
	return "exit"
}

func (e *Engine) nextOrderIDLocked() string {
	e.orderSeq++
	return fmt.Sprintf("t%04d", e.orderSeq)
}

func atOrNow(e *Engine, p *Position) time.Time { return e.now() }

func sumNotional(m map[string]*Position) float64 {
	t := 0.0
	for _, p := range m {
		t += p.Notional
	}
	return t
}

func sortPositions(p []Position) {
	// Newest first: the dashboard shows recent action at the top.
	for i := 1; i < len(p); i++ {
		for j := i; j > 0 && p[j].OpenedAt.After(p[j-1].OpenedAt); j-- {
			p[j], p[j-1] = p[j-1], p[j]
		}
	}
}

func round2(v float64) float64     { return math.Round(v*100) / 100 }
func round4(v float64) float64     { return math.Round(v*10000) / 10000 }
func timeOr(t time.Time) time.Time { return t }
func abs(v float64) float64 {
	if v < 0 {
		return -v
	}
	return v
}

func (e *Engine) realizedLocked() float64 {
	total := 0.0
	for _, t := range e.trades {
		total += t.PnL
	}
	return total
}
