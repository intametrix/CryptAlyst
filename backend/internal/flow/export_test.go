package flow

import (
	"fmt"
	"testing"
)

func TestHistory(t *testing.T) {
	e := NewEngine(DefaultConfig())
	const symbol = "AAAUSDT"

	// Seed the baseline with 8 fully-covered 10s buckets of flow 100 each, so
	// the baseline reference is exactly 100. OpenTimes at 0..79s land in
	// buckets 0..7 with 10 one-second bars each.
	bars := make([]Series1s, 0, 80)
	for i := 0; i < 80; i++ {
		bars = append(bars, Series1s{OpenTime: int64(i * 1000), Close: 100, QuoteVolume: 10})
	}
	if n := e.SeedBaseline(symbol, bars); n != 8 {
		t.Fatalf("seeded %d baseline samples, want 8", n)
	}
	// Keep the 24h volume (and hence the liquidity floor) negligible so the
	// floor cannot lift the reference above 100.
	e.SetQuoteVolume(symbol, 1)

	// Ingest 30 ticks across three 10-second buckets. Each tick adds exactly
	// 50 of quote volume (differenced), and the price rises 0.1 per tick.
	quoteVol := 1000.0
	for i := 0; i < 30; i++ {
		quoteVol += 50
		ts := int64(100_000 + i*1000)
		payload := fmt.Sprintf(
			`{"E":%d,"s":%q,"c":"%.4f","q":"%.4f","v":"0","n":"1","b":"99","B":"1","a":"101","A":"1"}`,
			ts, symbol, 100.0+float64(i)*0.1, quoteVol)
		if got := e.IngestTicker([]byte(payload)); got != symbol {
			t.Fatalf("tick %d not ingested (got %q)", i, got)
		}
	}

	bins := e.History(symbol, 200)
	if len(bins) != 3 {
		t.Fatalf("got %d bins, want 3: %+v", len(bins), bins)
	}
	// Oldest first, T is bucket close time.
	wantT := []int64{110_000, 120_000, 130_000}
	wantFlow := []float64{450, 500, 500} // first tick of bucket 10 has no delta
	for i, b := range bins {
		if b.T != wantT[i] {
			t.Errorf("bin %d T = %d, want %d", i, b.T, wantT[i])
		}
		if b.FlowQuote != wantFlow[i] {
			t.Errorf("bin %d flow = %v, want %v", i, b.FlowQuote, wantFlow[i])
		}
		if b.BaselineQuote != 100 {
			t.Errorf("bin %d baseline = %v, want 100", i, b.BaselineQuote)
		}
		if b.BurstMultiple != wantFlow[i]/100 {
			t.Errorf("bin %d burst = %v, want %v", i, b.BurstMultiple, wantFlow[i]/100)
		}
	}
	// Change10s: first to last tick price inside the bin.
	if d := bins[0].Change10s - 0.9/100.0; d > 1e-12 || d < -1e-12 {
		t.Errorf("bin 0 change = %v, want %v", bins[0].Change10s, 0.9/100.0)
	}

	// bins caps to the most recent N.
	if two := e.History(symbol, 2); len(two) != 2 || two[0].T != 120_000 {
		t.Fatalf("capped history wrong: %+v", two)
	}

	// Unknown symbol yields an empty slice, not nil and not a crash.
	if unknown := e.History("NOPEUSDT", 10); unknown == nil || len(unknown) != 0 {
		t.Fatalf("unknown symbol returned %v, want empty non-nil slice", unknown)
	}
}
