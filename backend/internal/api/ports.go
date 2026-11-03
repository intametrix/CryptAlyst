package api

import (
	"context"

	"pumpdump/backend/internal/binance"
)

// Ports, so the server can be tested without the world.
//
// Deps held concrete types, which meant a handler could only be exercised by
// building the whole platform: no test could answer "what does the backtest do
// when the exchange returns 300 bars and then fails?" This is the seam that lets a
// mock stand in.

// KlinesSource is the market data the API needs: candles for a symbol and frame.
// The REST client implements it; so does the test mock.
type KlinesSource interface {
	Klines(ctx context.Context, symbol, interval string, limit int) ([]binance.Kline, error)
}
