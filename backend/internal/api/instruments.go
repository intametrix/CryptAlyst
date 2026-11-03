package api

import (
	"context"
	"encoding/json"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"time"
)

// Security reference data.
//
// The registry of what an instrument *is* lives in the refdata service
// (scalp5/core/cmd/refdata, default 127.0.0.1:8790), not in the market feed.
// That separation matters: a symbol's identity, class, venue and tradability
// are reference data and change rarely; its price is market data and changes
// every tick. The picker needs the first, the chart needs the second.

// Instrument is one reference-data record, flattened for the picker.
type Instrument struct {
	Symbol string   `json:"symbol"`
	Name   string   `json:"name"`
	Class  string   `json:"class"`
	Status string   `json:"status"`
	Venues []string `json:"venues"`
}

// refdataInstrument mirrors the service's wire shape (only what we use).
type refdataInstrument struct {
	ID     string   `json:"id"`
	Symbol string   `json:"symbol"`
	Name   string   `json:"name"`
	Class  string   `json:"class"`
	Status string   `json:"status"`
	Venues []string `json:"venues"`
}

// handleInstruments proxies the reference-data service so the browser can list
// real instruments without a second origin.
func (s *Server) handleInstruments(w http.ResponseWriter, r *http.Request) {
	base := strings.TrimRight(s.deps.Refdata, "/")
	if base == "" {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{
			"instruments": []Instrument{},
			"error":       "reference data service not configured",
		})
		return
	}
	class := strings.TrimSpace(r.URL.Query().Get("class"))
	q := strings.ToUpper(strings.TrimSpace(r.URL.Query().Get("q")))
	// Only tradable spot/perp pairs belong in a price chart's picker.
	status := r.URL.Query().Get("status")
	if status == "" {
		status = "active"
	}
	limit := queryInt(r, "limit", 2000)
	if limit > 5000 {
		limit = 5000
	}

	ctx, cancel := context.WithTimeout(r.Context(), 8*time.Second)
	defer cancel()

	u := base + "/v1/instruments?status=" + url.QueryEscape(status)
	if class != "" {
		u += "&class=" + url.QueryEscape(class)
	}
	if q != "" {
		u += "&q=" + url.QueryEscape(q)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		http.Error(w, "bad request: "+err.Error(), http.StatusBadRequest)
		return
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]any{
			"instruments": []Instrument{},
			"error":       "reference data unavailable: " + err.Error(),
		})
		return
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		writeJSON(w, http.StatusBadGateway, map[string]any{
			"instruments": []Instrument{},
			"error":       "reference data status " + resp.Status,
		})
		return
	}

	var body struct {
		Data []refdataInstrument `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]any{
			"instruments": []Instrument{},
			"error":       "reference data decode: " + err.Error(),
		})
		return
	}

	out := make([]Instrument, 0, len(body.Data))
	seen := map[string]bool{}
	for _, in := range body.Data {
		sym := strings.ToUpper(in.Symbol)
		if sym == "" || seen[sym] {
			continue
		}
		// A picker for a price chart only makes sense for USDT-quoted pairs.
		if !strings.HasSuffix(sym, "USDT") {
			continue
		}
		seen[sym] = true
		out = append(out, Instrument{
			Symbol: sym, Name: in.Name, Class: in.Class, Status: in.Status, Venues: in.Venues,
		})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Symbol < out[j].Symbol })
	if len(out) > limit {
		out = out[:limit]
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"instruments": out,
		"count":       len(out),
		"source":      base,
	})
}
