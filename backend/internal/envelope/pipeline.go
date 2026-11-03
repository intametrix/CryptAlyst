package envelope

// The pipeline every packet passes before its payload is published.
//
// authority -> keying -> correlation -> freshness -> operator -> transformer.
//
// A refusal is recorded as a packet on that topic, so "why not" is something you
// can open rather than a line in a log. This is the ATLAS rule: every output is
// either a step on an existing envelope or a new envelope.

import (
	"fmt"
	"net/http"
	"time"
)

// Stage names one interceptor.
type Stage struct {
	Name   string `json:"name"`
	Passed bool   `json:"passed"`
	Rule   string `json:"rule,omitempty"`
	Detail string `json:"detail,omitempty"`
}

// Pipeline is the record of what ran, attached to a packet's meta.hops.
type Pipeline struct {
	Stages []Stage `json:"stages"`
}

// AllPassed reports whether every stage passed.
func (p Pipeline) AllPassed() bool {
	for _, s := range p.Stages {
		if !s.Passed {
			return false
		}
	}
	return true
}

// FirstRefusal returns the failing stage, if any.
func (p Pipeline) FirstRefusal() (Stage, bool) {
	for _, s := range p.Stages {
		if !s.Passed {
			return s, true
		}
	}
	return Stage{}, false
}

// Request describes one publication attempt.
type Request struct {
	Topic    string
	Key      string
	Producer string
	// ComputedAt is when the producer computed the payload. Zero means "now".
	ComputedAt int64
	// CID is the correlation id: minted here if empty.
	CID string
	// ParentCID chains a decision to the envelope that triggered it.
	ParentCID string
	// Refs are the inputs the payload read.
	Refs []Ref
}

// Run applies the interceptors and returns the packet to publish.
//
// The operator is the caller: it computes the payload only after Run says the
// request may proceed, which is what makes an interceptor a gate rather than a
// comment.
func Run(req Request, now time.Time, data func() (any, error)) (Packet, Pipeline, error) {
	pl := Pipeline{}

	// authority — the producer may write this topic.
	t, known := TopicByName(req.Topic)
	authority := Stage{Name: "authority", Rule: "producer registered for topic"}
	switch {
	case !known:
		authority.Passed = false
		authority.Detail = fmt.Sprintf("unknown topic %q", req.Topic)
	case !MayProduce(req.Topic, req.Producer):
		authority.Passed = false
		authority.Detail = fmt.Sprintf("producer %q may not write %q", req.Producer, req.Topic)
	default:
		authority.Passed = true
	}
	pl.Stages = append(pl.Stages, authority)
	if !authority.Passed {
		return Packet{}, pl, fmt.Errorf("%s", authority.Detail)
	}

	// keying — the topic is keyed by symbol where it concerns one.
	keying := Stage{Name: "keying", Passed: true, Rule: "symbol key present"}
	switch req.Topic {
	case "market.price", "market.depth", "market.candle", "market.mini24h", "ticks",
		"intel.signal", "intel.flow", "intel.ta", "intel.forecast", "model.decision",
		"model.fill", "model.pnl", "risk.state", "risk.refusal":
		if req.Key == "" {
			keying.Passed = false
			keying.Detail = "topic requires a symbol key"
		}
	}
	pl.Stages = append(pl.Stages, keying)
	if !keying.Passed {
		return Packet{}, pl, fmt.Errorf("%s", keying.Detail)
	}

	// correlation — a cid is minted and chained.
	cid := req.CID
	if cid == "" {
		cid = MintCID(req.Topic, req.Key, now)
	}
	pl.Stages = append(pl.Stages, Stage{Name: "correlation", Passed: true, Rule: "cid minted", Detail: cid})

	// freshness — the payload's own label is within the topic's limit.
	computedAt := req.ComputedAt
	if computedAt == 0 {
		computedAt = now.UnixMilli()
	}
	age := now.UnixMilli() - computedAt
	fresh := Stage{Name: "freshness", Passed: true, Rule: fmt.Sprintf("age <= %dms", t.TTLMS)}
	if t.TTLMS > 0 && age > t.TTLMS {
		fresh.Passed = false
		fresh.Detail = fmt.Sprintf("computed_at %dms ago exceeds %dms", age, t.TTLMS)
	}
	pl.Stages = append(pl.Stages, fresh)
	if !fresh.Passed {
		return Packet{}, pl, fmt.Errorf("%s", fresh.Detail)
	}

	// operator — the caller computes the payload.
	payload, err := data()
	if err != nil {
		return Packet{}, pl, err
	}
	b, err := NewPacketWith(req.Topic, req.Key, req.Producer, payload, req.Refs...)
	if err != nil {
		return Packet{}, pl, err
	}
	b.Meta.CID = cid
	b.Meta.ParentCID = req.ParentCID
	b.Meta.ComputedAt = computedAt

	// transformer — normalisation only; the shape is fixed.
	b.Meta.Pipeline = pl.Stages
	b.Meta.Hops = stageNames(pl)
	return b, pl, nil
}

// RefusalPacket records a refused publication as an envelope on its topic, which is
// what makes "why not" auditable.
func RefusalPacket(req Request, pl Pipeline, reason string) Packet {
	stage, _ := pl.FirstRefusal()
	body := map[string]any{
		"verdict": "REFUSED",
		"reason":  reason,
		"topic":   req.Topic,
		"key":     req.Key,
		"stage":   stage.Name,
		"rule":    stage.Rule,
	}
	b, err := NewPacket("system.error", req.Key, "platform", body, req.Refs...)
	if err != nil {
		return Packet{}
	}
	b.Meta.Pipeline = pl.Stages
	b.Meta.Hops = stageNames(pl)
	return b
}

func stageNames(pl Pipeline) []string {
	out := make([]string, 0, len(pl.Stages))
	for _, s := range pl.Stages {
		name := s.Name
		if !s.Passed {
			name += ":refused"
		}
		out = append(out, name)
	}
	return out
}

// Write publishes a packet to the wire, so handlers answer with an envelope rather
// than a bespoke map.
func Write(w http.ResponseWriter, p Packet, status int) {
	w.Header().Set("content-type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(mustJSON(p))
}
