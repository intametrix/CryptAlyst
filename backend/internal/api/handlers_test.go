package api

import (
	"encoding/json"
	"net/http/httptest"
	"path/filepath"
	"testing"
	"time"

	"pumpdump/backend/internal/mocks"
)

// newTestServer builds the smallest server that can answer a forecast request: a
// mock exchange, a private store, and nothing else. This is the point of the port —
// no network, no wall clock, no machine-specific path.
func newTestServer(t *testing.T, market *mocks.Market) *Server {
	t.Helper()
	dir := t.TempDir()
	prevRoot := storeRoot
	storeRoot = filepath.Join(dir, "history")
	t.Cleanup(func() { storeRoot = prevRoot })

	return New(Deps{
		Market:      market,
		KlinesCache: NewKlinesCache(),
	})
}

// get runs a handler and decodes its JSON answer.
func get(t *testing.T, s *Server, h func(w *httptest.ResponseRecorder), url string) map[string]any {
	t.Helper()
	rec := httptest.NewRecorder()
	s.Routes().ServeHTTP(rec, httptest.NewRequest("GET", url, nil))
	var body map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("answer is not JSON (%d): %s", rec.Code, rec.Body.String()[:min(200, rec.Body.Len())])
	}
	return body
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// The mock exchange feeds the backtest, and the request the handler made is visible.
func TestBacktestRunsOnTheMockExchange(t *testing.T) {
	market := &mocks.Market{Bars: mocks.Series("BTCUSDT", 400, 77_000, 3_600_000, time.Now().Add(-400*time.Hour).UnixMilli())}
	s := newTestServer(t, market)

	body := get(t, s, nil, "/api/forecast/backtest?symbol=BTCUSDT&interval=1h&horizon=6&bars=200&method=vwap")
	if body["ok"] != true {
		t.Fatalf("backtest failed: %v", body)
	}
	metrics, _ := body["metrics"].(map[string]any)
	if metrics == nil {
		t.Fatalf("no metrics in %v", body)
	}
	if market.CallCount() == 0 {
		t.Fatal("the handler never asked the market port for bars")
	}
	first := market.Calls[0]
	if first.Interval != "1h" {
		t.Fatalf("requested interval %q, want 1h", first.Interval)
	}
}

// A failure from the exchange must reach the caller, not be swallowed into a zero.
func TestBacktestReportsAMarketFailure(t *testing.T) {
	market := &mocks.Market{Err: errTestExchangeDown}
	s := newTestServer(t, market)

	body := get(t, s, nil, "/api/forecast/backtest?symbol=BTCUSDT&interval=1h&horizon=6&bars=200&method=vwap")
	if body["ok"] != false {
		t.Fatalf("expected a failure, got %v", body)
	}
	if msg, _ := body["error"].(string); msg == "" {
		t.Fatalf("failure carried no message: %v", body)
	}
}

// The chart publishes an envelope: the answer must carry a cid and the journal must
// hold the packet.
func TestLiveChartPublishesAnEnvelope(t *testing.T) {
	market := &mocks.Market{Bars: mocks.Series("BTCUSDT", 400, 77_000, 5_000, time.Now().Add(-400*5*time.Second).UnixMilli())}
	s := newTestServer(t, market)

	body := get(t, s, nil, "/api/forecast/live-chart?symbol=BTCUSDT&interval=5s&method=vwap&horizon=6")
	if body["ok"] != true {
		t.Fatalf("live chart failed: %v", body)
	}
	if cid, _ := body["cid"].(string); cid == "" {
		t.Fatalf("no cid on the answer: %v", body)
	}
	if gen, _ := body["generation"].(float64); gen == 0 {
		t.Fatalf("no generation on the answer: %v", body)
	}

	counts, err := envelopeCounts()
	if err != nil {
		t.Fatalf("journal unreadable: %v", err)
	}
	if counts["intel.forecast"] == 0 {
		t.Fatalf("no intel.forecast packet recorded: %v", counts)
	}
}

// A decision with nothing recorded must refuse and say why, not invent a verdict.
func TestDecisionWithoutEvidenceRefuses(t *testing.T) {
	market := &mocks.Market{Bars: mocks.Series("BTCUSDT", 400, 77_000, 5_000, time.Now().UnixMilli())}
	s := newTestServer(t, market)

	body := get(t, s, nil, "/api/decision?symbol=BTCUSDT")
	if body["ok"] != false {
		t.Fatalf("expected a refusal, got %v", body)
	}
	if msg, _ := body["error"].(string); msg == "" {
		t.Fatalf("refusal carried no reason: %v", body)
	}
}
