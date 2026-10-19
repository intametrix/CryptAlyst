package main

import (
	"encoding/json"
	"testing"
	"time"

	"cryptalyst/core/internal/envelope"
)

func TestDerivedRefsAreAttachedWithAgeAndMarked(t *testing.T) {
	refMu.Lock()
	refIndex = map[string]map[string]latestRef{}
	refMu.Unlock()

	now := time.Now().UnixMilli()
	rememberRef(envelope.Envelope[json.RawMessage]{
		Data: json.RawMessage(`{}`),
		Meta: envelope.Meta{Topic: "intel.signal", Key: "BTCUSDT", Generation: 7, CID: "cid-a"},
	})
	// a different symbol must not leak in
	rememberRef(envelope.Envelope[json.RawMessage]{
		Data: json.RawMessage(`{}`),
		Meta: envelope.Meta{Topic: "intel.flow", Key: "ETHUSDT", Generation: 9, CID: "cid-b"},
	})

	refs := derivedRefs("BTCUSDT", now+1500)
	if len(refs) != 1 {
		t.Fatalf("refs = %d, want 1", len(refs))
	}
	if !refs[0].Derived {
		t.Fatal("a recorder-derived reference must be marked derived")
	}
	if refs[0].Topic != "intel.signal" || refs[0].Generation != 7 || refs[0].CID != "cid-a" {
		t.Fatalf("wrong reference: %+v", refs[0])
	}
	if refs[0].AgeMS < 1000 {
		t.Fatalf("age not recorded: %d", refs[0].AgeMS)
	}
}

func TestDerivedRefsIgnoreStaleInputs(t *testing.T) {
	refMu.Lock()
	refIndex = map[string]map[string]latestRef{}
	refMu.Unlock()
	rememberRef(envelope.Envelope[json.RawMessage]{Data: json.RawMessage(`{}`),
		Meta: envelope.Meta{Topic: "intel.ta", Key: "SOLUSDT", Generation: 1}})
	// ten minutes later the input is too old to be a plausible source
	if refs := derivedRefs("SOLUSDT", time.Now().UnixMilli()+600000); len(refs) != 0 {
		t.Fatalf("stale reference survived: %+v", refs)
	}
}


