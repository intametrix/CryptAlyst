// HTTP tests for the refdata command. All data is generated in-test (Rule A3).
package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"cryptalyst/core/internal/envelope"
	"cryptalyst/core/internal/refdata"
)

// newTestServer mounts the mux over an in-memory registry with one generated
// instrument.
func newTestServer(t *testing.T) *httptest.Server {
	t.Helper()
	svc, err := refdata.NewService(refdata.NewMemory())
	if err != nil {
		t.Fatalf("NewService: %v", err)
	}
	t.Cleanup(func() { svc.Close() })
	if _, err := svc.PutInstrument(refdata.Instrument{
		ID:          "crypto_perp:genvenue:GENUSDT",
		Class:       refdata.ClassCryptoPerp,
		Symbol:      "GENUSDT",
		Status:      refdata.StatusActive,
		Venues:      []string{"genvenue"},
		Identifiers: map[string]string{refdata.IDTicker: "GEN"},
	}); err != nil {
		t.Fatalf("seed instrument: %v", err)
	}
	s := &server{svc: svc}
	ts := httptest.NewServer(s.routes())
	t.Cleanup(ts.Close)
	return ts
}

// serveThrough runs one request against the real test server so path values
// behave identically to a live listener, and returns status plus the
// enveloped data payload.
func serveThrough(t *testing.T, ts *httptest.Server, method, path string, body any) (int, json.RawMessage) {
	t.Helper()
	var reader *bytes.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			t.Fatalf("marshal: %v", err)
		}
		reader = bytes.NewReader(raw)
	} else {
		reader = bytes.NewReader(nil)
	}
	req, err := http.NewRequest(method, ts.URL+path, reader)
	if err != nil {
		t.Fatalf("NewRequest: %v", err)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("do: %v", err)
	}
	defer resp.Body.Close()
	var env struct {
		Data json.RawMessage `json:"data"`
		Meta envelope.Meta   `json:"meta"`
	}
	if resp.StatusCode == http.StatusOK {
		if err := json.NewDecoder(resp.Body).Decode(&env); err != nil {
			t.Fatalf("decode envelope: %v", err)
		}
		// /healthz answers bare JSON by design; every other 200 travels in
		// the envelope shape with a minted correlation id.
		if env.Meta.Version != 0 && (env.Meta.Producer != "refdata" || env.Meta.CID == "") {
			t.Fatalf("envelope meta incomplete: %+v", env.Meta)
		}
	}
	return resp.StatusCode, env.Data
}

func TestHealthz(t *testing.T) {
	ts := newTestServer(t)
	code, _ := serveThrough(t, ts, "GET", "/healthz", nil)
	if code != http.StatusOK {
		t.Fatalf("healthz: %d", code)
	}
}

func TestInstrumentEndpoints(t *testing.T) {
	ts := newTestServer(t)

	code, data := serveThrough(t, ts, "GET", "/v1/instruments/crypto_perp:genvenue:GENUSDT", nil)
	if code != http.StatusOK {
		t.Fatalf("get instrument: %d", code)
	}
	var inst refdata.Instrument
	if err := json.Unmarshal(data, &inst); err != nil || inst.Symbol != "GENUSDT" {
		t.Fatalf("get instrument payload: %v %s", err, data)
	}

	code, data = serveThrough(t, ts, "GET", "/v1/instruments?class=crypto_perp&venue=genvenue", nil)
	if code != http.StatusOK {
		t.Fatalf("list: %d", code)
	}
	var list []refdata.Instrument
	if err := json.Unmarshal(data, &list); err != nil || len(list) != 1 {
		t.Fatalf("list payload: %v %s", err, data)
	}

	code, _ = serveThrough(t, ts, "GET", "/v1/instruments/crypto_perp:genvenue:NOPE", nil)
	if code != http.StatusNotFound {
		t.Fatalf("missing instrument should 404, got %d", code)
	}
}

func TestPutInstrumentRoundTrip(t *testing.T) {
	ts := newTestServer(t)
	code, data := serveThrough(t, ts, "PUT", "/v1/instruments", refdata.Instrument{
		ID:     "equity:genexchange:GENG",
		Class:  refdata.ClassEquity,
		Symbol: "GENG",
		Status: refdata.StatusActive,
	})
	if code != http.StatusOK {
		t.Fatalf("put instrument: %d", code)
	}
	var inst refdata.Instrument
	if err := json.Unmarshal(data, &inst); err != nil || inst.ID != "equity:genexchange:geng" {
		t.Fatalf("put payload: %v %s", err, data)
	}

	code, _ = serveThrough(t, ts, "PUT", "/v1/instruments", refdata.Instrument{ID: "bad"})
	if code != http.StatusBadRequest {
		t.Fatalf("invalid instrument should 400, got %d", code)
	}
}

func TestEnvelopeWrappedRequestAccepted(t *testing.T) {
	ts := newTestServer(t)
	// A bus-side client sends the envelope shape; the service unwraps it.
	wrapped := envelope.New(refdata.Instrument{
		ID:     "fx:genvenue:GENUSD",
		Class:  refdata.ClassFX,
		Symbol: "GENUSD",
		Status: refdata.StatusActive,
	}, envelope.Meta{Plane: envelope.PlaneIntel, Topic: "refdata.write", Producer: "test"})
	raw, err := json.Marshal(wrapped)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	req, err := http.NewRequest("PUT", ts.URL+"/v1/instruments", bytes.NewReader(raw))
	if err != nil {
		t.Fatalf("NewRequest: %v", err)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("do: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("enveloped write rejected: %d", resp.StatusCode)
	}
}

func TestResolveAndIdentifiersEndpoint(t *testing.T) {
	ts := newTestServer(t)
	code, _ := serveThrough(t, ts, "POST", "/v1/instruments/crypto_perp:genvenue:GENUSDT/identifiers",
		map[string]string{"type": refdata.IDISIN, "value": "GEN000000001"})
	if code != http.StatusOK {
		t.Fatalf("add identifier: %d", code)
	}

	code, data := serveThrough(t, ts, "GET", "/v1/resolve?type=isin&value=GEN000000001", nil)
	if code != http.StatusOK {
		t.Fatalf("resolve: %d", code)
	}
	var hits []refdata.Instrument
	if err := json.Unmarshal(data, &hits); err != nil || len(hits) != 1 {
		t.Fatalf("resolve payload: %v %s", err, data)
	}

	code, _ = serveThrough(t, ts, "GET", "/v1/resolve", nil)
	if code != http.StatusBadRequest {
		t.Fatalf("resolve without value should 400, got %d", code)
	}
}

func TestDocumentsAndCorrelationsEndpoints(t *testing.T) {
	ts := newTestServer(t)
	code, data := serveThrough(t, ts, "PUT", "/v1/documents", refdata.Document{
		InstrumentID: "crypto_perp:genvenue:GENUSDT",
		Title:        "GEN limits",
		Kind:         "limits",
		Body:         "# Limits\n\ngenerated\n",
	})
	if code != http.StatusOK {
		t.Fatalf("put document: %d", code)
	}
	var doc refdata.Document
	if err := json.Unmarshal(data, &doc); err != nil || doc.SHA256 == "" {
		t.Fatalf("document payload: %v %s", err, data)
	}

	code, data = serveThrough(t, ts, "GET", "/v1/instruments/crypto_perp:genvenue:GENUSDT/documents", nil)
	if code != http.StatusOK {
		t.Fatalf("instrument documents: %d", code)
	}
	var docs []refdata.Document
	if err := json.Unmarshal(data, &docs); err != nil || len(docs) != 1 {
		t.Fatalf("documents payload: %v %s", err, data)
	}

	code, data = serveThrough(t, ts, "GET", "/v1/instruments/crypto_perp:genvenue:GENUSDT/correlations", nil)
	if code != http.StatusOK {
		t.Fatalf("correlations: %d", code)
	}
	var edges []refdata.Edge
	if err := json.Unmarshal(data, &edges); err != nil || len(edges) == 0 {
		t.Fatalf("correlations payload: %v %s", err, data)
	}
}

func TestEdgesAndSearchEndpoints(t *testing.T) {
	ts := newTestServer(t)
	code, _ := serveThrough(t, ts, "PUT", "/v1/edges", refdata.Edge{
		Subject:   "crypto_perp:genvenue:GENUSDT",
		Predicate: refdata.EdgeListedOn,
		Object:    "venue:genvenue",
	})
	if code != http.StatusOK {
		t.Fatalf("put edge: %d", code)
	}

	code, data := serveThrough(t, ts, "GET", "/v1/edges?predicate=listed_on", nil)
	if code != http.StatusOK {
		t.Fatalf("list edges: %d", code)
	}
	var edges []refdata.Edge
	if err := json.Unmarshal(data, &edges); err != nil || len(edges) != 1 {
		t.Fatalf("edges payload: %v %s", err, data)
	}

	code, _ = serveThrough(t, ts, "PUT", "/v1/edges", refdata.Edge{
		Subject: "a", Predicate: "bogus", Object: "b"})
	if code != http.StatusBadRequest {
		t.Fatalf("bad predicate should 400, got %d", code)
	}

	code, data = serveThrough(t, ts, "GET", "/v1/search?q=genusdt", nil)
	if code != http.StatusOK {
		t.Fatalf("search: %d", code)
	}
	var res refdata.SearchResult
	if err := json.Unmarshal(data, &res); err != nil || len(res.Instruments) != 1 {
		t.Fatalf("search payload: %v %s", err, data)
	}

	code, _ = serveThrough(t, ts, "GET", "/v1/search", nil)
	if code != http.StatusBadRequest {
		t.Fatalf("search without q should 400, got %d", code)
	}
}

func TestStatsEndpoint(t *testing.T) {
	ts := newTestServer(t)
	code, data := serveThrough(t, ts, "GET", "/v1/stats", nil)
	if code != http.StatusOK {
		t.Fatalf("stats: %d", code)
	}
	var stats refdata.Stats
	if err := json.Unmarshal(data, &stats); err != nil || stats.Instruments != 1 {
		t.Fatalf("stats payload: %v %s", err, data)
	}
}

// TestSeedIsIdempotent applies a generated seed twice: the second pass must
// skip every record (no journal growth), and a changed record must reapply.
func TestSeedIsIdempotent(t *testing.T) {
	svc, err := refdata.NewService(refdata.NewMemory())
	if err != nil {
		t.Fatalf("NewService: %v", err)
	}
	defer svc.Close()
	path := filepath.Join(t.TempDir(), "seed.json")
	raw := []byte(`{
	  "instruments": [
	    {"id": "crypto_perp:genvenue:GENUSDT", "class": "crypto_perp", "symbol": "GENUSDT", "status": "active", "venues": ["genvenue"], "identifiers": {"ticker": "GEN"}},
	    {"id": "crypto_spot:genvenue:GENUSDT", "class": "crypto_spot", "symbol": "GENUSDT", "status": "active", "venues": ["genvenue"]}
	  ],
	  "documents": [
	    {"class": "crypto_perp", "title": "GEN class definition", "kind": "definition", "body": "# GEN\n\ngenerated\n"}
	  ],
	  "edges": [
	    {"subject": "crypto_perp:genvenue:genusdt", "predicate": "listed_on", "object": "venue:genvenue"}
	  ]
	}`)
	if err := os.WriteFile(path, raw, 0o644); err != nil {
		t.Fatalf("write seed: %v", err)
	}

	applied, skipped, err := applySeed(svc, path)
	if err != nil || applied != 4 || skipped != 0 {
		t.Fatalf("pass 1: applied=%d skipped=%d err=%v", applied, skipped, err)
	}
	statsAfterFirst := svc.Stats()

	applied, skipped, err = applySeed(svc, path)
	if err != nil || applied != 0 || skipped != 4 {
		t.Fatalf("pass 2 not a pure skip: applied=%d skipped=%d err=%v", applied, skipped, err)
	}
	if svc.Stats() != statsAfterFirst {
		t.Fatalf("reseed moved the stats: %+v -> %+v", statsAfterFirst, svc.Stats())
	}

	// A record with changed content must reapply and overwrite.
	changed := bytes.Replace(raw, []byte(`"status": "active", "venues": ["genvenue"], "identifiers": {"ticker": "GEN"}`),
		[]byte(`"status": "active", "venues": ["genvenue"], "limits": {"tick_size": 0.5}, "identifiers": {"ticker": "GEN"}`), 1)
	if err := os.WriteFile(path, changed, 0o644); err != nil {
		t.Fatalf("rewrite seed: %v", err)
	}
	applied, skipped, err = applySeed(svc, path)
	if err != nil || applied != 1 || skipped != 3 {
		t.Fatalf("pass 3: applied=%d skipped=%d err=%v", applied, skipped, err)
	}
	inst, err := svc.GetInstrument("crypto_perp:genvenue:GENUSDT")
	if err != nil || inst.Limits["tick_size"] != 0.5 {
		t.Fatalf("changed record not stored: %+v %v", inst, err)
	}

	// A seeded symbol must resolve through its ticker.
	if hits := svc.Resolve(refdata.IDTicker, "GEN"); len(hits) != 1 {
		t.Fatalf("seed resolve GEN: %+v", hits)
	}
}

func TestDecodeBodyRejectsGarbage(t *testing.T) {
	req := httptest.NewRequest("PUT", "/v1/instruments", strings.NewReader("not json"))
	if _, err := decodeBody[refdata.Instrument](req); err == nil {
		t.Fatal("garbage body accepted")
	}
}

func TestListDocumentsEndpoint(t *testing.T) {
	ts := newTestServer(t)
	code, _ := serveThrough(t, ts, "PUT", "/v1/documents", refdata.Document{
		InstrumentID: "crypto_perp:genvenue:GENUSDT",
		Title:        "GEN limits", Kind: "limits", Body: "generated limits body\n",
	})
	if code != http.StatusOK {
		t.Fatalf("put instrument doc: %d", code)
	}
	code, _ = serveThrough(t, ts, "PUT", "/v1/documents", refdata.Document{
		Class: refdata.ClassCryptoPerp, Title: "class def", Kind: "definition", Body: "generated class body\n",
	})
	if code != http.StatusOK {
		t.Fatalf("put class doc: %d", code)
	}
	code, data := serveThrough(t, ts, "GET", "/v1/documents", nil)
	if code != http.StatusOK {
		t.Fatalf("list documents: %d", code)
	}
	var docs []refdata.Document
	if err := json.Unmarshal(data, &docs); err != nil || len(docs) != 2 {
		t.Fatalf("documents payload: %v %s", err, data)
	}
	code, data = serveThrough(t, ts, "GET", "/v1/documents?kind=definition", nil)
	var filtered []refdata.Document
	if err := json.Unmarshal(data, &filtered); err != nil || len(filtered) != 1 || code != http.StatusOK {
		t.Fatalf("kind filter: %d %v %s", code, err, data)
	}
}

func TestUIServing(t *testing.T) {
	ts := newTestServer(t)
	for _, path := range []string{"/", "/ui", "/ui/", "/static/style.css", "/static/app.js", "/some/deep/route"} {
		resp, err := http.Get(ts.URL + path)
		if err != nil {
			t.Fatalf("GET %s: %v", path, err)
		}
		body := make([]byte, 512)
		n, _ := resp.Body.Read(body)
		resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("GET %s: status %d", path, resp.StatusCode)
		}
		if path == "/" || path == "/ui/" || path == "/ui" || path == "/some/deep/route" {
			if !strings.Contains(string(body[:n]), "<title>") {
				t.Fatalf("GET %s: expected app shell, got %.80q", path, body)
			}
		}
	}
	// API routes must not be shadowed by the UI catch-all.
	for _, path := range []string{"/healthz", "/v1/stats"} {
		resp, err := http.Get(ts.URL + path)
		if err != nil {
			t.Fatalf("GET %s: %v", path, err)
		}
		resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("GET %s: status %d (UI shadowed the API?)", path, resp.StatusCode)
		}
	}
}
