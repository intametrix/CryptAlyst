package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// A job that reports an error must come back as failed, with the error in the
// reply — not as a "done" job with the failure buried in the result.
func TestFailedJobReportsError(t *testing.T) {
	a := &Agent{
		jobs:   map[string]*Job{},
		state:  t.TempDir(),
		device: "cpu",
		gitSHA: "test",
	}
	j := a.submit("m", map[string]any{}, "cpu", "", "nonsense-type")

	// The runner is asynchronous. Observe it through the agent's lock, because
	// the worker goroutine writes the job fields under that same lock.
	type snapshot struct{ status, err string }
	read := func() snapshot {
		a.mu.Lock()
		defer a.mu.Unlock()
		return snapshot{j.Status, j.Error}
	}
	var got snapshot
	for i := 0; i < 200; i++ {
		got = read()
		if got.status == "failed" {
			break
		}
		sleepMs(20)
	}
	if got.status != "failed" {
		t.Fatalf("a job with an error ended as %q, want failed", got.status)
	}
	if got.err == "" {
		t.Fatalf("failed job carries no error text")
	}
	a.mu.Lock()
	_, hasErr := j.Result["error"]
	a.mu.Unlock()
	if !hasErr {
		t.Fatalf("failed job result lost the error field")
	}
}

// history_ref must fall back to the platform when the agent has no local
// marketdata service. Regression for: forecast/backtest failing with
// "need >= 5 closes" on a box that only runs the agent.
func TestFetchHistoryFallsBackToPlatform(t *testing.T) {
	// A platform that answers in the {bars:[...]} shape.
	platform := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/klines" {
			http.NotFound(w, r)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"symbol": r.URL.Query().Get("symbol"),
			"bars": []map[string]any{
				{"t": int64(1), "c": 100.0},
				{"t": int64(2), "c": 101.5},
				{"t": int64(3), "c": 99.25},
			},
		})
	}))
	defer platform.Close()

	a := &Agent{}
	a.rt.platform = platform.URL
	a.rt.host = "http://agent"
	a.rt.marketdata = "" // no local service: the platform is the only source

	got := a.fetchHistory(map[string]any{"symbol": "BTCUSDT", "interval": "1h", "limit": 3})
	if len(got) != 3 {
		t.Fatalf("fetchHistory returned %d points, want 3 from the platform", len(got))
	}
	if v := toFloat(got[1]["v"]); v != 101.5 {
		t.Fatalf("second close = %v, want 101.5", v)
	}
	// The source records the exact endpoint that answered, path included.
	if a.historySource != platform.URL+"/klines" {
		t.Fatalf("historySource = %q, want %s/klines", a.historySource, platform.URL)
	}
}

// When nothing answers, fetchHistory returns nothing rather than hanging, and
// the job then reports the real reason instead of a bare "need >= 3 points".
func TestFetchHistoryNoSource(t *testing.T) {
	a := &Agent{}
	a.rt.platform = "" // no platform and no local service: nothing can answer
	a.rt.marketdata = ""
	got := a.fetchHistory(map[string]any{"symbol": "NOPE", "interval": "1h", "limit": 5})
	if len(got) != 0 {
		t.Fatalf("fetched %d points with no source available", len(got))
	}
	if a.historySource != "" {
		t.Fatalf("historySource = %q, want empty", a.historySource)
	}
}

// The marketdata {klines:[...]} shape must still be understood.
func TestFetchSeriesAcceptsKlinesShape(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"klines": []map[string]any{{"t": int64(10), "c": 42.5}},
		})
	}))
	defer srv.Close()

	got := fetchSeries(srv.URL, "BTCUSDT", "1h", 1)
	if len(got) != 1 || toFloat(got[0]["v"]) != 42.5 {
		t.Fatalf("klines shape not decoded: %#v", got)
	}
}

// sleepMs keeps the polling helper short.
func sleepMs(ms int) { time.Sleep(time.Duration(ms) * time.Millisecond) }
