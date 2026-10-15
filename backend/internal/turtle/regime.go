package turtle

import (
	"context"
	"math"
	"time"
)

// btcRegime evaluates the BTC regime gate.
//
// Alt trend quality is dominated by BTC beta: when BTC itself is breaking down,
// an alt 55-bar breakout is mostly beta rather than independent signal, and
// correlations converge toward 0.9 exactly when it hurts most. The gate is
// intentionally simple -- BTC's close against its own Donchian low -- because a
// complicated filter here would be tuned to the last cycle.
//
// Failure to fetch BTC is reported, not assumed safe: `risk_off` stays false so
// the survey still produces output, but the error is surfaced in the payload so
// a missing gate is visible rather than silent.
func (s *Survey) btcRegime(ctx context.Context) map[string]any {
	out := map[string]any{
		"symbol":   s.params.BTCSymbol,
		"period":   s.params.BTCGatePeriod,
		"interval": s.params.BTCGateInterval,
		"risk_off": false,
	}
	fetchCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()

	limit := s.params.BTCGatePeriod * 2
	if limit < 120 {
		limit = 120
	}
	bars, err := s.client.Klines(fetchCtx, s.params.BTCSymbol, s.params.BTCGateInterval, limit)
	if err != nil {
		// FAIL CLOSED. A risk gate that silently disarms during exactly the
		// infrastructure failure you most need it for is worse than no gate at
		// all, because you believe you are protected. An unavailable gate
		// blocks new entries; it is surfaced in the payload and logged.
		out["error"] = err.Error()
		out["risk_off"] = true
		return out
	}
	// Completed daily bars only, for the same reason as the survey.
	bars = dropFormingBar(bars)
	if len(bars) < s.params.BTCGatePeriod+1 {
		out["error"] = "insufficient BTC history"
		out["risk_off"] = true // fail closed
		return out
	}
	closePrice := bars[len(bars)-1].Close
	level := priorLowestLow(bars, s.params.BTCGatePeriod)
	out["close"] = closePrice
	if math.IsNaN(level) {
		out["error"] = "no gate level"
		out["risk_off"] = true // fail closed
		return out
	}
	out["gate_low"] = level
	if level > 0 {
		out["distance_pct"] = (closePrice - level) / level
	}

	// Latched state with hysteresis. A live threshold would re-open the gate
	// every time price ticked back above the low, which is precisely when the
	// worst alt entries happen.
	high := priorHighestHigh(bars, s.params.BTCGatePeriod)
	if !math.IsNaN(high) {
		out["gate_high"] = high
	}
	s.mu.Lock()
	previous := s.btcRiskOff
	latched := s.btcLatched
	switch {
	case !latched:
		// First evaluation: adopt the live state so startup is not blind.
		s.btcRiskOff = closePrice < level
		s.btcLatched = true
	case previous && !math.IsNaN(high) && closePrice > high:
		s.btcRiskOff = false
	case !previous && closePrice < level:
		s.btcRiskOff = true
	}
	current := s.btcRiskOff
	s.mu.Unlock()

	out["risk_off"] = current
	out["latched"] = true
	out["changed"] = latched && current != previous
	return out
}
