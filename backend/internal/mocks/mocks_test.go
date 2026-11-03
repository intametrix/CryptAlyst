package mocks

import (
	"context"
	"testing"
)

// The mocks must be as honest as the code they stand in for: a fake that cannot
// fail tests nothing.
func TestMarketCanFailOnDemand(t *testing.T) {
	m := &Market{Bars: Series("BTCUSDT", 10, 100, 1000, 0), FailAfter: 1}
	ctx := context.Background()
	if _, err := m.Klines(ctx, "BTCUSDT", "1h", 5); err != nil {
		t.Fatalf("first call should succeed: %v", err)
	}
	if _, err := m.Klines(ctx, "BTCUSDT", "1h", 5); err == nil {
		t.Fatal("second call should fail once FailAfter is exceeded")
	}
	if m.CallCount() != 2 {
		t.Fatalf("calls recorded = %d, want 2", m.CallCount())
	}
}

func TestSeriesIsDeterministicAndBounded(t *testing.T) {
	a := Series("X", 50, 100, 1000, 1_000)
	b := Series("X", 50, 100, 1000, 1_000)
	if len(a) != 50 || len(b) != 50 {
		t.Fatalf("lengths = %d, %d", len(a), len(b))
	}
	for i := range a {
		if a[i].Close != b[i].Close || a[i].OpenTime != b[i].OpenTime {
			t.Fatalf("series is not deterministic at %d", i)
		}
		if a[i].Low > a[i].Close || a[i].High < a[i].Close {
			t.Fatalf("bar %d has an impossible range: %+v", i, a[i])
		}
	}
}

func TestMarketHonoursTheLimit(t *testing.T) {
	m := &Market{Bars: Series("X", 100, 100, 1000, 0)}
	got, err := m.Klines(context.Background(), "X", "1h", 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 10 {
		t.Fatalf("returned %d bars for limit 10", len(got))
	}
}
