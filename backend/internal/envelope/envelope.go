// Package envelope defines the wire contract between the Go backend and the GPU
// agent. A packet is an Envelope: a versioned, self-describing container with a
// kind, an opaque payload, and provenance. The version lets the backend and the
// agent evolve independently — an agent rejects a version it does not know
// rather than silently misreading the payload.
//
// This is the same shape as the ATLAS envelope (data + meta + references + a
// version): the payload is the data, the header is the meta, a HistoryRef is
// the reference, and Version is the version.
package envelope

import (
	"encoding/json"
	"fmt"
	"time"
)

// Version is the current contract version. Bump it on a breaking change.
const Version = 1

// Kind discriminates the payload of an envelope.
type Kind string

// Kinds.
const (
	// KindJob is a sweep request: graph + grid + history.
	KindJob Kind = "job"
	// KindAck is an acknowledgement that a job was accepted and queued.
	KindAck Kind = "ack"
	// KindResult is a sweep response: per-combo metrics.
	KindResult Kind = "result"
	// KindError is a terminal error for a job.
	KindError Kind = "error"
)

// Envelope is the outer packet. Everything the backend and the agent exchange
// travels in one, so a single code path handles jobs, results, and errors.
type Envelope struct {
	Version    int               `json:"version"`
	ID         string            `json:"id"` // job id; echoed back in results
	Kind       Kind              `json:"kind"`
	SentAtMS   int64             `json:"sent_at_ms"`
	Payload    json.RawMessage   `json:"payload"`
	Provenance map[string]string `json:"provenance,omitempty"`
}

// New builds an envelope around a payload, marshalling it into the opaque
// Payload field.
func New(id string, kind Kind, payload any) (Envelope, error) {
	b, err := json.Marshal(payload)
	if err != nil {
		return Envelope{}, fmt.Errorf("envelope: marshal payload: %w", err)
	}
	return Envelope{
		Version:  Version,
		ID:       id,
		Kind:     kind,
		SentAtMS: time.Now().UnixMilli(),
		Payload:  b,
	}, nil
}

// Decode unmarshals the payload into dst.
func (e Envelope) Decode(dst any) error {
	if e.Version != Version {
		return fmt.Errorf("envelope: unsupported version %d (want %d)", e.Version, Version)
	}
	if len(e.Payload) == 0 {
		return fmt.Errorf("envelope: empty payload")
	}
	return json.Unmarshal(e.Payload, dst)
}

// Marshal serialises the whole envelope.
func (e Envelope) Marshal() ([]byte, error) {
	return json.Marshal(e)
}

// Unmarshal parses a whole envelope.
func Unmarshal(b []byte) (Envelope, error) {
	var e Envelope
	err := json.Unmarshal(b, &e)
	return e, err
}
