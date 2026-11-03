package main

import (
	"sync"
	"testing"
)

// A rename must be visible on the very next beacon: the advertised name is
// read live, never captured once at startup. Regression for the bug where a
// rename returned ok but the platform kept showing the old host name.
func TestDisplayNameTracksRename(t *testing.T) {
	a := &Agent{}

	// Unnamed agent falls back to the supplied host name.
	if got := a.displayName("host-fallback"); got != "host-fallback" {
		t.Fatalf("displayName on unnamed agent = %q, want the fallback", got)
	}

	// After a rename the live name wins immediately (no restart, no waiting).
	a.SetName("R1")
	if got := a.displayName("host-fallback"); got != "R1" {
		t.Fatalf("displayName after rename = %q, want R1", got)
	}

	// A second rename also takes effect on the next read.
	a.SetName("R2")
	if got := a.displayName("host-fallback"); got != "R2" {
		t.Fatalf("displayName after second rename = %q, want R2", got)
	}

	// An empty rename is ignored rather than clearing the name.
	a.SetName("   ")
	if got := a.Name(); got != "R2" {
		t.Fatalf("blank rename changed the name to %q, want R2 kept", got)
	}
}

// SetName is called from the HTTP handler while the beacon loop reads it, so
// the pair must be race-free. Run with -race to make this meaningful.
func TestSetNameConcurrentWithRead(t *testing.T) {
	a := &Agent{}
	var wg sync.WaitGroup

	wg.Add(1)
	go func() {
		defer wg.Done()
		for i := 0; i < 200; i++ {
			a.SetName("name-" + string(rune('a'+i%26)))
		}
	}()
	wg.Add(1)
	go func() {
		defer wg.Done()
		for i := 0; i < 200; i++ {
			if got := a.displayName("fallback"); got == "" {
				t.Errorf("displayName returned an empty name during a concurrent rename")
				return
			}
		}
	}()
	wg.Wait()
}
