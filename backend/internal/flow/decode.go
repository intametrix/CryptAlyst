package flow

import (
	"encoding/json"
	"strconv"
	"strings"
)

// Exact-key JSON extraction.
//
// Binance payloads deliberately contain key pairs that differ only in case
// ("e"/"E", "q"/"Q", "b"/"B", "a"/"A", "c"/"C", "l"/"L", "m"/"M"), and Go's
// encoding/json resolves struct tags case-INSENSITIVELY. Decoding these
// payloads into a tagged struct therefore both fails outright (a string "e"
// colliding with an int64 `json:"E"`) and silently corrupts fields (24h quote
// volume "q" overwritten by best-bid quantity "Q").
//
// Going through a map with exact byte keys removes the ambiguity at the source.
// The map conversion is also cheap: Go compiles `m[string(key)]` lookups
// without allocating.

func decodeFields(data json.RawMessage) (map[string]json.RawMessage, error) {
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(data, &fields); err != nil {
		return nil, err
	}
	return fields, nil
}

// rawString returns the field as a string. Binance encodes prices and volumes
// as JSON strings but timestamps and counts as JSON numbers, so numeric tokens
// are returned verbatim and parsed by the typed accessors below.
func rawString(fields map[string]json.RawMessage, key []byte) string {
	raw := fields[string(key)]
	if len(raw) == 0 {
		return ""
	}
	if raw[0] == '"' {
		var s string
		if err := json.Unmarshal(raw, &s); err != nil {
			return ""
		}
		return s
	}
	return strings.TrimSpace(string(raw))
}

func rawFloat(fields map[string]json.RawMessage, key []byte) float64 {
	text := rawString(fields, key)
	if text == "" {
		return 0
	}
	f, err := strconv.ParseFloat(text, 64)
	if err != nil {
		return 0
	}
	return f
}

func rawInt(fields map[string]json.RawMessage, key []byte) int64 {
	text := rawString(fields, key)
	if text == "" {
		return 0
	}
	if n, err := strconv.ParseInt(text, 10, 64); err == nil {
		return n
	}
	// Tolerate a float-encoded integer rather than dropping the message.
	if f, err := strconv.ParseFloat(text, 64); err == nil {
		return int64(f)
	}
	return 0
}

func rawBool(fields map[string]json.RawMessage, key []byte) bool {
	return rawString(fields, key) == "true"
}
