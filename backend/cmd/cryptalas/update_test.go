package main

import (
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestNeedsUpdate(t *testing.T) {
	cases := []struct {
		current, latest string
		want            bool
	}{
		{"abc", "abc", false}, // same
		{"abc", "def", true},  // newer
		{"abc", "", false},    // no version in manifest
		{"", "def", true},     // unknown current -> treat as update
	}
	for _, c := range cases {
		if got := needsUpdate(c.current, c.latest); got != c.want {
			t.Errorf("needsUpdate(%q, %q) = %v, want %v", c.current, c.latest, got, c.want)
		}
	}
}

func TestVerifySHA256(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "blob")
	content := []byte("self-update-test-payload")
	if err := os.WriteFile(path, content, 0o644); err != nil {
		t.Fatal(err)
	}

	// A wrong hash must be rejected.
	if err := verifySHA256(path, strings.Repeat("0", 64)); err == nil {
		t.Fatalf("verifySHA256 accepted a wrong hash")
	}

	// The real hash must be accepted.
	sum := sha256.Sum256(content)
	if err := verifySHA256(path, hex.EncodeToString(sum[:])); err != nil {
		t.Fatalf("verifySHA256 rejected the correct hash: %v", err)
	}
}
