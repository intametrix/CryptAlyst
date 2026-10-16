package api

import (
	"net/http"
	"sort"
	"strings"
	"time"
)

// handlePerf serves what each symbol's predictions have actually done, so
// "was I correct" is answerable per instrument rather than only in aggregate.
func (s *Server) handlePerf(w http.ResponseWriter, r *http.Request) {
	if s.deps.Perf == nil {
		writeJSON(w, http.StatusOK, map[string]any{
			"type": "perf", "symbols": []any{}, "global": nil,
			"note": "per-symbol performance is unavailable (no ledger configured)",
		})
		return
	}
	s.deps.Perf.Refresh(time.Now().UnixMilli())

	if symbol := strings.ToUpper(strings.TrimSpace(r.URL.Query().Get("symbol"))); symbol != "" {
		stats, ok := s.deps.Perf.Symbol(symbol)
		adjustment := s.deps.Perf.Adjust(symbol)
		writeJSON(w, http.StatusOK, map[string]any{
			"type":       "perf",
			"symbol":     symbol,
			"stats":      stats,
			"adjustment": adjustment,
			"recorded":   ok,
		})
		return
	}

	rows := s.deps.Perf.All()
	// Most-predicted first: a symbol with two predictions tells you little, so
	// it should not lead a table about reliability.
	sort.SliceStable(rows, func(i, j int) bool { return rows[i].Predictions > rows[j].Predictions })
	if len(rows) > 200 {
		rows = rows[:200]
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"type":            "perf",
		"symbols":         rows,
		"global":          s.deps.Perf.Global(),
		"malformed":       s.deps.Perf.Malformed(),
		"skipped_horizon": s.deps.Perf.SkippedHorizon(),
		"note": "shrunk_hit_rate pulls each symbol toward the market-wide rate by a " +
			"Beta prior, so a thin record cannot masquerade as a strong one; " +
			"hit_rate is the raw figure for comparison",
	})
}
