package turtle

import "time"

// Marking every open position, continuously.
//
// THE BUG THIS FIXES. Mark() was called from exactly one place: the alert feed
// handler. So a position's `last` price -- and therefore its unrealised PnL -- only
// moved when a NEW ALERT fired for that symbol. Between alerts the PnL sat frozen
// at its entry value, and the session's equity, return and drawdown were all
// computed from stale marks.
//
// That is worse than a display problem. The 2N stop is checked inside Mark(), so a
// position could pass straight through its stop and never be closed, because
// nothing marked it. The stop only fired if an unrelated alert happened to arrive
// for the same symbol afterwards -- meaning STOP LOSSES WERE LUCK, not rules.
//
// A session that reports PnL is claiming to know the value of what it holds. That
// claim is only true if every open position is marked against the current market,
// on a clock, whether or not anything else is happening.

// MarkAll updates every open position from a price snapshot and fires any stops.
//
// Returns the number of positions closed, so the caller can log a stop rather than
// have it appear silently in the trade list.
func (e *Engine) MarkAll(prices map[string]float64, at time.Time) int {
	e.mu.Lock()
	if e.state != StateRunning || len(e.positions) == 0 {
		e.mu.Unlock()
		return 0
	}
	// Snapshot the symbols first: closeLocked deletes from the map, and mutating a
	// map while ranging over it is undefined in Go.
	symbols := make([]string, 0, len(e.positions))
	for sym := range e.positions {
		symbols = append(symbols, sym)
	}
	e.mu.Unlock()

	closed := 0
	for _, sym := range symbols {
		price, ok := prices[sym]
		if !ok || price <= 0 {
			// No price for this symbol yet. Skipped rather than marked with a zero,
			// which would look like a total loss and instantly trip every stop.
			continue
		}
		e.mu.Lock()
		pos, still := e.positions[sym]
		if still {
			pos.Last = price
			hit := (pos.Side == "long" && price <= pos.StopPrice) ||
				(pos.Side == "short" && price >= pos.StopPrice)
			if hit && e.state == StateRunning {
				e.closeLocked(sym, pos, pos.StopPrice, "2N stop")
				closed++
			}
		}
		e.mu.Unlock()
	}
	return closed
}

// EquityCurvePoint is one sample of the running equity, which is what makes a PnL
// chart possible rather than a single number that moves.
type EquityCurvePoint struct {
	AtMS       int64   `json:"at_ms"`
	Equity     float64 `json:"equity"`
	Realized   float64 `json:"realized"`
	Unrealized float64 `json:"unrealized"`
	Open       int     `json:"open_positions"`
}

// SampleEquity appends the current equity to the curve, throttled so a 1Hz mark
// loop does not produce a point every second forever.
func (e *Engine) SampleEquity(at time.Time) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.state != StateRunning {
		return
	}
	// One point every 5 seconds: dense enough that a PnL chart has shape, sparse
	// enough that an hour of session is 720 points rather than 3600.
	if n := len(e.equityCurve); n > 0 && at.UnixMilli()-e.equityCurve[n-1].AtMS < 5000 {
		return
	}
	e.equityCurve = append(e.equityCurve, EquityCurvePoint{
		AtMS: at.UnixMilli(), Equity: round2(e.equityLocked()),
		Realized: round2(e.realizedLocked()), Unrealized: round2(e.openValueLocked() - sumNotional(e.positions)),
		Open: len(e.positions),
	})
	if len(e.equityCurve) > 2000 {
		e.equityCurve = e.equityCurve[len(e.equityCurve)-1000:]
	}
}

// EquityCurve returns the sampled equity series, oldest first, for a PnL chart.
func (e *Engine) EquityCurve() []EquityCurvePoint {
	e.mu.Lock()
	defer e.mu.Unlock()
	return append([]EquityCurvePoint(nil), e.equityCurve...)
}

// RunMarks marks every open position on a clock, independently of alerts.
func (e *Engine) RunMarks(prices func() map[string]float64, every time.Duration) <-chan struct{} {
	done := make(chan struct{})
	go func() {
		defer close(done)
		ticker := time.NewTicker(every)
		defer ticker.Stop()
		for range ticker.C {
			if e.Session().State != StateRunning {
				continue
			}
			if closed := e.MarkAll(prices(), time.Now()); closed > 0 {
				// Logged because a stop is an event the human should be able to see
				// happening, not something that appears later in a list.
				markNote(closed)
			}
			e.SampleEquity(time.Now())
		}
	}()
	return done
}

var markNote = func(closed int) {}
