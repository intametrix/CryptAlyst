// Package mocks provides test doubles for the platform's ports.
//
// The rule these exist to make possible: a handler is tested with generated data
// and no network, no wall clock and no machine-specific path. Every mock here is
// deterministic and records what it was asked, so a test can assert on the
// interaction as well as the answer.
package mocks

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"

	"pumpdump/backend/internal/binance"
)

// Market is a candle source that answers from a fixture instead of an exchange.
type Market struct {
	mu sync.Mutex
	// Bars is returned for any request; Klines slices the tail to the requested limit.
	Bars []binance.Kline
	// Err, when set, is returned instead of bars — the failure path.
	Err error
	// FailAfter makes the first N calls succeed and the rest fail, so a test can
	// drive "the data stopped arriving".
	FailAfter int
	// Delay is applied per call, for timeout tests.
	Delay time.Duration
	// Calls records every request, in order.
	Calls []Call
}

// Call is one recorded Klines request.
type Call struct {
	Symbol   string
	Interval string
	Limit    int
}

// Klines implements api.KlinesSource.
func (m *Market) Klines(ctx context.Context, symbol, interval string, limit int) ([]binance.Kline, error) {
	if m.Delay > 0 {
		select {
		case <-time.After(m.Delay):
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	}
	m.mu.Lock()
	m.Calls = append(m.Calls, Call{Symbol: symbol, Interval: interval, Limit: limit})
	n := len(m.Calls)
	failAfter := m.FailAfter
	err := m.Err
	bars := m.Bars
	m.mu.Unlock()

	if err != nil {
		return nil, err
	}
	if failAfter > 0 && n > failAfter {
		return nil, errors.New("mock market: stopped answering")
	}
	if limit > 0 && len(bars) > limit {
		bars = bars[len(bars)-limit:]
	}
	out := make([]binance.Kline, len(bars))
	copy(out, bars)
	return out, nil
}

// CallCount returns how many requests were made.
func (m *Market) CallCount() int {
	m.mu.Lock()
	defer m.mu.Unlock()
	return len(m.Calls)
}

// Series builds a deterministic candle series: a sine wave around a base price, so
// a test can assert a trend, a reversal and a flat stretch without a fixture file.
func Series(symbol string, count int, base, stepMS float64, startMS int64) []binance.Kline {
	out := make([]binance.Kline, 0, count)
	price := base
	for i := 0; i < count; i++ {
		// A gentle drift with a small oscillation, deterministic for a given index.
		drift := 1 + 0.0007*float64(i%17-8)/8
		closePx := base*drift + float64(i%5)*0.01
		openPx := price
		high := closePx
		if openPx > high {
			high = openPx
		}
		low := closePx
		if openPx < low {
			low = openPx
		}
		out = append(out, binance.Kline{
			OpenTime: startMS + int64(float64(i)*stepMS),
			Open:     openPx,
			High:     high,
			Low:      low,
			Close:    closePx,
			Volume:   1 + float64(i%7),
		})
		price = closePx
	}
	_ = symbol
	return out
}

// FlatSeries is a series that never moves — the case a naive model should win and a
// trend model should not invent one.
func FlatSeries(count int, base, stepMS float64, startMS int64) []binance.Kline {
	out := make([]binance.Kline, 0, count)
	for i := 0; i < count; i++ {
		out = append(out, binance.Kline{
			OpenTime: startMS + int64(float64(i)*stepMS),
			Open:     base, High: base, Low: base, Close: base, Volume: 1,
		})
	}
	return out
}

// String makes a market readable in a failure message.
func (m *Market) String() string {
	return fmt.Sprintf("mocks.Market(calls=%d, bars=%d)", m.CallCount(), len(m.Bars))
}
