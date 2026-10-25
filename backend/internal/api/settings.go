package api

import (
	"encoding/json"
	"net/http"
	"sync"

	"pumpdump/backend/internal/screen"
)

// Settings is the live-tunable set of detection/selection knobs, exposed at
// /api/settings. Every field applies immediately when POSTed; no restart.
type Settings struct {
	mu *sync.RWMutex

	ScreenMinMove        float64 `json:"screen_min_move"`
	ScreenMinVolRatio    float64 `json:"screen_min_vol_ratio"`
	ScreenMinVolZ        float64 `json:"screen_min_vol_z"`
	ScreenMinRecentUSD   float64 `json:"screen_min_recent_usd"`
	ScreenMinQuoteVolume float64 `json:"screen_min_quote_volume"`
	ScreenTopN           int     `json:"screen_top_n"`
	MinLiquidity         float64 `json:"min_liq"`
	MaxSymbols           int     `json:"max_symbols"`
}

// NewSettings snapshots the initial values from the built services.
func NewSettings(th screen.Thresholds, topN int, minLiq float64, maxSym int) *Settings {
	return &Settings{
		mu:                   &sync.RWMutex{},
		ScreenMinMove:        th.MinMove5m,
		ScreenMinVolRatio:    th.MinVolRatio,
		ScreenMinVolZ:        th.MinVolZ,
		ScreenMinRecentUSD:   th.MinRecentUSD,
		ScreenMinQuoteVolume: th.MinQuoteVolume24h,
		ScreenTopN:           topN,
		MinLiquidity:         minLiq,
		MaxSymbols:           maxSym,
	}
}

// Snapshot returns a copy of the current settings.
func (s *Settings) Snapshot() Settings {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return *s
}

// handleSettings returns the current settings.
func (s *Server) handleSettings(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, s.deps.Settings.Snapshot())
}

// handleSettingsApply applies a full settings object immediately.
func (s *Server) handleSettingsApply(w http.ResponseWriter, r *http.Request) {
	var next Settings
	if err := json.NewDecoder(r.Body).Decode(&next); err != nil {
		http.Error(w, "invalid body: "+err.Error(), http.StatusBadRequest)
		return
	}

	// pump/dump screen thresholds + scan width (apply instantly)
	th := s.deps.Screener.Thresholds()
	if next.ScreenMinMove != 0 {
		th.MinMove5m = next.ScreenMinMove
	}
	if next.ScreenMinVolRatio != 0 {
		th.MinVolRatio = next.ScreenMinVolRatio
	}
	if next.ScreenMinVolZ != 0 {
		th.MinVolZ = next.ScreenMinVolZ
	}
	if next.ScreenMinRecentUSD != 0 {
		th.MinRecentUSD = next.ScreenMinRecentUSD
	}
	if next.ScreenMinQuoteVolume != 0 {
		th.MinQuoteVolume24h = next.ScreenMinQuoteVolume
	}
	s.deps.Screener.SetThresholds(th)
	if next.ScreenTopN > 0 {
		s.deps.Screener.SetTopN(next.ScreenTopN)
	}

	// universe floor + cap (apply on the next 60s refresh)
	if next.MinLiquidity > 0 {
		s.deps.Universe.SetMinQuoteVolume(next.MinLiquidity)
	}
	if next.MaxSymbols > 0 {
		s.deps.Universe.SetMaxSymbols(next.MaxSymbols)
	}

	// Merge onto the current settings so a partial POST does not zero the
	// fields the caller did not mention.
	cur := s.deps.Settings.Snapshot()
	if next.ScreenMinMove != 0 {
		cur.ScreenMinMove = next.ScreenMinMove
	}
	if next.ScreenMinVolRatio != 0 {
		cur.ScreenMinVolRatio = next.ScreenMinVolRatio
	}
	if next.ScreenMinVolZ != 0 {
		cur.ScreenMinVolZ = next.ScreenMinVolZ
	}
	if next.ScreenMinRecentUSD != 0 {
		cur.ScreenMinRecentUSD = next.ScreenMinRecentUSD
	}
	if next.ScreenMinQuoteVolume != 0 {
		cur.ScreenMinQuoteVolume = next.ScreenMinQuoteVolume
	}
	if next.ScreenTopN > 0 {
		cur.ScreenTopN = next.ScreenTopN
	}
	if next.MinLiquidity > 0 {
		cur.MinLiquidity = next.MinLiquidity
	}
	if next.MaxSymbols > 0 {
		cur.MaxSymbols = next.MaxSymbols
	}
	s.deps.Settings.mu.Lock()
	*s.deps.Settings = cur
	s.deps.Settings.mu.Unlock()

	writeJSON(w, http.StatusOK, s.deps.Settings.Snapshot())
}
