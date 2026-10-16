package api

import (
	"net/http"
	"time"

	"pumpdump/backend/internal/flow"
	"pumpdump/backend/internal/turtle"
)

// handleTurtle is the whole turtle surface behind one route family, so the
// dashboard has a single thing to talk to:
//
//	GET  /api/turtle            -> session state (equity, PnL, positions, counters)
//	POST /api/turtle?action=start
//	POST /api/turtle?action=stop
//	GET  /api/turtle/trades     -> completed round trips, newest first
//	GET  /api/turtle/orders     -> every instruction, filled AND rejected
//
// Orders are exposed separately from trades because a list of fills alone would
// hide the entries the volatility filter vetoed, and that veto is the rule the
// human asked for -- it has to be visible or it cannot be judged.
func (s *Server) handleTurtle(w http.ResponseWriter, r *http.Request) {
	if s.deps.Turtle == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{
			"error": "turtle engine not configured",
		})
		return
	}
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusOK, s.deps.Turtle.Session())
		return
	}
	switch r.URL.Query().Get("action") {
	case "start":
		// SELECT THE UNIVERSE FIRST, then start. The human's framing: the
		// platform decides what to invest in from its prediction engine, and only
		// then follows the alerts. Choosing the instrument and choosing the moment
		// are separate problems, and a session that started watching all 400
		// symbols would take every alert in the market.
		writeJSON(w, http.StatusOK, s.startTurtleSession())
	case "stop":
		writeJSON(w, http.StatusOK, s.deps.Turtle.Stop())
	default:
		writeJSON(w, http.StatusBadRequest, map[string]string{
			"error": `unknown action; use action=start or action=stop`,
		})
	}
}

func (s *Server) handleTurtleTrades(w http.ResponseWriter, r *http.Request) {
	if s.deps.Turtle == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "turtle engine not configured"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"trades": s.deps.Turtle.Trades()})
}

func (s *Server) handleTurtleOrders(w http.ResponseWriter, r *http.Request) {
	if s.deps.Turtle == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "turtle engine not configured"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"orders": s.deps.Turtle.Orders()})
}

// turtleWatchSize is how many symbols a session watches. Small on purpose: the
// engine holds at most three positions, so a long watchlist adds alerts without
// adding opportunity, and every extra symbol is another way to be wrong.
const turtleWatchSize = 12

// startTurtleSession builds the watchlist from the live prediction inputs and
// then starts the session.
func (s *Server) startTurtleSession() turtle.Session {
	if s.deps.Engine != nil {
		now := time.Now()
		rows := s.deps.Engine.Snapshot(0, now.UnixMilli())
		cands := make([]turtle.Candidate, 0, len(rows))
		for i := range rows {
			r := &rows[i]
			if r.Price <= 0 {
				continue
			}
			c := turtle.Candidate{
				Symbol: r.Symbol, Price: r.Price,
				VolPct:         volPctOf(r),
				TrendAgreement: r.TrendAgreement,
				TrendSign:      r.TrendSign,
				BurstMultiple:  r.BurstMultiple,
				QuoteVolume24h: r.QuoteVolume24h,
				ATRPct:         0,
				DonchianHigh:   donchianHighOf(r),
				DonchianLow:    donchianLowOf(r),
			}
			if r.Precursor != nil {
				c.PrecursorScore = r.Precursor.Score
			}
			c.ATR = r.Sigma10s
			cands = append(cands, c)
		}
		cfg := s.deps.Turtle.Session().Config
		sel := turtle.SelectUniverse(cands, turtleWatchSize, cfg.VolVetoPct)
		s.deps.Turtle.SetWatchlist(sel)
	}
	return s.deps.Turtle.Start()
}

// volPctOf expresses recent dispersion as a percentage, the unit the veto uses.
func volPctOf(r *flow.Candidate) float64 {
	if r.Sigma10s <= 0 || r.Price <= 0 {
		return 0
	}
	return r.Sigma10s / r.Price * 100
}

// The Donchian bounds come from the symbol's own recent range, which is the
// closest thing to a channel the flow engine keeps: Price +/- the 10s dispersion
// is a one-sigma band, and the entry confirm wants a genuine break of the recent
// extreme rather than a one-sigma touch. Two sigma is used so the confirm means
// something on a 300 USDT account where a false entry costs 1% of equity.
func donchianHighOf(r *flow.Candidate) float64 {
	if r.Sigma10s <= 0 {
		return 0
	}
	return r.Price + 2*r.Sigma10s
}

func donchianLowOf(r *flow.Candidate) float64 {
	if r.Sigma10s <= 0 {
		return 0
	}
	return r.Price - 2*r.Sigma10s
}
