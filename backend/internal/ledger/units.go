// Explicit-units contract. Every record the ledger WRITES carries a "units"
// block naming the convention for every measured field:
//
//	{"units":{"price":"quote_usd","pct_fields":"percent_of_entry",
//	          "mfe_pct":"percent_of_entry","mae_pct":"percent_of_entry"}}
//
// Reader rule: an ABSENT units block means the current convention
// (percent_of_entry) — every historical record parses exactly as before. A
// units block naming a convention the reader does not implement is a LOUD
// error naming the record type and the offending value, never a silent
// assumption. Unknown keys inside a known block are ignored, so writers can
// add fields. fastsample's records (label_10s, path_1s) belong to DeepSeek
// and may or may not carry units; readers tolerate either.

package ledger

import (
	"bytes"
	"encoding/json"
	"fmt"
)

// unitsBlock is the convention the current code implements. Anything else
// in any of these slots fails loudly.
var unitsBlock = map[string]string{
	"price":      "quote_usd",
	"pct_fields": "percent_of_entry",
	"mfe_pct":    "percent_of_entry",
	"mae_pct":    "percent_of_entry",
}

// stampUnits injects the units block into an already-marshalled record.
// write() routes every ledger record through this, so future record types
// get the block automatically.
func stampUnits(raw []byte) ([]byte, error) {
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		return nil, err
	}
	m["units"] = unitsBlock
	return json.Marshal(m)
}

// StampUnits injects the ledger's units block into an already-marshalled
// record; exposed so non-ledger writers (e.g. the audit retraction copy)
// stamp new records with the same convention.
func StampUnits(raw []byte) ([]byte, error) {
	return stampUnits(raw)
}

// CheckUnits validates the units block on one raw ledger record. typeName
// is the record type (e.g. "resolution") for the error message. Absent
// block: nil (historical convention). Known values: nil. Anything else: an
// error naming the record type, the field, and the unsupported value.
func CheckUnits(typeName string, raw []byte) error {
	var m map[string]json.RawMessage
	if err := json.Unmarshal(raw, &m); err != nil {
		return err
	}
	u, ok := m["units"]
	if !ok {
		return nil
	}
	var block map[string]string
	if err := json.Unmarshal(u, &block); err != nil {
		return fmt.Errorf("record %q: unparseable units block: %w", typeName, err)
	}
	for field, want := range unitsBlock {
		got, present := block[field]
		if !present {
			continue // absent key: assumed at the historical convention
		}
		if got != want {
			return fmt.Errorf("record %q: unsupported units.%s %q (this reader implements %q only)",
				typeName, field, got, want)
		}
	}
	return nil
}

// hasUnitsBlock is a test/debug helper: reports whether raw carries a units
// block (any shape).
func hasUnitsBlock(raw []byte) bool {
	return bytes.Contains(raw, []byte(`"units"`))
}
