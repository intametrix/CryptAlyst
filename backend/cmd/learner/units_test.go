package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The learner's loader must fail loudly on an unimplemented units
// convention, naming the field and value; historical records without a
// units block keep parsing.
func TestLoadRejectsUnknownUnits(t *testing.T) {
	dir := t.TempDir()
	bad := filepath.Join(dir, "bad.jsonl")
	content := `{"type":"resolution","id":"x","label":"EXPIRED","mfe_pct":0.5,"units":{"mfe_pct":"fraction_of_entry"}}` + "\n"
	if err := os.WriteFile(bad, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
	_, err := load(bad, 1.5, 1.0)
	if err == nil {
		t.Fatal("expected loud failure on unknown units")
	}
	for _, part := range []string{"units.mfe_pct", "fraction_of_entry"} {
		if !strings.Contains(err.Error(), part) {
			t.Fatalf("error %q missing %q", err.Error(), part)
		}
	}

	good := filepath.Join(dir, "good.jsonl")
	content = `{"type":"prediction","prediction":{"id":"a","detector":"d","kind":"PUMP","symbol":"S","direction":1,"severity":"ALERT","score":1,"price":1,"ts_ms":1000}}` + "\n"
	if err := os.WriteFile(good, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := load(good, 1.5, 1.0); err != nil {
		t.Fatalf("historical record without units must parse: %v", err)
	}
}
