package api

import (
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"sync"

	"pumpdump/backend/internal/envelope"
)

// The envelope surface: the registry, the recent packets, and the count per topic.
//
// These exist so the architecture is inspectable from the platform itself rather
// than only in a document — a reader can see which topics are defined, who may
// write them, and what has actually been recorded.

// envelopeJournalPath is the append-only packet log.
func envelopeJournalPath() string {
	return filepath.Join(storeRoot, "..", "envelope-journal.jsonl")
}

// handleEnvelopeTopics serves the topic registry.
func (s *Server) handleEnvelopeTopics(w http.ResponseWriter, r *http.Request) {
	ok(w, map[string]any{"topics": envelope.Topics()})
}

// handleEnvelopeRecent serves the newest recorded packets, filtered and bounded.
func (s *Server) handleEnvelopeRecent(w http.ResponseWriter, r *http.Request) {
	q := readQuery(r)
	topic := q.text("topic", "")
	key := q.symbol()
	limit := q.int("limit", 50, 1, 500)

	path := envelopeJournalPath()
	out := make([]envelope.Packet, 0, limit)
	// Read the whole journal and keep the newest N: a journal is append-only, so
	// the tail is the interesting end and the file is small by design.
	var all []envelope.Packet
	if err := envelope.Replay(path, func(p envelope.Packet) bool {
		if topic != "" && p.Meta.Topic != topic {
			return false
		}
		if key != "" && p.Meta.Key != key {
			return false
		}
		return true
	}, func(p envelope.Packet) { all = append(all, p) }); err != nil {
		ok(w, map[string]any{"packets": []envelope.Packet{}, "note": "journal not readable: " + err.Error()})
		return
	}
	if len(all) > limit {
		all = all[len(all)-limit:]
	}
	out = append(out, all...)
	ok(w, map[string]any{"packets": out, "count": len(out)})
}

// handleEnvelopeStats counts recorded packets per topic.
func (s *Server) handleEnvelopeStats(w http.ResponseWriter, r *http.Request) {
	counts, err := envelope.Count(envelopeJournalPath())
	if err != nil {
		ok(w, map[string]any{"counts": map[string]int{}, "note": "no journal yet"})
		return
	}
	total := 0
	for _, n := range counts {
		total += n
	}
	ok(w, map[string]any{"counts": counts, "total": total, "path": envelopeJournalPath()})
}

// recordEnvelope writes a packet to the journal and publishes it to the bus,
// ignoring failures: neither the record nor the broadcast may fail the request that
// produced the payload.
func recordEnvelope(p envelope.Packet) {
	if len(p.Data) == 0 {
		return
	}
	if j, err := envelope.OpenJournal(envelopeJournalPath()); err == nil {
		_ = j.Record(p)
		_ = j.Close()
	}
	publishEnvelope(p)
}

// busPublisher is the shared RabbitMQ connection, created on first use.
var (
	busOnce sync.Once
	bus     *envelope.Bus
)

func envelopeBus() *envelope.Bus {
	busOnce.Do(func() {
		addr := os.Getenv("CRYPTALYST_STOMP")
		if addr == "" {
			addr = "127.0.0.1:61613"
		}
		login := os.Getenv("CRYPTALYST_STOMP_LOGIN")
		if login == "" {
			login = "cryptalyst"
		}
		pass := os.Getenv("CRYPTALYST_STOMP_PASSCODE")
		if pass == "" {
			pass = "cryptalyst"
		}
		bus = envelope.NewBus(addr, login, pass)
	})
	return bus
}

// publishEnvelope broadcasts a packet, tolerating a broker that is down.
func publishEnvelope(p envelope.Packet) {
	_ = envelopeBus().Publish(p)
}

var _ = strconv.Itoa
