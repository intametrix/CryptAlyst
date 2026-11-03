package api

import (
	"encoding/json"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"
)

// Saved configurations.
//
// Optimisation is only useful if its result survives: the winning model and its
// parameters are written to disk, and forecasting can then ask for the symbol's
// saved configuration by name instead of re-deciding every time.

type savedConfig struct {
	Symbol    string  `json:"symbol"`
	Interval  string  `json:"interval"`
	Method    string  `json:"method"`
	Lookback  int     `json:"lookback"`
	Bars      int     `json:"bars"`
	Horizon   int     `json:"horizon"`
	R2        float64 `json:"r2"`
	MAPE      float64 `json:"mape_pct"`
	SavedAtMS int64   `json:"saved_at_ms"`
}

var (
	configMu   sync.Mutex
	configPath = "backend-web-forecast-configs.json"
)

func loadConfigs() map[string]savedConfig {
	out := map[string]savedConfig{}
	raw, err := os.ReadFile(configPath)
	if err != nil {
		return out
	}
	_ = json.Unmarshal(raw, &out)
	return out
}

func persistConfigs(all map[string]savedConfig) error {
	raw, err := json.MarshalIndent(all, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(configPath, raw, 0o644)
}

// handleConfigs saves, lists or deletes a symbol's configuration.
//
//	POST /api/forecast/configs?symbol=BTCUSDT&method=vwap&lookback=2&bars=600&horizon=6&r2=0.98&mape=0.83
//	GET  /api/forecast/configs
//	DEL  /api/forecast/configs?symbol=BTCUSDT
func (s *Server) handleConfigs(w http.ResponseWriter, r *http.Request) {
	configMu.Lock()
	defer configMu.Unlock()
	all := loadConfigs()

	switch r.Method {
	case http.MethodGet:
		list := make([]savedConfig, 0, len(all))
		for _, c := range all {
			list = append(list, c)
		}
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "configs": list})
		return
	case http.MethodDelete:
		sym := strings.ToUpper(strings.TrimSpace(r.URL.Query().Get("symbol")))
		delete(all, sym)
		_ = persistConfigs(all)
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "deleted": sym})
		return
	case http.MethodPost:
		sym := strings.ToUpper(strings.TrimSpace(r.URL.Query().Get("symbol")))
		if sym == "" {
			writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "symbol required"})
			return
		}
		cfg := savedConfig{
			Symbol: sym, Interval: r.URL.Query().Get("interval"),
			Method:   strings.TrimSpace(r.URL.Query().Get("method")),
			Lookback: queryInt(r, "lookback", 2), Bars: queryInt(r, "bars", 600),
			Horizon: queryInt(r, "horizon", 6),
			R2:      queryFloat(r, "r2", 0), MAPE: queryFloat(r, "mape", 0),
			SavedAtMS: time.Now().UnixMilli(),
		}
		if cfg.Interval == "" {
			cfg.Interval = "4h"
		}
		if cfg.Method == "" {
			writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "method required"})
			return
		}
		all[sym] = cfg
		if err := persistConfigs(all); err != nil {
			writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": err.Error()})
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "config": cfg})
		return
	}
	http.Error(w, "unsupported method", http.StatusMethodNotAllowed)
}

// savedFor returns the stored configuration for a symbol, if any.
func savedFor(symbol string) (savedConfig, bool) {
	configMu.Lock()
	defer configMu.Unlock()
	c, ok := loadConfigs()[strings.ToUpper(strings.TrimSpace(symbol))]
	return c, ok
}
