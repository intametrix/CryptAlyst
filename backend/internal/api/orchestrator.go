package api

// The orchestrator.
//
// From the architecture: "the orchestrator never knows what a model computes. It
// knows capabilities, deadlines and metadata." A capability says which topic
// answers it and how old an answer may be; the journal says what the newest answer
// is and when it was computed. From those two things alone the orchestrator can say
// what needs recomputing — without importing a single model.
//
// When it decides work is needed it publishes a control.engine packet naming the
// capability. It does not call the calculator directly: a request for work is an
// envelope like everything else, so it is recorded and can be replayed.

import (
	"errors"
	"net/http"
	"os"
	"time"

	"pumpdump/backend/internal/envelope"
)

// capability is one thing the platform can compute, expressed as the topic that
// carries its answer and the freshness that answer must have.
type capability struct {
	Name     string `json:"name"`
	Topic    string `json:"topic"`
	Producer string `json:"producer"`
	// MaxAgeMS is the deadline: older than this and the answer is not usable.
	MaxAgeMS int64 `json:"max_age_ms"`
}

// capabilities is the table the orchestrator reasons over. It is derived from the
// topic registry rather than invented, so a new topic with a producer and a TTL
// becomes a schedulable capability by addition, not by editing the orchestrator.
func capabilities() []capability {
	out := []capability{}
	seen := map[string]bool{}
	for _, t := range envelope.Topics() {
		if !t.Record || len(t.Producer) == 0 || t.TTLMS == 0 || seen[t.Name] {
			continue
		}
		seen[t.Name] = true
		out = append(out, capability{
			Name:     t.Name,
			Topic:    t.Name,
			Producer: t.Producer[0],
			MaxAgeMS: t.TTLMS,
		})
	}
	return out
}

// capabilityState is what the journal knows about one capability for one key.
type capabilityState struct {
	Capability string `json:"capability"`
	Topic      string `json:"topic"`
	Key        string `json:"key"`
	Generation int64  `json:"generation"`
	AgeMS      int64  `json:"age_ms"`
	MaxAgeMS   int64  `json:"max_age_ms"`
	Decision   string `json:"decision"` // serve | recompute
	Reason     string `json:"reason"`
	CID        string `json:"cid,omitempty"`
}

// planFor reads the journal and decides, per capability and key, whether the newest
// answer may be served or must be recomputed.
func (s *Server) planFor(symbol string, now time.Time) ([]capabilityState, error) {
	type latest struct {
		gen        int64
		computedAt int64
		cid        string
	}
	newest := map[string]latest{}
	err := envelope.Replay(envelopeJournalPath(), func(p envelope.Packet) bool {
		if symbol != "" && p.Meta.Key != symbol {
			return false
		}
		return true
	}, func(p envelope.Packet) {
		k := p.Meta.Topic + "|" + p.Meta.Key
		cur, ok := newest[k]
		if !ok || p.Meta.Generation >= cur.gen {
			newest[k] = latest{gen: p.Meta.Generation, computedAt: p.Meta.ComputedAt, cid: p.Meta.CID}
		}
	})
	// No journal yet is not an error: it means nothing has been computed, which is
	// exactly the state the plan should report as "recompute".
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}

	caps := capabilities()
	// Keys present in the journal that these capabilities concern.
	keys := map[string]bool{}
	for k := range newest {
		keys[k] = true
	}
	if symbol != "" {
		keys = map[string]bool{}
		for _, c := range caps {
			keys[c.Topic+"|"+symbol] = true
		}
	}

	out := make([]capabilityState, 0, len(caps))
	for _, c := range caps {
		// Without a key there is nothing per-symbol to schedule; global topics get a
		// single row so the plan is complete.
		if len(keys) == 0 {
			out = append(out, capabilityState{
				Capability: c.Name, Topic: c.Topic, Key: symbol, MaxAgeMS: c.MaxAgeMS,
				Decision: "recompute", Reason: "nothing recorded",
			})
			continue
		}
		for k := range keys {
			topic, key := splitTopicKey(k)
			if topic != c.Topic {
				continue
			}
			st := capabilityState{
				Capability: c.Name, Topic: c.Topic, Key: key, MaxAgeMS: c.MaxAgeMS,
			}
			if v, ok := newest[k]; ok {
				st.Generation = v.gen
				st.CID = v.cid
				st.AgeMS = now.UnixMilli() - v.computedAt
				if st.AgeMS <= c.MaxAgeMS {
					st.Decision = "serve"
					st.Reason = "within its deadline"
				} else {
					st.Decision = "recompute"
					st.Reason = "older than its deadline"
				}
			} else {
				st.Decision = "recompute"
				st.Reason = "nothing recorded"
			}
			out = append(out, st)
		}
	}
	return out, nil
}

func splitTopicKey(k string) (string, string) {
	for i := 0; i < len(k); i++ {
		if k[i] == '|' {
			return k[:i], k[i+1:]
		}
	}
	return k, ""
}

// handleOrchestratorPlan answers what the platform should compute next.
func (s *Server) handleOrchestratorPlan(w http.ResponseWriter, r *http.Request) {
	q := readQuery(r)
	symbol := q.symbol()
	plan, err := s.planFor(symbol, time.Now())
	if err != nil {
		ok(w, map[string]any{"plan": []capabilityState{}, "note": "journal not readable: " + err.Error()})
		return
	}
	recompute := 0
	for _, p := range plan {
		if p.Decision == "recompute" {
			recompute++
		}
	}
	ok(w, map[string]any{"plan": plan, "count": len(plan), "recompute": recompute})
}

// handleOrchestratorTick publishes a work request for every stale capability. The
// request is an envelope, not a function call: it is recorded and replayable.
func (s *Server) handleOrchestratorTick(w http.ResponseWriter, r *http.Request) {
	if !requireMethod(w, r, http.MethodPost) {
		return
	}
	q := readQuery(r)
	symbol := q.symbol()
	now := time.Now()
	plan, err := s.planFor(symbol, now)
	if err != nil {
		fail(w, err.Error())
		return
	}

	type requested struct {
		Capability string `json:"capability"`
		Key        string `json:"key"`
		Reason     string `json:"reason"`
		CID        string `json:"cid"`
	}
	done := []requested{}
	for _, p := range plan {
		if p.Decision != "recompute" {
			continue
		}
		body := map[string]any{
			"request": p.Capability, "key": p.Key, "reason": p.Reason,
			"stale_generation": p.Generation, "age_ms": p.AgeMS, "max_age_ms": p.MaxAgeMS,
		}
		req := envelope.Request{Topic: "control.engine", Key: p.Key, Producer: "engine"}
		if p.CID != "" {
			req.ParentCID = p.CID
		}
		pkt, _, perr := envelope.Run(req, now, func() (any, error) { return body, nil })
		if perr != nil {
			continue
		}
		recordEnvelope(pkt)
		done = append(done, requested{Capability: p.Capability, Key: p.Key, Reason: p.Reason, CID: pkt.Meta.CID})
	}
	ok(w, map[string]any{"requested": done, "count": len(done)})
}
