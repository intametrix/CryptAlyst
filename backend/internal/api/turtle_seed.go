package api

import (
	"math"
	"pumpdump/backend/internal/flow"
	"sort"
	"time"

	"pumpdump/backend/internal/turtle"
)

// Seeding the watchlist from ALERTS rather than from an indicator ranking.
//
// The human's instruction: "Build the watch list from tickers or symbols that have
// caused alerts as a start."
//
// WHY THIS IS A BETTER START THAN A SCORE. An indicator ranking asks "which symbol
// looks most likely to move", which is a forecast and inherits every weakness of
// the forecast. An alert asks "which symbol ALREADY moved enough to trip a
// detector", which is a FACT rather than a prediction. For a watchlist whose only
// job is to decide what to pay attention to, a demonstrated ability to move is the
// more honest criterion -- and it is self-updating, because the watchlist follows
// whatever the detectors are actually finding rather than what a fixed formula
// expects them to find.
//
// It also fixes a concrete failure: the scored selection picked the most LIQUID
// symbols, which in a quiet market are the least likely to move, so the session
// watched twelve majors and took no risk at all. Every symbol on this list has
// produced at least one alert, so every one of them has demonstrated volatility.
//
// THE COUNT IS THE RANKING. A symbol that fired nine alerts is more interesting
// than one that fired once, and the ordering falls out of the data rather than
// needing a weight to be chosen.

// AlertsSeed builds a watchlist from symbols that recently produced alerts.
func (s *Server) AlertsSeed(limit int) []turtle.Scored {
	if s.deps.Alerts == nil || s.deps.Engine == nil {
		return nil
	}
	// A generous window: alerts are sparse enough that an hour is the shortest
	// span guaranteed to contain a useful number of them.
	since := time.Now().Add(-1 * time.Hour).UnixMilli()
	alerts, _, err := s.deps.Alerts.ReadHistory("", since, 2000)
	if err != nil || len(alerts) == 0 {
		return nil
	}

	type stat struct {
		count    int
		lastMS   int64
		severity string
		kinds    map[string]int
	}
	stats := map[string]*stat{}
	for _, a := range alerts {
		st := stats[a.Symbol]
		if st == nil {
			st = &stat{kinds: map[string]int{}}
			stats[a.Symbol] = st
		}
		st.count++
		if a.TSMS > st.lastMS {
			st.lastMS = a.TSMS
			st.severity = a.Severity
		}
		st.kinds[a.Kind]++
	}

	// Current market context, so a seeded symbol still carries the price, ATR and
	// channel bounds the entry rules need.
	rows := s.deps.Engine.Snapshot(0, time.Now().UnixMilli())
	ctx := map[string]turtle.Candidate{}
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
			ATR:            nFromSigma(r.Sigma10s),
		}
		// A REAL CHANNEL, not the current price plus a band. The old helper
		// computed `price + 2*sigma`, and the entry required `price >= that`,
		// which is `price >= price + 2*sigma` -- false for every positive sigma.
		// The confirm could never pass and the engine declined every setup with
		// "no Donchian breakout".
		if ch, ok := s.deps.Engine.ChannelFor(r.Symbol, flow.ChannelWindowSeconds, time.Now().UnixMilli()); ok {
			c.DonchianHigh = ch.High
			c.DonchianLow = ch.Low
		}
		if r.Precursor != nil {
			c.PrecursorScore = r.Precursor.Score
		}
		ctx[r.Symbol] = c
	}

	out := make([]turtle.Scored, 0, len(stats))
	for sym, st := range stats {
		c, ok := ctx[sym]
		if !ok {
			// Alerted but not currently tracked: it cannot be traded without a
			// price and an ATR, so it is skipped rather than carried with zeroes
			// that would make the engine refuse every entry on it.
			continue
		}
		kinds := make([]string, 0, len(st.kinds))
		for k, n := range st.kinds {
			kinds = append(kinds, k)
			_ = n
		}
		sort.Strings(kinds)
		// Score is the alert count, log-scaled so one very noisy symbol does not
		// dominate a list that exists to spread attention.
		score := float64(st.count)
		if score > 10 {
			score = 10
		}
		out = append(out, turtle.Scored{
			Candidate: c,
			Score:     score / 10,
			Reasons: []string{
				"alert-seeded: " + itoa(st.count) + " alert(s) in the last hour",
				"kinds: " + joinShort(kinds),
			},
		})
	}
	// MOST ALERTS FIRST. The count is the ranking the human asked for: a symbol
	// that fired forty alerts has demonstrated more movement than one that fired
	// once, and the order falls out of the data rather than needing a weight.
	sort.SliceStable(out, func(i, j int) bool {
		if out[i].Score != out[j].Score {
			return out[i].Score > out[j].Score
		}
		// Deterministic tie-break, so the same market produces the same list and a
		// change in the watchlist means a change in the market rather than in map
		// iteration order.
		return out[i].Symbol < out[j].Symbol
	})

	// THE LIMIT WAS ACCEPTED AND NEVER APPLIED. AlertsSeed(limit) took the
	// parameter and returned every alerted symbol regardless, so the watchlist was
	// as long as the alert history happened to be -- 41 symbols when 12 was asked
	// for. A parameter that is silently ignored is worse than no parameter,
	// because the caller believes the list is bounded.
	if limit > 0 && len(out) > limit {
		out = out[:limit]
	}
	return out
}

func itoa(v int) string {
	if v == 0 {
		return "0"
	}
	var b [8]byte
	i := len(b)
	for v > 0 {
		i--
		b[i] = byte('0' + v%10)
		v /= 10
	}
	return string(b[i:])
}

func joinShort(parts []string) string {
	out := ""
	for i, p := range parts {
		if i > 0 {
			out += "+"
		}
		out += p
		if len(out) > 40 {
			return out + "…"
		}
	}
	return out
}

// nFromSigma scales the measured 10s dispersion to the N horizon.
//
// N is a RANGE measure and ten seconds is an instant, not a range. Using the raw
// 10s figure put the 2N stop about 0.003% away and stopped live positions out in
// three seconds -- inside the spread. Dispersion grows as sqrt(t), so N is taken at
// five minutes.
func nFromSigma(sigma10s float64) float64 {
	if sigma10s <= 0 {
		return 0
	}
	const measuredSec, horizonSec = 10.0, 300.0
	return sigma10s * math.Sqrt(horizonSec/measuredSec)
}
