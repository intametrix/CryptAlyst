package api

import (
	"encoding/json"
	"net/http"
	"strconv"
	"strings"
)

// One request layer for every handler.
//
// Seventy-nine handlers each rebuilt the same preamble — parse symbol, validate,
// default the interval, clamp the integers, check a dependency, write
// {"ok":false,"error":...}. Thirty-five of them wrote that envelope by hand, so a
// typo in one key produced a reply the client could not parse and nothing caught
// it. These helpers are the single definition.

// ok writes a successful envelope.
func ok(w http.ResponseWriter, payload map[string]any) {
	if payload == nil {
		payload = map[string]any{}
	}
	payload["ok"] = true
	writeJSON(w, http.StatusOK, payload)
}

// fail writes an error envelope. The status stays 200 for query-level failures
// (the client renders the message); use failStatus when the HTTP code matters.
func fail(w http.ResponseWriter, msg string) {
	writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": msg})
}

// failStatus writes an error envelope with an explicit HTTP status.
func failStatus(w http.ResponseWriter, code int, msg string) {
	writeJSON(w, code, map[string]any{"ok": false, "error": msg})
}

// requireMethod rejects anything but the listed verbs with 405.
func requireMethod(w http.ResponseWriter, r *http.Request, verbs ...string) bool {
	for _, v := range verbs {
		if r.Method == v {
			return true
		}
	}
	w.Header().Set("Allow", strings.Join(verbs, ", "))
	failStatus(w, http.StatusMethodNotAllowed, "method not allowed")
	return false
}

// paramSymbol reads and normalises the symbol parameter.
func paramSymbol(r *http.Request) string {
	return strings.ToUpper(strings.TrimSpace(r.URL.Query().Get("symbol")))
}

// paramInterval reads the interval, defaulting when absent.
func paramInterval(r *http.Request, def string) string {
	iv := strings.TrimSpace(r.URL.Query().Get("interval"))
	if iv == "" {
		return def
	}
	return iv
}

// paramInt reads an integer inside [lo, hi], defaulting when absent or invalid.
func paramInt(r *http.Request, key string, def, lo, hi int) int {
	raw := strings.TrimSpace(r.URL.Query().Get(key))
	if raw == "" {
		return def
	}
	v, err := strconv.Atoi(raw)
	if err != nil {
		return def
	}
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

// paramInt64 reads a millisecond timestamp, defaulting when absent.
func paramInt64(r *http.Request, key string, def int64) int64 {
	raw := strings.TrimSpace(r.URL.Query().Get(key))
	if raw == "" {
		return def
	}
	v, err := strconv.ParseInt(raw, 10, 64)
	if err != nil {
		return def
	}
	return v
}

// paramFloat reads a float, defaulting when absent or invalid.
func paramFloat(r *http.Request, key string, def float64) float64 {
	raw := strings.TrimSpace(r.URL.Query().Get(key))
	if raw == "" {
		return def
	}
	v, err := strconv.ParseFloat(raw, 64)
	if err != nil {
		return def
	}
	return v
}

// paramBool reads 1/0/true/false.
func paramBool(r *http.Request, key string, def bool) bool {
	switch strings.ToLower(strings.TrimSpace(r.URL.Query().Get(key))) {
	case "1", "true", "yes", "on":
		return true
	case "0", "false", "no", "off":
		return false
	}
	return def
}

// decodeJSON reads a JSON body into v, answering the client on failure.
func decodeJSON(w http.ResponseWriter, r *http.Request, v any) bool {
	if err := json.NewDecoder(r.Body).Decode(v); err != nil {
		failStatus(w, http.StatusBadRequest, "invalid body")
		return false
	}
	return true
}

// failAgent reports a failure that also names the agent it came from, so the UI
// can say which machine was at fault rather than blaming the platform.
func failAgent(w http.ResponseWriter, msg, agent string) {
	writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": msg, "agent": agent})
}

// failAgentStatus is failAgent with an explicit HTTP status.
func failAgentStatus(w http.ResponseWriter, code int, msg, agent string) {
	writeJSON(w, code, map[string]any{"ok": false, "error": msg, "agent": agent})
}
