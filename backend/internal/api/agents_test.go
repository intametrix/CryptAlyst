package api

import (
	"testing"
	"time"
)

func TestAgentRegistryUpsertAndTouch(t *testing.T) {
	r := NewAgentRegistry()

	// First enroll: id defaults to host, timestamps are set, fields carried.
	info := r.Upsert(AgentInfo{Host: "http://127.0.0.1:8810", Device: "cuda", Arch: "amd64", NumCPU: 8, GPUPresent: true})
	if info.ID != "http://127.0.0.1:8810" {
		t.Fatalf("id = %q, want host", info.ID)
	}
	if info.FirstSeen == 0 || info.LastSeen == 0 {
		t.Fatalf("timestamps not set: %+v", info)
	}
	if !info.GPUPresent {
		t.Fatalf("gpu_present not carried through")
	}

	// Re-enroll refreshes the fields but preserves first_seen.
	first := info.FirstSeen
	time.Sleep(2 * time.Millisecond)
	info2 := r.Upsert(AgentInfo{Host: "http://127.0.0.1:8810", Device: "cpu", Arch: "amd64"})
	if info2.FirstSeen != first {
		t.Fatalf("first_seen changed on re-enroll: %d -> %d", first, info2.FirstSeen)
	}
	if info2.LastSeen <= first {
		t.Fatalf("last_seen not refreshed")
	}
	if info2.Device != "cpu" {
		t.Fatalf("device not updated: %q", info2.Device)
	}

	// Touch updates last_seen; an unknown id reports false.
	if !r.Touch("http://127.0.0.1:8810") {
		t.Fatalf("touch on known id returned false")
	}
	if r.Touch("http://unknown") {
		t.Fatalf("touch on unknown id returned true")
	}
}

func TestAgentRegistryListSortsByLastSeen(t *testing.T) {
	r := NewAgentRegistry()
	r.Upsert(AgentInfo{Host: "http://a:8810"})
	time.Sleep(2 * time.Millisecond)
	r.Upsert(AgentInfo{Host: "http://b:8810"}) // newest
	list := r.List()
	if len(list) != 2 {
		t.Fatalf("len = %d, want 2", len(list))
	}
	if list[0].Host != "http://b:8810" {
		t.Fatalf("list[0] = %q, want b (newest first)", list[0].Host)
	}
}
