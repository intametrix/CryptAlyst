package api

import (
	"context"
	"fmt"
	"strings"

	"pumpdump/backend/internal/binance"
)

// One forecast pipeline: validate the request, fetch the bars, check the window.
//
// Twelve call sites each decided for themselves what "enough data" means — 140+
// horizon, 120+horizon, 120, 90, 60+horizon — and the sub-minute frames broke
// because none of the numbers was a function of the frame. The policy lives here
// now, keyed by interval, so adding a frame is a row in a table rather than a
// search through the handlers.

// intervalPolicy describes what a frame needs before it can be forecast.
type intervalPolicy struct {
	// warmup is the smallest window any model can be fitted on.
	warmup int
	// maxBars caps what is requested, so a client cannot ask for a gigabyte.
	maxBars int
}

var intervalPolicies = map[string]intervalPolicy{
	"1s":  {warmup: 120, maxBars: 1000},
	"5s":  {warmup: 40, maxBars: 1000},
	"30s": {warmup: 20, maxBars: 1000},
	"1m":  {warmup: 120, maxBars: 1500},
	"5m":  {warmup: 120, maxBars: 1500},
	"15m": {warmup: 120, maxBars: 1500},
	"30m": {warmup: 120, maxBars: 1500},
	"1h":  {warmup: 120, maxBars: 1000},
	"4h":  {warmup: 120, maxBars: 1000},
	"1d":  {warmup: 120, maxBars: 1000},
}

// policyFor answers the policy for a frame, defaulting to the hourly rule.
func policyFor(interval string) intervalPolicy {
	if p, ok := intervalPolicies[strings.ToLower(interval)]; ok {
		return p
	}
	return intervalPolicy{warmup: 120, maxBars: 1000}
}

// minBarsFor is the smallest series this frame can be scored on.
func (p intervalPolicy) minBarsFor(horizon int) int {
	if horizon < 1 {
		horizon = 1
	}
	return p.warmup + horizon
}

// forecastRequest is the validated shape every forecast endpoint takes.
type forecastRequest struct {
	Symbol   string
	Interval string
	Horizon  int
	Bars     int
	Lookback int
	FromMS   int64
	ToMS     int64
	Method   string
}

// readForecast validates the common query parameters for a forecast endpoint.
func readForecast(r *queryReader) (forecastRequest, error) {
	req := forecastRequest{
		Symbol:   r.symbol(),
		Interval: r.interval("1h"),
		Horizon:  r.int("horizon", 6, 1, 720),
		Bars:     r.int("bars", 240, 20, 5000),
		Lookback: r.int("lookback", 0, 0, 500),
		FromMS:   r.int64("from_ms", 0),
		ToMS:     r.int64("to_ms", 0),
		Method:   r.text("method", ""),
	}
	if req.Symbol == "" {
		return forecastRequest{}, fmt.Errorf("symbol required")
	}
	if p := policyFor(req.Interval); req.Bars > p.maxBars {
		req.Bars = p.maxBars
	}
	return req, nil
}

// barsForRequest fetches the series for a validated request and enforces the
// frame's minimum, so every caller reports the same refusal.
func (s *Server) barsForRequest(ctx context.Context, req forecastRequest, extra int) ([]binance.Kline, error) {
	want := req.Bars + req.Horizon + extra
	bars, err := s.barsFor(ctx, req.Symbol, req.Interval, want)
	if err != nil {
		return nil, err
	}
	min := policyFor(req.Interval).minBarsFor(req.Horizon)
	if len(bars) < min {
		return nil, fmt.Errorf("not enough bars: %d of %d needed on %s", len(bars), min, req.Interval)
	}
	return bars, nil
}
