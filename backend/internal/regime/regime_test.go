package regime

import (
	"testing"

	"pumpdump/backend/internal/market"
)

func ctx(sym string, chg float64) market.Context {
	return market.Context{Symbol: sym, PriceChangePct: chg}
}

func TestComputeRising(t *testing.T) {
	ctxs := []market.Context{
		ctx("BTCUSDT", 2.5), ctx("ETHUSDT", 2.0),
		ctx("SOLUSDT", 3.0), ctx("ADAUSDT", 1.5), ctx("DOGEUSDT", 4.0),
	}
	r := Compute(ctxs)
	if r.Label != Rising {
		t.Fatalf("want rising, got %s (score %.3f)", r.Label, r.Score)
	}
	if r.BreadthUp != 1.0 {
		t.Fatalf("breadth_up = %v, want 1.0", r.BreadthUp)
	}
	if r.BTCRet24 != 2.5 || r.ETHRet24 != 2.0 {
		t.Fatalf("btc/eth not extracted: %v/%v", r.BTCRet24, r.ETHRet24)
	}
}

func TestComputeFalling(t *testing.T) {
	ctxs := []market.Context{
		ctx("BTCUSDT", -3.0), ctx("ETHUSDT", -2.0),
		ctx("SOLUSDT", -4.0), ctx("ADAUSDT", -1.0), ctx("DOGEUSDT", -5.0),
	}
	r := Compute(ctxs)
	if r.Label != Falling {
		t.Fatalf("want falling, got %s (score %.3f)", r.Label, r.Score)
	}
}

func TestComputeSideways(t *testing.T) {
	ctxs := []market.Context{
		ctx("BTCUSDT", 0.3), ctx("ETHUSDT", -0.4),
		ctx("SOLUSDT", 0.1), ctx("ADAUSDT", -0.2), ctx("DOGEUSDT", 0.0),
	}
	r := Compute(ctxs)
	if r.Label != Sideways {
		t.Fatalf("want sideways, got %s (score %.3f)", r.Label, r.Score)
	}
}

func TestComputeEmpty(t *testing.T) {
	r := Compute(nil)
	if r.Label != Sideways {
		t.Fatalf("empty universe must be sideways, got %s", r.Label)
	}
}
