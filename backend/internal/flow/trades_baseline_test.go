package flow

import (
	"fmt"
	"testing"
)

// TestTradesMultipleIsCountOverCount is the regression test for the unit
// mismatch that made trades_multiple meaningless: it used to divide a trade
// COUNT by the flow baseline's median quote DOLLARS (e.g. 120 trades / $7,700
// = 0.0156). The metric now divides by a trades baseline fed from the same
// per-bucket close, so a 10x trade burst reads as ~10x.
func TestTradesMultipleIsCountOverCount(t *testing.T) {
	e := NewEngine(DefaultConfig())
	const symbol = "BBBUSDT"

	// Seed the flow baseline only; the trades baseline cannot be seeded from
	// klines (no per-window trade counts), so it must fill from live ticks.
	bars := make([]Series1s, 0, 80)
	for i := 0; i < 80; i++ {
		bars = append(bars, Series1s{OpenTime: int64(i * 1000), Close: 100, QuoteVolume: 10})
	}
	e.SeedBaseline(symbol, bars)
	e.SetQuoteVolume(symbol, 1)

	ingest := func(ts int64, price, cumQuote float64, cumTrades int64) {
		payload := fmt.Sprintf(
			`{"E":%d,"s":%q,"c":"%.4f","q":"%.4f","v":"0","n":"%d","P":"0",`+
				`"b":"99.99","B":"10","a":"100.01","A":"10"}`,
			ts, symbol, price, cumQuote, cumTrades)
		if got := e.IngestTicker([]byte(payload)); got != symbol {
			t.Fatalf("tick at %d not ingested (got %q)", ts, got)
		}
	}

	// 8 quiet buckets: flow 10/tick, 5 trades/tick, price flat. Each bucket
	// closes 100 flow and 50 trades, so both baselines' references are 100
	// and 50 respectively.
	var cumQuote float64 = 1000
	var cumTrades int64 = 1000
	base := int64(200_000)
	for i := 0; i < 80; i++ {
		cumQuote += 10
		cumTrades += 5
		ingest(base+int64(i*1000), 100, cumQuote, cumTrades)
	}

	// Burst bucket: flow 1000/tick, 50 trades/tick, price +0.05% per tick.
	burstStart := base + 80_000
	for i := 0; i < 10; i++ {
		cumQuote += 1000
		cumTrades += 50
		ingest(burstStart+int64(i*1000), 100*(1+0.0005*float64(i+1)), cumQuote, cumTrades)
	}

	now := burstStart + 9_000
	candidates := e.Detect(now)
	if len(candidates) != 1 {
		t.Fatalf("got %d candidates, want 1", len(candidates))
	}
	c := candidates[0]
	// The window's lower bound is inclusive, so the last quiet tick (one
	// second before the burst) also counts: 9 burst ticks + 1 quiet tick.
	if c.FlowQuote != 10_010 {
		t.Errorf("flow = %v, want 10010", c.FlowQuote)
	}
	if c.Trades10s != 505 {
		t.Errorf("trades = %d, want 505", c.Trades10s)
	}
	// 505 trades against a reference of 50 quiet-bucket trades.
	if c.TradesMultiple < 9.9 || c.TradesMultiple > 10.2 {
		t.Errorf("trades_multiple = %v, want ~10 (count over count)", c.TradesMultiple)
	}
}
