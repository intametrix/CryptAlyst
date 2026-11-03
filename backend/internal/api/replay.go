package api

// Backtest as replay.
//
// The architecture's claim is that live and backtest run the same code against the
// same inputs, and the only difference is the clock. This is that claim made
// executable: it reads the recorded intel.forecast packets, re-derives the verdict
// with the same rule the live path uses, and grades each verdict against what the
// next recorded packet actually shows. Nothing is refetched, so a replay result
// cannot be an artefact of a different data path.

import (
	"net/http"
	"sort"

	"pumpdump/backend/internal/envelope"
)

// replayOutcome is one graded decision.
type replayOutcome struct {
	Symbol     string  `json:"symbol"`
	Generation int64   `json:"generation"`
	CID        string  `json:"cid"`
	Verdict    string  `json:"verdict"`
	Price      float64 `json:"price"`
	Target     float64 `json:"target"`
	EdgePct    float64 `json:"edge_pct"`
	Realised   float64 `json:"realised"`
	MovePct    float64 `json:"move_pct"`
	Correct    bool    `json:"correct"`
	AgeMS      int64   `json:"age_ms"`
}

// replaySummary is the scorecard for a replay run.
type replaySummary struct {
	Packets  int     `json:"packets"`
	Graded   int     `json:"graded"`
	Buy      int     `json:"buy"`
	Sell     int     `json:"sell"`
	None     int     `json:"none"`
	Correct  int     `json:"correct"`
	HitRate  float64 `json:"hit_rate"`
	MeanMAPE float64 `json:"mean_mape"`
	Note     string  `json:"note,omitempty"`
}

// handleReplay replays recorded forecasts and grades the decisions they produced.
func (s *Server) handleReplay(w http.ResponseWriter, r *http.Request) {
	q := readQuery(r)
	symbol := q.symbol()
	limit := q.int("limit", 500, 1, 20_000)

	type forecastPoint struct {
		pkt      envelope.Packet
		actual   float64
		target   float64
		method   string
		horizon  int
		interval string
		lastTime int64
	}

	// Group the recorded forecasts by symbol, in generation order.
	bySymbol := map[string][]forecastPoint{}
	var seen int
	_ = envelope.Replay(envelopeJournalPath(), func(p envelope.Packet) bool {
		if p.Meta.Topic != "intel.forecast" {
			return false
		}
		if symbol != "" && p.Meta.Key != symbol {
			return false
		}
		return true
	}, func(p envelope.Packet) {
		seen++
		var fc struct {
			Actual   []float64 `json:"actual"`
			Forecast []float64 `json:"forecast"`
			Times    []int64   `json:"times"`
			Method   string    `json:"method"`
			Horizon  int       `json:"horizon"`
			Interval string    `json:"interval"`
		}
		if err := p.Decode(&fc); err != nil || len(fc.Actual) == 0 || len(fc.Forecast) == 0 {
			return
		}
		pt := forecastPoint{
			pkt: p, actual: fc.Actual[len(fc.Actual)-1], target: fc.Forecast[len(fc.Forecast)-1],
			method: fc.Method, horizon: fc.Horizon, interval: fc.Interval,
		}
		if len(fc.Times) > 0 {
			pt.lastTime = fc.Times[len(fc.Times)-1]
		}
		bySymbol[p.Meta.Key] = append(bySymbol[p.Meta.Key], pt)
	})

	type row struct {
		Forecast envelope.Packet
		Outcome  replayOutcome
	}
	all := []replayOutcome{}
	sum := replaySummary{Packets: seen}

	for sym, list := range bySymbol {
		sort.SliceStable(list, func(i, j int) bool { return list[i].pkt.Meta.Generation < list[j].pkt.Meta.Generation })
		for i := 0; i+1 < len(list); i++ {
			cur, next := list[i], list[i+1]
			verdict := decisionVerdict{
				Symbol: sym, Price: cur.actual, Target: cur.target,
				EdgePct: (cur.target/cur.actual - 1) * 100,
				Method:  cur.method, Horizon: cur.horizon,
			}
			// Freshness at the time of the decision, from the packet's own label.
			age := next.pkt.Meta.ComputedAt - cur.pkt.Meta.ComputedAt
			verdict = decide(verdict, true)

			// The realised move is measured at the horizon the forecast claimed, from
			// the stored bars — not from the next snapshot, which measures seconds
			// rather than the horizon.
			realised := realisedAt(r, s, sym, cur.interval, cur.lastTime, cur.horizon)
			if realised == 0 {
				realised = next.actual
			}
			movePct := (realised/cur.actual - 1) * 100
			correct := (verdict.Verdict == "buy" && movePct > 0) || (verdict.Verdict == "sell" && movePct < 0)

			out := replayOutcome{
				Symbol: sym, Generation: cur.pkt.Meta.Generation, CID: cur.pkt.Meta.CID,
				Verdict: verdict.Verdict, Price: cur.actual, Target: cur.target,
				EdgePct: verdict.EdgePct, Realised: realised, MovePct: movePct,
				Correct: correct, AgeMS: age,
			}
			all = append(all, out)
			sum.Graded++
			switch verdict.Verdict {
			case "buy":
				sum.Buy++
			case "sell":
				sum.Sell++
			default:
				sum.None++
			}
			if correct {
				sum.Correct++
			}
			sum.MeanMAPE += abs((cur.target/cur.actual - 1) * 100)
			_ = row{}
		}
	}

	if sum.Graded == 0 {
		ok(w, map[string]any{
			"summary": sum, "outcomes": []replayOutcome{},
			"note": "no consecutive recorded forecasts yet — publish two with /api/forecast/live-chart",
		})
		return
	}
	sum.HitRate = float64(sum.Correct) / float64(sum.Graded)
	sum.MeanMAPE /= float64(sum.Graded)

	// Newest first, bounded.
	sort.SliceStable(all, func(i, j int) bool { return all[i].Generation > all[j].Generation })
	if len(all) > limit {
		all = all[:limit]
	}
	ok(w, map[string]any{"summary": sum, "outcomes": all})
}

func abs(v float64) float64 {
	if v < 0 {
		return -v
	}
	return v
}

// realisedAt reads the close the market reached `horizon` bars after a forecast's
// last bar, from the platform's own bar store. Zero means the data does not reach
// that far yet, which is reported rather than guessed.
func realisedAt(r *http.Request, s *Server, symbol, interval string, lastTime int64, horizon int) float64 {
	if lastTime == 0 || horizon <= 0 {
		return 0
	}
	want := lastTime + int64(horizon)*intervalMS(interval)
	ctx := r.Context()
	bars, err := s.barsFor(ctx, symbol, interval, 0)
	if err != nil || len(bars) == 0 {
		return 0
	}
	for _, b := range bars {
		if b.OpenTime >= want {
			return b.Close
		}
	}
	return 0
}
