package envelope

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"time"
)

// Correlation ids.
//
// ATLAS states the design in one line: minting is local, mapping is central,
// resolution is gated. This is the local half — a cid is created at the boundary
// that created the work, so work still starts when nothing central is reachable.
//
// A cid is opaque. It says "these belong together", never "this is the truth".

// MintCID creates a correlation id for a publication.
func MintCID(topic, key string, at time.Time) string {
	var b [8]byte
	if _, err := rand.Read(b[:]); err != nil {
		// A clock-derived fallback still correlates; it is only less unique.
		return fmt.Sprintf("%s-%s-%d", short(topic), key, at.UnixNano())
	}
	return fmt.Sprintf("%s-%s-%s", short(topic), key, hex.EncodeToString(b[:]))
}

func short(topic string) string {
	const max = 12
	if len(topic) <= max {
		return topic
	}
	return topic[len(topic)-max:]
}

// NewPacketWith is NewPacket without the registry lookup failure mode: the caller
// has already passed the authority interceptor.
func NewPacketWith(topic, key, producer string, data any, refs ...Ref) (Packet, error) {
	return NewPacket(topic, key, producer, data, refs...)
}

func mustJSON(v any) []byte {
	b, err := json.Marshal(v)
	if err != nil {
		return []byte(`{"error":"marshal failed"}`)
	}
	return b
}
