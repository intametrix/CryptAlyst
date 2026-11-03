package api

import (
	"net/http"
	"path/filepath"
	"strconv"

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

// recordEnvelope writes a packet to the journal, ignoring failures: recording must
// never fail the request that produced the payload.
func recordEnvelope(p envelope.Packet) {
	if len(p.Data) == 0 {
		return
	}
	j, err := envelope.OpenJournal(envelopeJournalPath())
	if err != nil {
		return
	}
	_ = j.Record(p)
	_ = j.Close()
}

var _ = strconv.Itoa
