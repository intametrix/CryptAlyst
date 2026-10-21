// Package correlation is the map that answers "what happened, and why".
//
// Minting is local (a cid is created at the boundary that created work),
// mapping is central (this package), and resolution is gated (the caller must
// pass an actor, and every read is recorded as a step on the document).
package correlation

import (
	"crypto/rand"
	"encoding/hex"
	"sort"
	"sync"
	"time"
)

// Step is one append on a document's ledger.
type Step struct {
	Kind   string    `json:"kind"` // mint | ref | decision | fill | read | note
	Actor  string    `json:"actor"`
	Detail string    `json:"detail,omitempty"`
	TS     time.Time `json:"ts"`
}

// Doc is the correlation map's row for one chain of work.
type Doc struct {
	ID        string    `json:"id"`
	Topic     string    `json:"topic"`
	Key       string    `json:"key"`
	CIDs      []string  `json:"cids"`
	Refs      []string  `json:"refs"`
	Steps     []Step    `json:"steps"`
	State     string    `json:"state"`
	FirstSeen time.Time `json:"first_seen"`
	LastSeen  time.Time `json:"last_seen"`
}

// Service owns the map. It is a projection of durable records, never the source
// of truth: everything here can be rebuilt by replaying the journal.
type Service struct {
	mu    sync.RWMutex
	docs  map[string]*Doc
	byCID map[string]string
	steps int64
	reads int64
}

func New() *Service {
	return &Service{docs: map[string]*Doc{}, byCID: map[string]string{}}
}

// Mint creates identity at the boundary. Local, no dependency on this service
// being up in the caller's path.
func Mint() string {
	b := make([]byte, 8)
	_, _ = rand.Read(b)
	return "cid-" + hex.EncodeToString(b)
}

// Record appends a step for a document, creating it on first sight.
func (s *Service) Record(docID, cid, kind, actor, detail string) *Doc {
	s.mu.Lock()
	defer s.mu.Unlock()
	now := time.Now().UTC()
	d := s.docs[docID]
	if d == nil {
		d = &Doc{ID: docID, State: "open", FirstSeen: now}
		s.docs[docID] = d
	}
	if cid != "" {
		d.CIDs = append(d.CIDs, cid)
		s.byCID[cid] = docID
	}
	d.Steps = append(d.Steps, Step{Kind: kind, Actor: actor, Detail: detail, TS: now})
	d.LastSeen = now
	s.steps++
	return d
}

// Link records that docID was derived from another document: the reference that
// makes "why did we buy" answerable.
func (s *Service) Link(docID, parentID string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	d := s.docs[docID]
	if d == nil {
		now := time.Now().UTC()
		d = &Doc{ID: docID, State: "open", FirstSeen: now}
		s.docs[docID] = d
	}
	d.Refs = append(d.Refs, parentID)
}

// Resolve answers what happened, gated: the reader is recorded as a step on the
// document being read, so reading the trace becomes part of the trace.
func (s *Service) Resolve(docID, actor string) (Doc, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	d, ok := s.docs[docID]
	if !ok {
		return Doc{}, false
	}
	d.Steps = append(d.Steps, Step{Kind: "read", Actor: actor, TS: time.Now().UTC()})
	d.LastSeen = time.Now().UTC()
	s.reads++
	out := *d
	out.Steps = append([]Step(nil), d.Steps...)
	out.Refs = append([]string(nil), d.Refs...)
	return out, true
}

// ByCID finds the document a correlation id belongs to.
func (s *Service) ByCID(cid string) (string, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	id, ok := s.byCID[cid]
	return id, ok
}

// Trace walks a document's ancestry, which is the chain a reviewer reads.
func (s *Service) Trace(docID string) []Doc {
	s.mu.RLock()
	defer s.mu.RUnlock()
	var out []Doc
	seen := map[string]bool{}
	var walk func(string)
	walk = func(id string) {
		if seen[id] {
			return
		}
		seen[id] = true
		d, ok := s.docs[id]
		if !ok {
			return
		}
		out = append(out, *d)
		for _, parent := range d.Refs {
			walk(parent)
		}
	}
	walk(docID)
	// the walk collects the leaf first and its ancestors after it; a reviewer
	// reads the other way round, so reverse into root-first order
	for i, j := 0, len(out)-1; i < j; i, j = i+1, j-1 {
		out[i], out[j] = out[j], out[i]
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].FirstSeen.Before(out[j].FirstSeen) })
	return out
}

func (s *Service) Stats() (docs int, steps, reads int64) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return len(s.docs), s.steps, s.reads
}
