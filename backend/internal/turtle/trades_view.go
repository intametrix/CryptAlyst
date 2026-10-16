package turtle

import "time"

// Open and settled trades as two distinct lists.
//
// The human's instruction: "As soon as you trade the trade should appear in
// trades. Trades closed out should be in settled. Unrealized PnL should be
// displayed at the trade level."
//
// WHY THESE ARE TWO LISTS RATHER THAN ONE WITH A STATUS COLUMN. They answer
// different questions. An OPEN trade is a position the account is carrying and its
// number is moving right now; a SETTLED trade is a finished fact whose number can
// never change. Mixing them means a reader cannot tell whether the PnL in front of
// them is still in play, and sorting by time puts a live position and a closed one
// in the same column of numbers with different meanings.
//
// The alternative -- one list filtered in the UI -- was rejected because it makes
// the distinction a presentation choice rather than a fact about the data, and a
// client that forgot to filter would silently show settled PnL as though it were
// live.

// OpenTrade is a position the account is currently carrying.
type OpenTrade struct {
	Symbol string `json:"symbol"`
	Side   string `json:"side"`
	// SideLabel is BUY or SELL, which is what a trade blotter shows.
	SideLabel string  `json:"side_label"`
	Units     float64 `json:"units"`
	Entry     float64 `json:"entry_price"`
	Last      float64 `json:"last_price"`
	Stop      float64 `json:"stop_price"`
	N         float64 `json:"n"`
	Notional  float64 `json:"notional"`

	// UnrealizedPnL is the point of this struct: the PnL AT THE TRADE LEVEL, which
	// is what a trader looks at rather than a portfolio total.
	UnrealizedPnL    float64 `json:"unrealized_pnl"`
	UnrealizedPnLPct float64 `json:"unrealized_pnl_pct"`
	// DistanceToStopPct is how far price must move to hit the stop. It answers
	// "how much room does this have left" without arithmetic.
	DistanceToStopPct float64 `json:"distance_to_stop_pct"`
	// RiskUSD is what this trade loses if the stop is hit, at its current size.
	RiskUSD float64 `json:"risk_usd"`

	OpenedAt   time.Time `json:"opened_at"`
	HoldSec    float64   `json:"hold_sec"`
	AlertID    string    `json:"alert_id"`
	Multiplier float64   `json:"position_multiple"`
}

// SettledTrade is a completed round trip. The PnL can never change again.
type SettledTrade struct {
	Symbol    string    `json:"symbol"`
	Side      string    `json:"side"`
	SideLabel string    `json:"side_label"`
	Units     float64   `json:"units"`
	Entry     float64   `json:"entry_price"`
	Exit      float64   `json:"exit_price"`
	Notional  float64   `json:"notional"`
	PnL       float64   `json:"pnl"`
	PnLPct    float64   `json:"pnl_pct"`
	OpenedAt  time.Time `json:"opened_at"`
	ClosedAt  time.Time `json:"closed_at"`
	HoldSec   float64   `json:"hold_sec"`
	Reason    string    `json:"reason"`
	AlertID   string    `json:"alert_id"`
}

// sideLabel renders a side the way a trade blotter does.
func sideLabel(side string) string {
	if side == "short" {
		return "SELL"
	}
	return "BUY"
}

// OpenTrades returns the live positions with their PnL marked to the current price.
func (e *Engine) OpenTrades() []OpenTrade {
	e.mu.Lock()
	defer e.mu.Unlock()
	now := e.now()
	out := make([]OpenTrade, 0, len(e.positions))
	for _, p := range e.positions {
		dir := 1.0
		if p.Side == "short" {
			dir = -1
		}
		pnl := (p.Last - p.EntryPrice) * p.Units * dir
		pct := 0.0
		if p.Notional > 0 {
			pct = pnl / p.Notional * 100
		}
		// How far price has to move to reach the stop, as a percentage. The sign is
		// always positive: it is a distance, not a direction.
		dist := 0.0
		if p.Last > 0 {
			d := (p.Last - p.StopPrice) * dir
			dist = d / p.Last * 100
		}
		risk := (p.EntryPrice - p.StopPrice) * p.Units * dir
		if risk < 0 {
			risk = -risk
		}
		out = append(out, OpenTrade{
			Symbol: p.Symbol, Side: p.Side, SideLabel: sideLabel(p.Side),
			Units: p.Units, Entry: p.EntryPrice, Last: p.Last, Stop: p.StopPrice,
			N: p.N, Notional: round2(p.Notional),
			UnrealizedPnL: round2(pnl), UnrealizedPnLPct: round4(pct),
			DistanceToStopPct: round4(dist), RiskUSD: round2(risk),
			OpenedAt: p.OpenedAt, HoldSec: round4(now.Sub(p.OpenedAt).Seconds()),
			AlertID: p.AlertID, Multiplier: round4(p.Units * p.EntryPrice / maxFloat(e.cfg.StartingEquity, 1)),
		})
	}
	// Newest first: a blotter shows the most recent action at the top.
	for i := 1; i < len(out); i++ {
		for j := i; j > 0 && out[j].OpenedAt.After(out[j-1].OpenedAt); j-- {
			out[j], out[j-1] = out[j-1], out[j]
		}
	}
	return out
}

// SettledTrades returns completed round trips, newest first.
func (e *Engine) SettledTrades() []SettledTrade {
	e.mu.Lock()
	defer e.mu.Unlock()
	out := make([]SettledTrade, 0, len(e.trades))
	for i := len(e.trades) - 1; i >= 0; i-- {
		t := e.trades[i]
		notional := t.EntryPrice * t.Units
		out = append(out, SettledTrade{
			Symbol: t.Symbol, Side: t.Side, SideLabel: sideLabel(t.Side),
			Units: t.Units, Entry: t.EntryPrice, Exit: t.ExitPrice,
			Notional: round2(notional), PnL: t.PnL, PnLPct: t.PnLPct,
			OpenedAt: t.OpenedAt, ClosedAt: t.ClosedAt,
			HoldSec: round4(t.HoldSec), Reason: t.Reason, AlertID: t.AlertID,
		})
	}
	return out
}

func maxFloat(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}
