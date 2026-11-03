package api

import (
	"net/http"
	"strings"
	"time"

	"pumpdump/backend/internal/envelope"
)

// A decision made from recorded evidence.
//
// This is the point of the envelope: the decision does not recompute the forecast,
// it reads the packet that was published, checks that packet's own computed_at
// against the topic's limit, and publishes its verdict with the forecast's cid as
// parent_cid. The chain — decision -> forecast -> candles — is reconstructable from
// the journal alone.

// decisionVerdict is the operator's output.
type decisionVerdict struct {
	Verdict     string  `json:"verdict"` // buy | sell | none | refused
	Symbol      string  `json:"symbol"`
	Interval    string  `json:"interval"`
	Method      string  `json:"method"`
	Price       float64 `json:"price"`
	Target      float64 `json:"target"`
	EdgePct     float64 `json:"edge_pct"`
	Horizon     int     `json:"horizon"`
	Reason      string  `json:"reason"`
	AgeMS       int64   `json:"age_ms"`
	LimitMS     int64   `json:"limit_ms"`
	ForecastCID string  `json:"forecast_cid,omitempty"`
}

// decisionThresholdPct is the edge a forecast must claim before it is worth a trade.
// Without a floor every projection produces an order, which is how a forecast turns
// into noise with a commission.
const decisionThresholdPct = 0.05

// handleDecision reads the newest recorded forecast for a symbol and publishes a
// model.decision packet from it.
func (s *Server) handleDecision(w http.ResponseWriter, r *http.Request) {
	q := readQuery(r)
	symbol := q.symbol()
	if symbol == "" {
		fail(w, "symbol required")
		return
	}
	now := time.Now()

	// Replay the journal — the same call a backtest makes.
	var latest *envelope.Packet
	path := envelopeJournalPath()
	_ = envelope.Replay(path, func(p envelope.Packet) bool {
		return p.Meta.Topic == "intel.forecast" && p.Meta.Key == symbol
	}, func(p envelope.Packet) {
		cp := p
		if latest == nil || cp.Meta.Generation >= latest.Meta.Generation {
			latest = &cp
		}
	})

	if latest == nil {
		fail(w, "no recorded forecast for "+symbol+" — call /api/forecast/live-chart first")
		return
	}

	var fc struct {
		Actual   []float64 `json:"actual"`
		Forecast []float64 `json:"forecast"`
		Times    []int64   `json:"times"`
		Method   string    `json:"method"`
		Interval string    `json:"interval"`
		Horizon  int       `json:"horizon"`
	}
	if err := latest.Decode(&fc); err != nil {
		fail(w, "recorded forecast unreadable: "+err.Error())
		return
	}

	fresh, age, limit := latest.Fresh(now)
	verdict := decisionVerdict{
		Symbol: symbol, Interval: fc.Interval, Method: fc.Method,
		Horizon: fc.Horizon, AgeMS: age, LimitMS: limit,
		ForecastCID: latest.Meta.CID,
	}
	if len(fc.Actual) > 0 {
		verdict.Price = fc.Actual[len(fc.Actual)-1]
	}
	if len(fc.Forecast) > 0 {
		verdict.Target = fc.Forecast[len(fc.Forecast)-1]
	}
	if verdict.Price > 0 {
		verdict.EdgePct = (verdict.Target/verdict.Price - 1) * 100
	}

	verdict = decide(verdict, fresh)

	req := envelope.Request{
		Topic: "model.decision", Key: symbol, Producer: "engine",
		ParentCID: latest.Meta.CID,
		Refs: []envelope.Ref{{
			Topic: "intel.forecast", Key: symbol,
			Generation: latest.Meta.Generation,
			AgeMS:      age,
		}},
	}
	pkt, pl, err := envelope.Run(req, now, func() (any, error) { return verdict, nil })
	if err != nil {
		// A refusal is an envelope too.
		ref := envelope.RefusalPacket(req, pl, err.Error())
		recordEnvelope(ref)
		ok(w, map[string]any{"verdict": verdict, "refused": err.Error()})
		return
	}
	recordEnvelope(pkt)
	ok(w, map[string]any{
		"verdict": verdict, "cid": pkt.Meta.CID, "generation": pkt.Meta.Generation,
		"parent_cid": pkt.Meta.ParentCID, "pipeline": pkt.Meta.Pipeline,
	})
}

// handleEnvelopeChain walks a cid back through its parent links, which is the
// "why did we buy?" query the spec asks for.
func (s *Server) handleEnvelopeChain(w http.ResponseWriter, r *http.Request) {
	cid := strings.TrimSpace(r.URL.Query().Get("cid"))
	if cid == "" {
		fail(w, "cid required")
		return
	}
	// Build the journal index once, then walk BACK along parent links. Walking
	// forward answered "what came from this", which is not the question: a reviewer
	// asking "why did we buy?" wants the inputs, not the consequences.
	byCID := map[string]envelope.Packet{}
	var children []envelope.Packet
	_ = envelope.Replay(envelopeJournalPath(), nil, func(p envelope.Packet) {
		if p.Meta.CID != "" {
			byCID[p.Meta.CID] = p
		}
		if p.Meta.ParentCID == cid {
			children = append(children, p)
		}
	})

	var lineage []envelope.Packet
	seen := map[string]bool{}
	for cur := cid; cur != "" && !seen[cur]; {
		seen[cur] = true
		p, okp := byCID[cur]
		if !okp {
			break
		}
		lineage = append(lineage, p)
		cur = p.Meta.ParentCID
	}
	ok(w, map[string]any{
		"cid": cid, "packets": lineage, "count": len(lineage), "children": children,
	})
}

// decide is the rule, kept pure so it can be tested without a journal, a network or
// a clock. A stale forecast is refused before any edge is considered: an old number
// that happens to look profitable is the most expensive kind of number.
func decide(v decisionVerdict, fresh bool) decisionVerdict {
	switch {
	case !fresh:
		v.Verdict = "refused"
		v.Reason = "stale forecast"
	case v.EdgePct >= decisionThresholdPct:
		v.Verdict = "buy"
		v.Reason = "forecast above threshold"
	case v.EdgePct <= -decisionThresholdPct:
		v.Verdict = "sell"
		v.Reason = "forecast below threshold"
	default:
		v.Verdict = "none"
		v.Reason = "edge inside the threshold"
	}
	return v
}
