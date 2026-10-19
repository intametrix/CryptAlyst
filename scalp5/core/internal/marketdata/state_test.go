package marketdata

import (
	"encoding/json"
	"testing"
)

func TestTickPayloadOnlyCarriesStreamedSymbols(t *testing.T) {
	st := New()
	// two symbols we stream, twenty we do not
	st.OnBook("BTCUSDT", Book{Bid: 100, Ask: 101})
	st.OnBook("ETHUSDT", Book{Bid: 10, Ask: 11})
	for i := 0; i < 20; i++ {
		st.OnMini("DUST"+string(rune('A'+i))+"USDT", Mini{Pct: 1, Q: 5, P: 1})
	}
	st.OnMini("BTCUSDT", Mini{Pct: 2, Q: 9, P: 100})
	st.OnMini("ETHUSDT", Mini{Pct: -1, Q: 3, P: 10})

	ticks, _ := st.TickPayload()
	var payload struct {
		P map[string]float64 `json:"p"`
		M map[string]Mini    `json:"m"`
	}
	if err := json.Unmarshal(ticks, &payload); err != nil {
		t.Fatal(err)
	}
	if len(payload.P) != 2 {
		t.Fatalf("prices = %d, want 2", len(payload.P))
	}
	if len(payload.M) != 2 {
		t.Fatalf("mini = %d, want 2 (the universe must not be published)", len(payload.M))
	}
	if len(ticks) > 1024 {
		t.Fatalf("payload is %d bytes; a two-symbol snapshot must stay small", len(ticks))
	}
}

func TestDepthChanged(t *testing.T) {
	st := New()
	st.OnDepth("BTCUSDT", []Level{{Price: 100, Qty: 1}}, []Level{{Price: 101, Qty: 1}})
	if !st.DepthChanged("BTCUSDT", 0, 0) {
		t.Fatal("first publish must happen")
	}
	if st.DepthChanged("BTCUSDT", 100, 1) {
		t.Fatal("unchanged book must not publish")
	}
	if !st.DepthChanged("BTCUSDT", 100.5, 1) {
		t.Fatal("price change must publish")
	}
	if !st.DepthChanged("BTCUSDT", 100, 2) {
		t.Fatal("size change beyond the threshold must publish")
	}
}

func TestKLinesAreBounded(t *testing.T) {
	st := New()
	for i := 0; i < 500; i++ {
		st.OnKline("BTCUSDT", "1m", Kline{T: int64(i), C: float64(i)}, true)
	}
	if got := len(st.KLines("BTCUSDT", "1m", 1000)); got != 200 {
		t.Fatalf("series kept %d, want 200", got)
	}
	if got := len(st.KLines("BTCUSDT", "1m", 10)); got != 10 {
		t.Fatalf("limit ignored: %d", got)
	}
}
