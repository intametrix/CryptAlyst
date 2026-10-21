//go:build rocksdb

package store

import (
	"path/filepath"
	"testing"
)

// Rocks is a Store like any other: it answers the same four questions.
func TestRocks(t *testing.T) {
	r, err := OpenRocks(filepath.Join(t.TempDir(), "rocks"))
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	drive(t, r)
}
