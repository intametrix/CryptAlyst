package microburst

import (
	"testing"

	"pumpdump/backend/internal/binance"
)

// mkBars builds n one-second bars that are flat until `jump` seconds from the
// end, where they rise by jumpPct, then stay flat. Deterministic, no network.
func abs(v float64) float64 {
	if v < 0 {
		return -v
	}
	return v
}

func mkBars(n int, jumpPct float64) []binance.Kline {
	bars := make([]binance.Kline, n)
	base := 100.0
	for i := range bars {
		p := base
		if i >= n-10 {
			p = base * (1 + jumpPct)
		}
		bars[i] = binance.Kline{
			OpenTime:     int64(i) * 1000,
			Open:         p,
			High:         p * 1.0001,
			Low:          p * 0.9999,
			Close:        p,
			Volume:       10,
			QuoteVolume:  10 * p,
			Trades:       5,
			TakerBuyBase: 7,
		}
	}
	return bars
}

func TestFeatureBucketsComputesReturns(t *testing.T) {
	bars := mkBars(100, 0.01)
	feats := FeatureBuckets(bars, nil, 30)
	last := feats[len(feats)-1]
	if last["return_10s"] <= 0 {
		t.Fatalf("return_10s should be positive after a jump, got %v", last["return_10s"])
	}
	if _, ok := last["buy_sell_imbalance_5s"]; !ok {
		t.Fatal("missing buy_sell_imbalance_5s")
	}
	// TakerBuyBase=7 of Volume=10 => imbalance = 2*7/10 - 1 = 0.4
	if v := last["buy_sell_imbalance_5s"]; abs(v-0.4) > 1e-9 {
		t.Fatalf("buy_sell_imbalance_5s = %v, want 0.4", v)
	}
}

func TestOnsetLabelUpBurst(t *testing.T) {
	cfg := DefaultLabelConfig()
	cfg.MinBarrierPct = 0.05 // tiny floor so the jump clears it
	// Flat for 60s, then a +1% jump in the last 10s.
	bars := mkBars(80, 0.01)
	// Onset at bar 60: the next 60s contains the jump (bars 70-79), so the
	// upper barrier is hit -> UP_BURST.
	got := OnsetLabel(bars, 50, 60, cfg)
	if got != OnsetUp {
		t.Fatalf("onset label = %s, want %s", got, OnsetUp)
	}
}

func TestContinuationLabelReversal(t *testing.T) {
	cfg := DefaultLabelConfig()
	cfg.MinBarrierPct = 0.05
	// A downward jump: the downside barrier is hit first -> REVERSAL_FIRST.
	bars := mkBars(40, -0.02)
	got := ContinuationLabel(bars, 25, 10, cfg)
	if got != ContRevers {
		t.Fatalf("continuation label = %s, want %s", got, ContRevers)
	}
}

func TestMergeBurstsCollapsesConsecutive(t *testing.T) {
	labels := []string{OnsetNone, OnsetUp, OnsetUp, OnsetUp, OnsetNone, OnsetUp, OnsetUp}
	ids := MergeBursts(labels)
	// The 3 consecutive UP at 1-3 share one id; the 2 at 5-6 share the next.
	if ids[1] != ids[2] || ids[2] != ids[3] {
		t.Fatalf("consecutive burst not merged: %v", ids)
	}
	if ids[1] == ids[5] {
		t.Fatalf("separate bursts share an id: %v", ids)
	}
	if ids[0] != 0 || ids[4] != 0 {
		t.Fatalf("no-burst bars must be id 0: %v", ids)
	}
}

func TestMedianMAD(t *testing.T) {
	med, mad := medianMAD([]float64{1, 2, 3, 4, 100})
	if med != 3 {
		t.Fatalf("median = %v, want 3", med)
	}
	if mad != 1 {
		t.Fatalf("MAD = %v, want 1", mad)
	}
}
