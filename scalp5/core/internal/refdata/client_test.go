// Tests for the refdata HTTP client against a live test server. All data is
// generated in-test (Rule A3).
package refdata

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"cryptalyst/core/internal/correlation"
	"cryptalyst/core/internal/envelope"
)

// testHTTPServer serves the registry over HTTP with the same envelope
// wrapping as cmd/refdata, without importing the command.
func testHTTPServer(t *testing.T, svc *Service) *httptest.Server {
	t.Helper()
	mux := http.NewServeMux()
	wrap := func(topic string, payload any) envelope.Envelope[any] {
		return envelope.New[any](payload, envelope.Meta{
			Plane: envelope.PlaneIntel, Topic: topic, Producer: "refdata",
			Key: topic, CID: correlation.Mint(),
		})
	}
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]bool{"ok": true})
	})
	mux.HandleFunc("GET /v1/resolve", func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(wrap("refdata.resolve",
			svc.Resolve(q.Get("type"), q.Get("value"))))
	})
	mux.HandleFunc("GET /v1/instruments/{id}", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		inst, err := svc.GetInstrument(r.PathValue("id"))
		if err != nil {
			w.WriteHeader(http.StatusNotFound)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}
		_ = json.NewEncoder(w).Encode(wrap("refdata.instrument", inst))
	})
	mux.HandleFunc("GET /v1/instruments/{id}/correlations", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(wrap("refdata.edges",
			svc.Correlations(r.PathValue("id"))))
	})
	ts := httptest.NewServer(mux)
	t.Cleanup(ts.Close)
	return ts
}

func TestClientResolveAndInstrument(t *testing.T) {
	svc := newTestService(t, NewMemory())
	inst := putInstrument(t, svc, "crypto_perp:genvenue:GENUSDT", nil)
	if _, err := svc.AddIdentifier(inst.ID, IDISIN, "GEN000000001"); err != nil {
		t.Fatalf("AddIdentifier: %v", err)
	}
	ts := testHTTPServer(t, svc)

	c := NewClient(ts.URL)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	if err := c.Health(ctx); err != nil {
		t.Fatalf("Health: %v", err)
	}
	hits, err := c.Resolve(ctx, IDISIN, "GEN000000001")
	if err != nil || len(hits) != 1 || hits[0].ID != inst.ID {
		t.Fatalf("Resolve: %v %+v", err, hits)
	}
	got, err := c.Instrument(ctx, inst.ID)
	if err != nil || got.Symbol != "GENUSDT" {
		t.Fatalf("Instrument: %v %+v", err, got)
	}
	edges, err := c.Correlations(ctx, inst.ID)
	if err != nil || len(edges) != 1 {
		t.Fatalf("Correlations: %v %+v", err, edges)
	}
}

func TestClientErrors(t *testing.T) {
	svc := newTestService(t, NewMemory())
	putInstrument(t, svc, "crypto_perp:genvenue:GENUSDT", nil)
	ts := testHTTPServer(t, svc)
	c := NewClient(ts.URL)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	if _, err := c.Instrument(ctx, "crypto_perp:genvenue:NOPE"); err == nil {
		t.Fatal("missing instrument should error")
	}
	if _, err := c.Resolve(ctx, IDTicker, "MISSING"); err != nil {
		t.Fatalf("empty resolve should not error: %v", err)
	} else if hits, _ := c.Resolve(ctx, IDTicker, "MISSING"); len(hits) != 0 {
		t.Fatalf("missing resolve should be empty: %+v", hits)
	}

	dead := NewClient("http://127.0.0.1:1")
	if err := dead.Health(ctx); err == nil {
		t.Fatal("dead service health should error")
	}
}
