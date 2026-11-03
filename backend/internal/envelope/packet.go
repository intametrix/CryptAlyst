package envelope

// packet.go is the ATLAS envelope: data, meta, references.
//
// The platform's wire contract. The existing Envelope in envelope.go is the
// Go↔agent job protocol and keeps its shape; a Packet is what topics carry.
//
// Why two types and not one: the agent protocol is versioned independently and a
// running agent on the GPU box speaks version 1 today. Merging them would either
// break that agent or freeze the platform envelope at the agent's pace. The Packet
// is the platform contract; the job protocol can carry a Packet later without a
// wire break.
//
// Invariant (from the spec): the shape never changes. Everything that varies —
// venues, indicators, models, stores — lives in operators and transformers around
// it, never in a new field on the envelope.

import (
	"encoding/json"
	"time"
)

// PacketVersion is the platform envelope revision.
const PacketVersion = 1

// Plane groups topics by traffic class.
type Plane string

// Planes.
const (
	PlaneMarket  Plane = "market"
	PlaneIntel   Plane = "intel"
	PlaneModel   Plane = "model"
	PlaneRisk    Plane = "risk"
	PlaneControl Plane = "control"
	PlaneSystem  Plane = "system"
)

// Meta is provenance: who computed this, when, in what order, and under which
// correlation id. A staleness gate reads ComputedAt rather than guessing from a
// wall clock.
type Meta struct {
	Version    int      `json:"version"`
	Plane      Plane    `json:"plane"`
	Topic      string   `json:"topic"`
	Key        string   `json:"key,omitempty"`
	Producer   string   `json:"producer"`
	Generation int64    `json:"generation"`
	Seq        int64    `json:"seq,omitempty"`
	CID        string   `json:"cid,omitempty"`
	ParentCID  string   `json:"parent_cid,omitempty"`
	ComputedAt int64    `json:"computed_at"`
	ReceivedAt int64    `json:"received_at,omitempty"`
	ComputeMS  float64  `json:"compute_ms,omitempty"`
	Hops       []string `json:"hops,omitempty"`
	// Pipeline is the record of the interceptors that ran, so a reader can see
	// which checks passed without trusting a log line.
	Pipeline []Stage `json:"pipeline,omitempty"`
}

// Ref is an input the payload read: topic, key, the generation it read, and how
// old that input already was. Refer, do not copy.
type Ref struct {
	Topic      string `json:"topic"`
	Key        string `json:"key,omitempty"`
	Generation int64  `json:"generation,omitempty"`
	AgeMS      int64  `json:"age_ms,omitempty"`
}

// Packet is the envelope itself.
type Packet struct {
	Data json.RawMessage `json:"data"`
	Meta Meta            `json:"meta"`
	Refs []Ref           `json:"refs,omitempty"`
}

// NewPacket wraps a payload in the envelope shape. The caller supplies the topic
// and producer; generation and timestamps are stamped here so no producer can
// forget them.
func NewPacket(topic, key, producer string, data any, refs ...Ref) (Packet, error) {
	b, err := json.Marshal(data)
	if err != nil {
		return Packet{}, err
	}
	t, ok := TopicByName(topic)
	plane := PlaneSystem
	if ok {
		plane = t.Plane
	}
	now := time.Now().UnixMilli()
	return Packet{
		Data: b,
		Meta: Meta{
			Version:    PacketVersion,
			Plane:      plane,
			Topic:      topic,
			Key:        key,
			Producer:   producer,
			Generation: NextGeneration(topic, key),
			ComputedAt: now,
		},
		Refs: refs,
	}, nil
}

// Decode reads the payload into dst, refusing a version it does not know rather
// than misreading it.
func (p Packet) Decode(dst any) error {
	if p.Meta.Version != PacketVersion {
		return errVersion
	}
	if len(p.Data) == 0 {
		return errEmpty
	}
	return json.Unmarshal(p.Data, dst)
}

// AgeMS is how stale the payload is, from its own label.
func (p Packet) AgeMS(now time.Time) int64 {
	if p.Meta.ComputedAt == 0 {
		return -1
	}
	return now.UnixMilli() - p.Meta.ComputedAt
}

// Fresh reports whether the packet is within the topic's retention window.
func (p Packet) Fresh(now time.Time) (bool, int64, int64) {
	limit := int64(60_000)
	if t, ok := TopicByName(p.Meta.Topic); ok && t.TTLMS > 0 {
		limit = t.TTLMS
	}
	age := p.AgeMS(now)
	return age >= 0 && age <= limit, age, limit
}

var (
	errVersion = errorString("envelope: unsupported packet version")
	errEmpty   = errorString("envelope: empty payload")
)

type errorString string

func (e errorString) Error() string { return string(e) }
