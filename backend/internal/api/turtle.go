package api

import (
	"context"
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

// handleTurtleStats serves the Progress read. Expectancy is the number that
// matters for the alpha goal: a system can win most trades and still lose money,
// and a win rate alone hides that.
func (s *Server) handleTurtleStats(w http.ResponseWriter, r *http.Request) {
	if s.deps.Turtle == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "turtle engine not configured"})
		return
	}
	writeJSON(w, http.StatusOK, s.deps.Turtle.Progress())
}

// handleTurtleRotations serves the watchlist audit trail. A watchlist that
// changes silently makes every downstream result unattributable, so the churn is
// a first-class surface rather than a log line.
func (s *Server) handleTurtleRotations(w http.ResponseWriter, r *http.Request) {
	if s.deps.Turtle == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "turtle engine not configured"})
		return
	}
	recs := s.deps.Turtle.Rotations()
	writeJSON(w, http.StatusOK, map[string]any{
		"rotations": recs, "count": len(recs),
		"rotation_count": s.deps.Turtle.RotationCount(),
		"interval_sec":   int(turtle.RotationInterval.Seconds()),
	})
}

// handleTurtleEquity serves the sampled running equity, for a PnL chart rather
// than a single number that moves.
func (s *Server) handleTurtleEquity(w http.ResponseWriter, r *http.Request) {
	if s.deps.Turtle == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "turtle engine not configured"})
		return
	}
	curve := s.deps.Turtle.EquityCurve()
	writeJSON(w, http.StatusOK, map[string]any{"curve": curve, "points": len(curve)})
}

// handleTurtleTrades serves OPEN trades: positions the account is carrying, each
// with its own unrealised PnL marked to the current price.
//
// A trade appears here the instant it fills, which is what the human asked for.
// Settled round trips live at /api/turtle/settled -- see trades_view.go for why
// they are two lists rather than one with a status column.
func (s *Server) handleTurtleTrades(w http.ResponseWriter, r *http.Request) {
	if s.deps.Turtle == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "turtle engine not configured"})
		return
	}
	open := s.deps.Turtle.OpenTrades()
	total := 0.0
	for _, t := range open {
		total += t.UnrealizedPnL
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"trades": open, "count": len(open),
		"unrealized_pnl": round2api(total),
		"note":           "open positions, marked to the current price; each carries its own unrealized PnL",
	})
}

// handleTurtleSettled serves completed round trips. Their PnL can never change.
func (s *Server) handleTurtleSettled(w http.ResponseWriter, r *http.Request) {
	if s.deps.Turtle == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "turtle engine not configured"})
		return
	}
	settled := s.deps.Turtle.SettledTrades()
	total := 0.0
	for _, t := range settled {
		total += t.PnL
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"trades": settled, "count": len(settled), "realized_pnl": round2api(total),
		"note": "closed round trips; realized PnL is final",
	})
}

func round2api(v float64) float64 {
	return float64(int64(v*100+sign(v)*0.5)) / 100
}

func sign(v float64) float64 {
	if v < 0 {
		return -1
	}
	return 1
}

func (s *Server) handleTurtleOrders(w http.ResponseWriter, r *http.Request) {
	if s.deps.Turtle == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "turtle engine not configured"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"orders": s.deps.Turtle.Orders()})
}

// selectTurtleUniverse builds a fresh ranking from the live prediction inputs.
// Used at session start AND by the rotation loop, so the two can never disagree
// about what "the best symbols" means.
func (s *Server) selectTurtleUniverse() []turtle.Scored {
	if s.deps.Engine == nil || s.deps.Turtle == nil {
		return nil
	}
	rows := s.deps.Engine.Snapshot(0, time.Now().UnixMilli())
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
		}
		// Same correction as the alert seeder: a real recent envelope.
		if ch, ok := s.deps.Engine.ChannelFor(r.Symbol, flow.ChannelWindowSeconds, time.Now().UnixMilli()); ok {
			c.DonchianHigh = ch.High
			c.DonchianLow = ch.Low
		}
		c.ATR = nFromSigma(r.Sigma10s)
		if r.Precursor != nil {
			c.PrecursorScore = r.Precursor.Score
		}
		cands = append(cands, c)
	}
	cfg := s.deps.Turtle.BookConfig()
	return turtle.SelectUniverse(cands, turtleWatchSize, cfg.VolVetoPct)
}

// StartTurtleRotation is called once at boot. The loop decides for itself whether
// a session is live, so it does not need starting and stopping with the button.
func (s *Server) StartTurtleRotation(ctx context.Context) {
	if s.deps.Turtle == nil {
		return
	}
	go s.deps.Turtle.RunRotation(ctx, func(time.Time) []turtle.Scored {
		return s.selectTurtleUniverse()
	}, turtle.RotationInterval)
}

// turtleWatchSize is how many symbols a session watches.
//
// Set by the human to 50. The earlier value of 12 was defended on the grounds that
// the engine holds at most three positions so a longer list adds alerts without
// adding opportunity -- that reasoning was wrong for THIS selection. When the
// watchlist is seeded by alert count, a longer list is not more noise: it is the
// fifty symbols that have most recently demonstrated they can move, and the
// position limit is enforced separately by the unit caps. Excluding a symbol that
// is actively alerting costs a real opportunity; including one that never fires
// costs nothing, because a watchlist entry on its own takes no risk.
// buildAllocation turns a ranked watchlist into a risk-weighted distribution.
func (s *Server) buildAllocation(sel []turtle.Scored) turtle.Allocation {
	cfg := s.deps.Turtle.BookConfig()
	cands := make([]turtle.RiskWeight, 0, len(sel))
	for _, w := range sel {
		// Alert count is recovered from the seeder's own reason string, so the
		// weighting uses the same evidence the ranking did rather than a second,
		// possibly different, measurement.
		count := alertCountFromReasons(w.Reasons)
		score, reasons := turtle.RiskScoreFor(count, w.PrecursorScore, w.TrendAgreement, w.VolPct, cfg.VolVetoPct)
		cands = append(cands, turtle.RiskWeight{Symbol: w.Symbol, Score: score, Reasons: reasons})
	}
	return turtle.AllocateRisk(cands, turtle.PortfolioRiskBudgetPct)
}

// alertCountFromReasons pulls the alert count back out of the seed reason. Kept
// explicit and total: an unparseable reason yields 0, which falls back to the flat
// risk rate rather than inventing a weight.
func alertCountFromReasons(reasons []string) int {
	for _, r := range reasons {
		const marker = "alert-seeded: "
		if len(r) > len(marker) && r[:len(marker)] == marker {
			n := 0
			for _, ch := range r[len(marker):] {
				if ch < '0' || ch > '9' {
					break
				}
				n = n*10 + int(ch-'0')
			}
			return n
		}
	}
	return 0
}

const turtleWatchSize = 50

// startTurtleSession builds the watchlist from the live prediction inputs and
// then starts the session.
func (s *Server) startTurtleSession() turtle.Session {
	// ALERTS FIRST. A symbol that already tripped a detector has demonstrated it
	// can move; a symbol that merely ranks well on an indicator has not. The
	// scored selection remains as the fallback for a session that starts before
	// any alert has fired.
	sel := s.AlertsSeed(turtleWatchSize)
	if len(sel) == 0 {
		sel = s.selectTurtleUniverse()
	}
	if len(sel) > 0 {
		s.deps.Turtle.SetWatchlist(sel)
		// RISK-WEIGHT THE WHOLE BOOK before the first entry. Sizing reads the
		// allocation, so setting it after a session starts would mean the first
		// positions were sized flat while the rest were weighted -- two different
		// risk regimes in one session's PnL.
		s.deps.Turtle.SetAllocation(s.buildAllocation(sel))
	}
	// ALL CAPITAL DEPLOYED. Set before Start so the first position is sized in the
	// same mode as the last -- a session that changed sizing mode part way through
	// would produce a PnL belonging to two different systems.
	s.deps.Turtle.SetDeploymentMode(turtle.DeployFull)
	return s.deps.Turtle.Start()
}

// volPctOf expresses recent dispersion as a percentage, the unit the veto uses.
func volPctOf(r *flow.Candidate) float64 {
	if r.Sigma10s <= 0 || r.Price <= 0 {
		return 0
	}
	return r.Sigma10s / r.Price * 100
}

// handleTurtleAllocation serves the portfolio's risk distribution: each symbol's
// share of the account's risk budget, with the score and reasons behind it.
//
// Exposed because a weight nobody can see is a weight nobody can challenge, and
// the whole point of the human's rule is that exposure is a deliberate decision
// rather than an accident of which alerts happened to fire.
func (s *Server) handleTurtleAllocation(w http.ResponseWriter, r *http.Request) {
	if s.deps.Turtle == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "turtle engine not configured"})
		return
	}
	alloc := s.deps.Turtle.Allocation()
	writeJSON(w, http.StatusOK, map[string]any{
		"allocation": alloc,
		"budget_pct": turtle.PortfolioRiskBudgetPct,
		"note":       "the equity is the ACCOUNT's, shared across every symbol; each weight is that symbol's share of a bounded portfolio risk budget, and no single asset may take more than 2x an equal share",
	})
}

// handleTurtleExposure reports how much of the account is at work, so "all capital
// deployed" is a number on screen rather than an intention.
func (s *Server) handleTurtleExposure(w http.ResponseWriter, r *http.Request) {
	if s.deps.Turtle == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "turtle engine not configured"})
		return
	}
	writeJSON(w, http.StatusOK, s.deps.Turtle.Exposure())
}
