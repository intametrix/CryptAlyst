package envelope

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
)

func raw(s string) json.RawMessage { return json.RawMessage(s) }

func TestChainStopsAtFirstRefusal(t *testing.T) {
	env := New(raw(`{"a":1}`), Meta{Topic: "md.depth", Producer: "rogue", Key: "BTCUSDT"})
	_, err := Chain(env, RequireKey(true), Authorised([]string{"market_data"}))
	if err == nil || !strings.Contains(err.Error(), "not allowed") {
		t.Fatalf("expected an authority refusal, got %v", err)
	}
}

func TestRequireKey(t *testing.T) {
	env := New(raw(`{}`), Meta{Topic: "md.depth", Key: ""})
	if _, err := Chain(env, RequireKey(true)); err == nil {
		t.Fatal("key should be required")
	}
	env.Meta.Key = "BTCUSDT"
	if _, err := Chain(env, RequireKey(true)); err != nil {
		t.Fatalf("keyed message refused: %v", err)
	}
}

func TestEnsureCIDFillsTheGap(t *testing.T) {
	env := New(raw(`{}`), Meta{Topic: "ticks"})
	out, err := Chain(env, EnsureCID(func() string { return "cid-test" }))
	if err != nil || out.Meta.CID != "cid-test" {
		t.Fatalf("cid not minted: %v %q", err, out.Meta.CID)
	}
}

func TestMaxAgeRefusesStale(t *testing.T) {
	old := time.Now().Add(-2 * time.Minute)
	env := New(raw(`{}`), Meta{ComputedAt: &old})
	if _, err := Chain(env, MaxAge(30*time.Second)); err == nil {
		t.Fatal("stale message should be refused")
	}
	fresh := time.Now()
	env.Meta.ComputedAt = &fresh
	if _, err := Chain(env, MaxAge(30*time.Second)); err != nil {
		t.Fatalf("fresh message refused: %v", err)
	}
}

func TestRedactRemovesAndRecords(t *testing.T) {
	env := New(raw(`{"symbol":"BTCUSDT","notional":400}`), Meta{})
	out := Pipe(env, Redact("bystander", "notional"))
	var obj map[string]any
	json.Unmarshal(out.Data, &obj)
	if _, ok := obj["notional"]; ok {
		t.Fatal("field was not redacted")
	}
	if obj["symbol"] != "BTCUSDT" {
		t.Fatal("redaction removed the wrong field")
	}
	if out.Meta.Extra["redacted_for"] != "bystander" {
		t.Fatalf("redaction not recorded: %v", out.Meta.Extra)
	}
}
