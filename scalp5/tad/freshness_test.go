package main

import "testing"

// TestFreshnessAgeStale: ages are recorded from note ts, and stale flips when
// age exceeds the input's max age (test c: ages recorded, not guessed).
func TestFreshnessAgeStale(t *testing.T) {
	f := newFreshness()
	f.note("oi", "BTCUSDT", 100_000) // 100s ago at the OI 30s max -> stale
	got := f.snapshot("BTCUSDT", 130_000)["oi"]
	if got.AgeS != 30 {
		t.Fatalf("age_s = %v, want 30", got.AgeS)
	}
	if !got.Stale {
		t.Fatalf("oi age 30s should be stale at the 30s max, got %+v", got)
	}
	// a fresh note is not stale
	f.note("oi", "BTCUSDT", 129_000)
	if st := f.snapshot("BTCUSDT", 130_000)["oi"]; st.Stale {
		t.Fatalf("fresh oi should not be stale, got %+v", st)
	}
}

// TestFreshnessQuietVsBroken (test d): a symbol with no flow events while the
// flow source is flowing reads quiet (stale + src_ok), not broken. When the
// source is silent, it reads broken (stale + !src_ok).
func TestFreshnessQuietVsBroken(t *testing.T) {
	f := newFreshness()
	now := int64(200_000)

	// flow source advanced globally (another symbol saw flow), this symbol didn't
	f.note("flow", "ETHUSDT", 199_000)
	q := f.snapshot("BTCUSDT", now)["flow"]
	if !q.Stale || !q.SrcOK {
		t.Fatalf("quiet flow should be stale+src_ok, got %+v", q)
	}

	// flow source silent for longer than srcWin
	f2 := newFreshness()
	b := f2.snapshot("BTCUSDT", now)["flow"]
	if !b.Stale || b.SrcOK {
		t.Fatalf("silent flow should be stale+!src_ok, got %+v", b)
	}
}

// TestFreshnessNeverSeen: an input never observed has age -1 and is stale, with
// src_ok reflecting the global source only.
func TestFreshnessNeverSeen(t *testing.T) {
	f := newFreshness()
	st := f.snapshot("BTCUSDT", 1000)["forecast"]
	if st.AgeS != -1 || !st.Stale {
		t.Fatalf("never-seen input should be age -1 stale, got %+v", st)
	}
}
