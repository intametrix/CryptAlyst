package api

import (
	"net/http"
	"sort"
	"strings"
	"time"
)

// symbolDetail is the consolidated answer to "tell me everything about this
// symbol, and take me to the trade".
//
// A drill-down that requires the client to make five separate calls and stitch
// the results will drift out of sync with what the detectors actually saw. This
// gathers every perspective the backend holds into one object, and it
// deliberately includes the NEGATIVE answers too -- why there is no trade, and
// which gate rejected it -- because "no trade" is the most common outcome and
// an unexplained blank is worse than no feature.
type symbolDetail struct {
	Type    string `json:"type"`
	Symbol  string `json:"symbol"`
	TSMS    int64  `json:"ts_ms"`
	Context any    `json:"context"`
	Flow    any    `json:"flow"`
	Heat    any    `json:"heat"`
	Screen  any    `json:"screen"`
	Setup   any    `json:"setup"`
	Trade   any    `json:"trade"`
	WhyNot  string `json:"why_no_trade"`
	Metrics any    `json:"metrics"`
	Alerts  any    `json:"alerts"`
	Links   any    `json:"links"`
}

func (s *Server) handleSymbol(w http.ResponseWriter, r *http.Request) {
	symbol := strings.ToUpper(strings.TrimSpace(r.URL.Query().Get("symbol")))
	if symbol == "" || len(symbol) > 24 {
		http.Error(w, "symbol required", http.StatusBadRequest)
		return
	}
	now := time.Now().UnixMilli()

	detail := symbolDetail{
		Type:   "symbol",
		Symbol: symbol,
		TSMS:   now,
		Links: map[string]any{
			"chart":  "/api/klines?symbol=" + symbol + "&interval=1m&limit=300",
			"live":   "/api/klines/latest?symbol=" + symbol + "&interval=1m",
			"alerts": "/api/alerts?limit=200",
		},
	}

	// --- 24h context -----------------------------------------------------
	if s.deps.Universe != nil {
		if ctx, ok := s.deps.Universe.Get(symbol); ok {
			detail.Context = ctx
		}
	}

	// --- live flow state -------------------------------------------------
	if s.deps.Engine != nil {
		for _, row := range s.deps.Engine.Snapshot(0, now) {
			if row.Symbol == symbol {
				detail.Flow = row
				detail.Metrics = map[string]any{
					"flow_quote":      row.FlowQuote,
					"baseline_quote":  row.BaselineQuote,
					"burst_multiple":  row.BurstMultiple,
					"burst_z":         row.BurstZ,
					"coverage":        row.Coverage,
					"change_10s":      row.Change10s,
					"change_60s":      row.Change60s,
					"taker_buy_ratio": row.TakerBuyRatio,
					"trades_10s":      row.Trades10s,
					"spread_pct":      row.SpreadPct,
				}
				break
			}
		}
	}

	// --- heat ------------------------------------------------------------
	if s.deps.Heat != nil {
		for _, row := range s.deps.Heat.Compute(now, 0) {
			if row.Symbol == symbol {
				detail.Heat = row
				break
			}
		}
	}

	// --- screen row ------------------------------------------------------
	if s.deps.Screener != nil {
		rows, _, _ := s.deps.Screener.Cached()
		for _, row := range rows {
			if row.Symbol == symbol {
				detail.Screen = row
				break
			}
		}
	}

	// --- Turtle setup and, if allocated, the trade itself ------------------
	detail.WhyNot = "no structural breakout on the surveyed timeframes"
	if s.deps.Survey != nil {
		if payload, ok := s.deps.Survey.Current().(map[string]any); ok {
			if setups, ok := payload["setups"].([]map[string]any); ok {
				for _, setup := range setups {
					if asString(setup["symbol"]) != symbol {
						continue
					}
					detail.Setup = setup
					if plan, ok := setup["plan"]; ok && plan != nil {
						detail.Trade = plan
						detail.WhyNot = ""
					} else if reason := asString(setup["reject_reason"]); reason != "" {
						detail.WhyNot = reason
					} else {
						detail.WhyNot = "setup found but no position allocated (risk budget)"
					}
					break
				}
			}
			// A rejected setup names the exact gate that refused it.
			if detail.Setup == nil {
				if rejected, ok := payload["rejected"].([]map[string]any); ok {
					for _, item := range rejected {
						if asString(item["symbol"]) == symbol {
							detail.WhyNot = asString(item["reason"])
							break
						}
					}
				}
				if regime, ok := payload["btc_regime"].(map[string]any); ok {
					if off, _ := regime["risk_off"].(bool); off {
						detail.WhyNot = "BTC regime gate is shut: " + detail.WhyNot
					}
				}
			}
		}
	}

	// --- this symbol's recent detections ---------------------------------
	if s.deps.Alerts != nil {
		alerts := make([]Alert, 0, 16)
		for _, alert := range s.deps.Alerts.List(500) {
			if alert.Symbol == symbol {
				alerts = append(alerts, alert)
			}
		}
		sort.Slice(alerts, func(i, j int) bool { return alerts[i].TSMS > alerts[j].TSMS })
		if len(alerts) > 50 {
			alerts = alerts[:50]
		}
		detail.Alerts = alerts
	}

	writeJSON(w, http.StatusOK, detail)
}

func asString(v any) string {
	s, _ := v.(string)
	return s
}
