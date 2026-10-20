package main

import (
	"os"
	"path/filepath"
	"testing"
)

// TestResolveStorePathsAnchorsDefaults is the regression test for the
// launch-directory wart: store flags defaulted to bare file names, so the
// data landed wherever the binary happened to be started from. Defaults now
// resolve under -data-dir; explicit flags always win, including an explicit
// empty value, which disables the store and must stay empty.
func TestResolveStorePathsAnchorsDefaults(t *testing.T) {
	specs := []storeSpec{
		{name: "alerts", flagName: "alert-history", defaultVal: "alerts.jsonl", current: "alerts.jsonl"},
		{name: "ledger", flagName: "ledger", defaultVal: "ledger.jsonl", current: "ledger.jsonl"},
		{name: "turtle", flagName: "turtle-log", defaultVal: "turtle_sessions.jsonl", current: "turtle_sessions.jsonl"},
	}
	explicit := map[string]bool{}

	got := resolveStorePaths("./data", specs, explicit)
	for _, s := range specs {
		want := filepath.Join("./data", s.defaultVal)
		if got[s.name] != want {
			t.Errorf("%s = %q, want %q (default must anchor under data dir)", s.name, got[s.name], want)
		}
	}

	// Explicit overrides win, and an explicit empty value disables the store.
	specs[1].current = "/custom/ledger.jsonl"
	explicit["ledger"] = true
	got = resolveStorePaths("./data", specs, explicit)
	if got["ledger"] != "/custom/ledger.jsonl" {
		t.Errorf("explicit ledger flag = %q, want /custom/ledger.jsonl", got["ledger"])
	}
	if got["alerts"] != filepath.Join("./data", "alerts.jsonl") {
		t.Errorf("alerts = %q, want anchored default", got["alerts"])
	}

	specs[2].current = "" // operator disabled the turtle log explicitly
	explicit["turtle-log"] = true
	got = resolveStorePaths("./data", specs, explicit)
	if got["turtle"] != "" {
		t.Errorf("explicitly disabled turtle = %q, want empty", got["turtle"])
	}

	// An empty data dir preserves the historical launch-directory behaviour.
	explicit = map[string]bool{}
	got = resolveStorePaths("", specs, explicit)
	if got["alerts"] != "alerts.jsonl" {
		t.Errorf("empty data dir: alerts = %q, want historical bare default", got["alerts"])
	}
}

// TestImportLegacyStore: pre-data-dir files are copied in once, the original
// stays, an existing resolved store is never overwritten, and a missing
// legacy file is a silent no-op (a fresh checkout has nothing to import).
func TestImportLegacyStore(t *testing.T) {
	dir := t.TempDir()
	legacy := filepath.Join(dir, "turtle_sessions.jsonl")
	resolved := filepath.Join(dir, "data", "turtle_sessions.jsonl")

	if ok, err := importLegacyStore(resolved, legacy); ok || err != nil {
		t.Fatalf("missing legacy file: got (%v, %v), want (false, nil)", ok, err)
	}

	if err := os.WriteFile(legacy, []byte(`{"kind":"session_start"}`+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	ok, err := importLegacyStore(resolved, legacy)
	if !ok || err != nil {
		t.Fatalf("first import: got (%v, %v), want (true, nil)", ok, err)
	}
	got, err := os.ReadFile(resolved)
	if err != nil || string(got) != `{"kind":"session_start"}`+"\n" {
		t.Fatalf("imported content = %q, %v", got, err)
	}
	if _, err := os.Stat(legacy); err != nil {
		t.Fatal("the legacy original must be left in place")
	}

	// A second run must NOT overwrite the resolved store.
	if err := os.WriteFile(legacy, []byte("newer data\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if ok, err := importLegacyStore(resolved, legacy); ok || err != nil {
		t.Fatalf("re-import over existing store: got (%v, %v), want (false, nil)", ok, err)
	}
	got, _ = os.ReadFile(resolved)
	if string(got) != `{"kind":"session_start"}`+"\n" {
		t.Fatalf("resolved store was overwritten: %q", got)
	}
}
