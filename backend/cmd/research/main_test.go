package main

import (
	"math"
	"testing"

	"pumpdump/backend/internal/binance"
)

// mkFlat returns n hourly bars at a flat price with a ±1% range, so the true
// range is 2 and ATR(any period) is 2 (2% of price). Deterministic; no network.
func mkFlat(n int, price float64) []binance.Kline {
	bars := make([]binance.Kline, n)
	for i := range bars {
		bars[i] = binance.Kline{
			OpenTime:     int64(i) * 3_600_000,
			Open:         price,
			High:         price * 1.01,
			Low:          price * 0.99,
			Close:        price,
			Volume:       1000,
			TakerBuyBase: 500,
		}
	}
	return bars
}

func TestLabelAtUpFirst(t *testing.T) {
	cfg := DefaultConfig()
	cfg.HorizonBars = 5
	cfg.UpATR = 2.0
	cfg.DownATR = 2.0
	cfg.ATRPeriod = 14

	bars := mkFlat(40, 100)
	// Event bar i=30, entry=100, atrPct=0.02. Up barrier=104, down=96.
	bars[31] = binance.Kline{OpenTime: bars[31].OpenTime, Open: 100, High: 105, Low: 98, Close: 104, Volume: 1000, TakerBuyBase: 800}
	up, down, neither := labelAt(bars, 30, 1, cfg)
	if !up || down || neither {
		t.Fatalf("want UP first, got up=%v down=%v neither=%v", up, down, neither)
	}
}

func TestLabelAtDownFirst(t *testing.T) {
	cfg := DefaultConfig()
	cfg.HorizonBars = 5
	cfg.UpATR = 2.0
	cfg.DownATR = 2.0
	cfg.ATRPeriod = 14

	bars := mkFlat(40, 100)
	// Low=95 <= down barrier 96; High=99 < up barrier 104.
	bars[31] = binance.Kline{OpenTime: bars[31].OpenTime, Open: 100, High: 99, Low: 95, Close: 96, Volume: 1000, TakerBuyBase: 200}
	up, down, neither := labelAt(bars, 30, -1, cfg)
	if up || !down || neither {
		t.Fatalf("want DOWN first, got up=%v down=%v neither=%v", up, down, neither)
	}
}

func TestLabelAtNeither(t *testing.T) {
	cfg := DefaultConfig()
	cfg.HorizonBars = 5
	cfg.UpATR = 2.0
	cfg.DownATR = 2.0
	cfg.ATRPeriod = 14

	bars := mkFlat(40, 100) // flat: never touches either barrier
	up, down, neither := labelAt(bars, 30, 1, cfg)
	if up || down || !neither {
		t.Fatalf("want NEITHER, got up=%v down=%v neither=%v", up, down, neither)
	}
}

func TestATRPctKnown(t *testing.T) {
	bars := mkFlat(20, 100)
	got := atrPctAt(bars, 19, 14)
	want := 0.02 // ATR 2 / price 100
	if math.Abs(got-want) > 1e-9 {
		t.Fatalf("atrPctAt = %v, want %v", got, want)
	}
}

func TestRSIAtRisingIsHigh(t *testing.T) {
	bars := make([]binance.Kline, 20)
	for i := range bars {
		p := 100 + float64(i) // monotonically rising
		bars[i] = binance.Kline{OpenTime: int64(i) * 3_600_000, Open: p - 1, High: p + 1, Low: p - 2, Close: p}
	}
	rsi := rsiAt(bars, 19, 14)
	if rsi < 99 {
		t.Fatalf("rsi for a pure uptrend = %v, want ~100", rsi)
	}
}

func TestBuildEventsEmitsOnlyBreakouts(t *testing.T) {
	cfg := DefaultConfig()
	cfg.RangeBars = 10
	cfg.ATRPeriod = 14
	cfg.BreakoutATR = 0.5
	cfg.HorizonBars = 5

	bars := mkFlat(60, 100)
	// A clean up-breakout: bar 40 jumps to 103 (beyond range_high + 0.5*ATR).
	bars[40] = binance.Kline{OpenTime: bars[40].OpenTime, Open: 100, High: 103, Low: 100, Close: 103, Volume: 5000, TakerBuyBase: 4000}

	events := buildEvents("AAAUSDT", bars, nil, cfg)
	if len(events) == 0 {
		t.Fatal("expected at least one breakout event")
	}
	found := false
	for _, e := range events {
		if e.Direction != 1 {
			continue
		}
		found = true
		if e.Features["atr_pct"] == 0 {
			t.Fatalf("breakout event missing atr_pct feature")
		}
		if e.Features["taker_buy_ratio"] == 0 {
			t.Fatalf("breakout event missing taker_buy_ratio")
		}
	}
	if !found {
		t.Fatal("expected an up-breakout event with direction +1")
	}
}

func TestIntervalMS(t *testing.T) {
	cases := map[string]int64{"1m": 60_000, "5m": 300_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
	for iv, want := range cases {
		if got := intervalMS(iv); got != want {
			t.Fatalf("intervalMS(%q)=%d want %d", iv, got, want)
		}
	}
}
