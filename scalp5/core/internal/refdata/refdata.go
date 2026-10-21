// Package refdata is the reference-data registry the lake and its consumers
// call to answer "what IS this thing".
//
// The lake carries envelopes about market data; the correlation service maps
// chains of work ("what happened, and why"). Neither knows what a BTCUSDT is.
// This package owns instrument identity: canonical instruments with class,
// venues, limits and meta; the many external identifiers each instrument is
// known by (ticker, ISIN, CUSIP, FIGI, exchange symbol, an internal id from
// any book); documents (definitions, specs, risk notes); and the correlation
// edges that link them. Any consumer holding any one identifier can resolve
// the canonical instrument and everything registered against it.
//
// Storage is a swap point in the lake style: one Backend interface, a JSONL
// journal for the durable path (replay on open, last write wins), a memory
// backend for the hot path, and room for SQLite, RocksDB, vector or document
// adapters without a caller learning which one answered. The lake decides
// placement; callers see only the Service.
package refdata

import (
	"bufio"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

// Class is the instrument type. The registry is not crypto-only: equities,
// FX and commodities get their own classes so a future equity adapter needs
// no schema change.
type Class string

const (
	ClassCryptoSpot Class = "crypto_spot"
	ClassCryptoPerp Class = "crypto_perp"
	ClassCryptoFut  Class = "crypto_future"
	ClassEquity     Class = "equity"
	ClassFX         Class = "fx"
	ClassCommodity  Class = "commodity"
	ClassIndex      Class = "index"
	ClassOther      Class = "other"
)

// Instrument lifecycle states.
const (
	StatusActive   = "active"
	StatusTesting  = "testing"
	StatusHalted   = "halted"
	StatusDelisted = "delisted"
)

// Well-known identifier types. The registry accepts any string; these are
// the spellings the seed and docs use so adapters agree.
const (
	IDTicker         = "ticker"
	IDISIN           = "isin"
	IDCUSIP          = "cusip"
	IDFIGI           = "figi"
	IDExchangeSymbol = "exchange_symbol"
	IDInternal       = "internal" // any book's private symbol
)

// Edge predicates. same_as links two identifiers or an identifier and a
// canonical instrument; the rest link an instrument to venues, documents
// and external records.
const (
	EdgeSameAs      = "same_as"
	EdgeListedOn    = "listed_on"
	EdgeHasDocument = "has_document"
	EdgeIssuedBy    = "issued_by"
	EdgeTracks      = "tracks"
	EdgeDerivedFrom = "derived_from"
	maxResolveHops  = 8 // bound on transitive same_as traversal
)

// Instrument is the canonical record for one tradable thing.
type Instrument struct {
	ID          string             `json:"id"` // canonical: "<class>:<venue>:<symbol>", lowercase
	Class       Class              `json:"class"`
	Symbol      string             `json:"symbol"`
	Name        string             `json:"name,omitempty"`
	Status      string             `json:"status"`
	Venues      []string           `json:"venues,omitempty"`
	Limits      map[string]float64 `json:"limits,omitempty"` // tick_size, lot_size, max_leverage, max_position, min_notional ...
	Meta        map[string]string  `json:"meta,omitempty"`
	Identifiers map[string]string  `json:"identifiers,omitempty"` // type -> value, denormalised view of same_as edges
	CreatedMS   int64              `json:"created_ms"`
	UpdatedMS   int64              `json:"updated_ms"`
}

// Document is an attached blob: class definitions, venue specs, risk notes.
// Body is stored verbatim so the registry reads like documentation, not just
// a lookup table.
type Document struct {
	ID           string `json:"id"`
	InstrumentID string `json:"instrument_id"` // empty for class-level docs
	Class        Class  `json:"class,omitempty"`
	Title        string `json:"title"`
	Kind         string `json:"kind"` // definition | spec | risk | limits | api | note
	ContentType  string `json:"content_type,omitempty"`
	Body         string `json:"body"`
	SHA256       string `json:"sha256"`
	Source       string `json:"source,omitempty"` // provenance: who wrote it and from what
	CreatedMS    int64  `json:"created_ms"`
	UpdatedMS    int64  `json:"updated_ms"`
}

// Edge is one correlation triple: subject -predicate-> object. Subjects and
// objects are either canonical instrument/document ids or external
// identifiers in "type:value" form (e.g. "isin:US0378331005"). This is the
// join that lets a record keyed by ISIN in one store and by ticker in
// another resolve to the same instrument.
type Edge struct {
	Subject   string            `json:"subject"`
	Predicate string            `json:"predicate"`
	Object    string            `json:"object"`
	Meta      map[string]string `json:"meta,omitempty"`
	CreatedMS int64             `json:"created_ms"`
}

// Entry is one journal record. Kind selects the payload; the journal is a
// single append-only stream so replay order is total.
type Entry struct {
	Kind       string      `json:"kind"` // instrument | document | edge
	TS         int64       `json:"ts"`
	Instrument *Instrument `json:"instrument,omitempty"`
	Document   *Document   `json:"document,omitempty"`
	Edge       *Edge       `json:"edge,omitempty"`
}

// Backend is the swap point, in the lake's style: records in, replay out,
// nothing else. A SQLite/RocksDB/vector/document adapter implements exactly
// this.
type Backend interface {
	Append(Entry) error
	Replay(func(Entry)) error // calls fn for every entry in append order
	Name() string
	Close() error
}

// Memory is the hot-path backend: records kept in a ring per nothing — the
// durable authority is the journal; memory exists for tests and ephemeral
// deployments.
type Memory struct {
	mu      sync.Mutex
	entries []Entry
	closed  bool
}

func NewMemory() *Memory { return &Memory{} }

func (m *Memory) Name() string { return "memory" }

func (m *Memory) Append(e Entry) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.closed {
		return errors.New("refdata: memory backend closed")
	}
	m.entries = append(m.entries, e)
	return nil
}

func (m *Memory) Replay(fn func(Entry)) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	for _, e := range m.entries {
		fn(e)
	}
	return nil
}

func (m *Memory) Close() error {
	m.mu.Lock()
	m.closed = true
	m.mu.Unlock()
	return nil
}

// Journal is the durable backend: append-only JSONL, replayed on open.
// Last write wins is the merge rule — an upsert appends a fresh record and
// the newest entry for an id replaces older ones during replay.
type Journal struct {
	mu   sync.Mutex
	path string
	fh   *os.File
	w    *bufio.Writer
	n    int64
}

// OpenJournal creates or opens the journal at path, creating parent dirs.
// The file is opened append-only; replay happens before writes in NewService.
func OpenJournal(path string) (*Journal, error) {
	if path == "" {
		return nil, errors.New("refdata: journal path is empty")
	}
	if dir := filepath.Dir(path); dir != "" {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return nil, fmt.Errorf("refdata: mkdir: %w", err)
		}
	}
	fh, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return nil, fmt.Errorf("refdata: open journal: %w", err)
	}
	return &Journal{path: path, fh: fh, w: bufio.NewWriter(fh)}, nil
}

func (j *Journal) Name() string { return "jsonl:" + j.path }

func (j *Journal) Append(e Entry) error {
	j.mu.Lock()
	defer j.mu.Unlock()
	raw, err := json.Marshal(e)
	if err != nil {
		return err
	}
	if _, err := j.w.Write(append(raw, '\n')); err != nil {
		return err
	}
	if err := j.w.Flush(); err != nil {
		return err
	}
	j.n++
	return nil
}

// Replay streams the journal in append order. A torn final line (crash
// mid-write) is dropped, not fatal: the durable record is everything before
// the tear.
func (j *Journal) Replay(fn func(Entry)) error {
	j.mu.Lock()
	defer j.mu.Unlock()
	fh, err := os.Open(j.path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return err
	}
	defer fh.Close()
	sc := bufio.NewScanner(fh)
	sc.Buffer(make([]byte, 0, 1<<20), 1<<24)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		var e Entry
		if err := json.Unmarshal([]byte(line), &e); err != nil {
			continue // torn or foreign line: skip, never abort replay
		}
		fn(e)
	}
	return nil
}

func (j *Journal) Close() error {
	j.mu.Lock()
	defer j.mu.Unlock()
	if err := j.w.Flush(); err != nil {
		return err
	}
	return j.fh.Close()
}

// Service is the registry. It is a projection of the journal, like the
// correlation service: everything in memory can be rebuilt by replay.
type Service struct {
	mu          sync.RWMutex
	backend     Backend
	instruments map[string]*Instrument
	documents   map[string]*Document
	edges       []Edge
	byIdent     map[string]map[string]struct{} // "type:value" -> instrument ids
	identsFor   map[string]map[string]struct{} // instrument id -> its "type:value" keys
	docsFor     map[string]map[string]struct{} // instrument id -> document ids
	written     int64
	replayed    int64
}

// NewService replays backend into memory and returns the live registry.
func NewService(backend Backend) (*Service, error) {
	s := &Service{
		backend:     backend,
		instruments: map[string]*Instrument{},
		documents:   map[string]*Document{},
		byIdent:     map[string]map[string]struct{}{},
		identsFor:   map[string]map[string]struct{}{},
		docsFor:     map[string]map[string]struct{}{},
	}
	err := backend.Replay(func(e Entry) { s.apply(e); s.replayed++ })
	if err != nil {
		return nil, fmt.Errorf("refdata: replay %s: %w", backend.Name(), err)
	}
	return s, nil
}

func nowMS() int64 { return time.Now().UTC().UnixMilli() }

func (s *Service) append(e Entry) error {
	e.TS = nowMS()
	if err := s.backend.Append(e); err != nil {
		return err
	}
	s.mu.Lock()
	s.apply(e)
	s.written++
	s.mu.Unlock()
	return nil
}

// apply merges one entry into the indexes. Caller holds or does not need
// the lock (replay path is single-threaded by construction).
func (s *Service) apply(e Entry) {
	switch e.Kind {
	case "instrument":
		if e.Instrument == nil {
			return
		}
		inst := *e.Instrument
		s.reindexInstrumentLocked(&inst)
		s.instruments[inst.ID] = &inst
	case "document":
		if e.Document == nil {
			return
		}
		doc := *e.Document
		s.documents[doc.ID] = &doc
		if doc.InstrumentID != "" {
			if s.docsFor[doc.InstrumentID] == nil {
				s.docsFor[doc.InstrumentID] = map[string]struct{}{}
			}
			s.docsFor[doc.InstrumentID][doc.ID] = struct{}{}
		}
	case "edge":
		if e.Edge == nil {
			return
		}
		edge := *e.Edge
		// Normalise endpoints at ingestion: identifier and instrument-id
		// endpoints are case-insensitive in this registry (the byIdent index
		// and canonical instrument ids are lowercase), so mixed-case writers
		// and replay must converge. Instrument ids are detected by their
		// class prefix; identifier endpoints carry a "type:value" shape.
		if isInstrumentKey(edge.Subject) || edge.Predicate == EdgeSameAs {
			edge.Subject = strings.ToLower(edge.Subject)
		}
		if isInstrumentKey(edge.Object) || edge.Predicate == EdgeSameAs {
			edge.Object = strings.ToLower(edge.Object)
		}
		// An edge triple is a fact, not an event: re-recording it updates
		// the existing entry instead of appending a duplicate. This is what
		// makes seed application and adapter re-registration idempotent.
		replaced := false
		for i := range s.edges {
			if s.edges[i].Subject == edge.Subject &&
				s.edges[i].Predicate == edge.Predicate &&
				s.edges[i].Object == edge.Object {
				s.edges[i] = edge
				replaced = true
				break
			}
		}
		if !replaced {
			s.edges = append(s.edges, edge)
		}
		if edge.Predicate == EdgeSameAs {
			if instID, idType, val, ok := sameAsInstrumentIdent(&edge); ok {
				s.addIdentLocked(idType, val, instID)
			}
		}
		if edge.Predicate == EdgeHasDocument {
			if s.docsFor[edge.Subject] == nil {
				s.docsFor[edge.Subject] = map[string]struct{}{}
			}
			s.docsFor[edge.Subject][edge.Object] = struct{}{}
		}
	}
}

// reindexInstrumentLocked replaces the identifier index entries for one
// instrument: old keys are detached, current Identifiers re-registered.
func (s *Service) reindexInstrumentLocked(inst *Instrument) {
	for key := range s.identsFor[inst.ID] {
		if ids := s.byIdent[key]; ids != nil {
			delete(ids, inst.ID)
			if len(ids) == 0 {
				delete(s.byIdent, key)
			}
		}
	}
	delete(s.identsFor, inst.ID)
	for idType, val := range inst.Identifiers {
		s.addIdentLocked(idType, val, inst.ID)
	}
}

func (s *Service) addIdentLocked(idType, val, instID string) {
	key := idType + ":" + strings.ToLower(val)
	if s.byIdent[key] == nil {
		s.byIdent[key] = map[string]struct{}{}
	}
	s.byIdent[key][instID] = struct{}{}
	if s.identsFor[instID] == nil {
		s.identsFor[instID] = map[string]struct{}{}
	}
	s.identsFor[instID][key] = struct{}{}
}

// sameAsInstrumentIdent extracts an identifier registration from a same_as
// edge when exactly one side is a canonical instrument id and the other is
// an external identifier in "type:value" form. Edges written directly (by
// adapters that speak only edge triples) index the same way as identifiers
// registered through AddIdentifier.
func sameAsInstrumentIdent(e *Edge) (instID, idType, val string, ok bool) {
	leftClass := isInstrumentKey(e.Subject)
	rightClass := isInstrumentKey(e.Object)
	switch {
	case leftClass && !rightClass:
		i := strings.Index(e.Object, ":")
		if i <= 0 {
			return "", "", "", false
		}
		return e.Subject, e.Object[:i], e.Object[i+1:], true
	case rightClass && !leftClass:
		i := strings.Index(e.Subject, ":")
		if i <= 0 {
			return "", "", "", false
		}
		return e.Object, e.Subject[:i], e.Subject[i+1:], true
	}
	return "", "", "", false
}

func isInstrumentKey(s string) bool {
	_, ok := instrumentKeyClass(s)
	return ok
}

func instrumentKeyClass(id string) (Class, bool) {
	i := strings.Index(id, ":")
	if i <= 0 {
		return "", false
	}
	c := Class(id[:i])
	switch c {
	case ClassCryptoSpot, ClassCryptoPerp, ClassCryptoFut, ClassEquity,
		ClassFX, ClassCommodity, ClassIndex, ClassOther:
		return c, true
	}
	return "", false
}

// PutInstrument upserts an instrument. ID is required and canonicalised to
// lowercase; Class and Status get defaults when empty; CreatedMS is kept
// from the first record, UpdatedMS is stamped now. Registered identifiers
// are indexed for resolution.
func (s *Service) PutInstrument(inst Instrument) (*Instrument, error) {
	if inst.ID == "" {
		return nil, errors.New("refdata: instrument id is required")
	}
	inst.ID = strings.ToLower(inst.ID)
	if _, ok := instrumentKeyClass(inst.ID); !ok {
		return nil, fmt.Errorf("refdata: id %q must start with a class prefix, e.g. crypto_perp:binance:BTCUSDT", inst.ID)
	}
	if inst.Status == "" {
		inst.Status = StatusActive
	}
	s.mu.RLock()
	prev := s.instruments[inst.ID]
	s.mu.RUnlock()
	now := nowMS()
	if prev != nil {
		if inst.CreatedMS == 0 {
			inst.CreatedMS = prev.CreatedMS
		}
		if inst.Class == "" {
			inst.Class = prev.Class
		}
		if inst.Symbol == "" {
			inst.Symbol = prev.Symbol
		}
	} else if inst.CreatedMS == 0 {
		inst.CreatedMS = now
	}
	if inst.Class == "" {
		return nil, fmt.Errorf("refdata: instrument %s class is required", inst.ID)
	}
	inst.UpdatedMS = now
	if err := s.append(Entry{Kind: "instrument", Instrument: &inst}); err != nil {
		return nil, err
	}
	out := inst
	return &out, nil
}

// GetInstrument returns one canonical instrument by id.
func (s *Service) GetInstrument(id string) (*Instrument, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	inst := s.instruments[strings.ToLower(id)]
	if inst == nil {
		return nil, fmt.Errorf("refdata: instrument %q not found", id)
	}
	out := *inst
	return &out, nil
}

// Filter selects instruments for List.
type Filter struct {
	Class  Class
	Venue  string
	Status string
	Q      string // case-insensitive substring over id, symbol, name
}

// List returns instruments matching f, sorted by id for a stable answer.
func (s *Service) List(f Filter) []Instrument {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := []Instrument{}
	q := strings.ToLower(f.Q)
	for id, inst := range s.instruments {
		if f.Class != "" && inst.Class != f.Class {
			continue
		}
		if f.Status != "" && inst.Status != f.Status {
			continue
		}
		if f.Venue != "" && !hasString(inst.Venues, f.Venue) {
			continue
		}
		if q != "" && !strings.Contains(id, q) &&
			!strings.Contains(strings.ToLower(inst.Symbol), q) &&
			!strings.Contains(strings.ToLower(inst.Name), q) {
			continue
		}
		out = append(out, *inst)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

func hasString(xs []string, v string) bool {
	for _, x := range xs {
		if strings.EqualFold(x, v) {
			return true
		}
	}
	return false
}

// AddIdentifier registers idType:value for an instrument, recording the
// same_as edge that links the external identifier to the canonical id.
func (s *Service) AddIdentifier(instID, idType, value string) (*Edge, error) {
	if idType == "" || value == "" {
		return nil, errors.New("refdata: identifier type and value are required")
	}
	instID = strings.ToLower(instID)
	if _, err := s.GetInstrument(instID); err != nil {
		return nil, err
	}
	edge := &Edge{
		Subject:   instID,
		Predicate: EdgeSameAs,
		Object:    idType + ":" + strings.ToLower(value),
		CreatedMS: nowMS(),
	}
	if err := s.append(Entry{Kind: "edge", Edge: edge}); err != nil {
		return nil, err
	}
	// Keep the denormalised view on the instrument in step.
	s.mu.RLock()
	inst := s.instruments[instID]
	s.mu.RUnlock()
	if inst != nil {
		merged := *inst
		if merged.Identifiers == nil {
			merged.Identifiers = map[string]string{}
		}
		merged.Identifiers[idType] = value
		merged.UpdatedMS = nowMS()
		if err := s.append(Entry{Kind: "instrument", Instrument: &merged}); err != nil {
			return nil, err
		}
	}
	return edge, nil
}

// Resolve maps any identifier to its canonical instruments. idType may be
// empty, meaning "match the value across all identifier types". Transitive
// same_as chains are followed up to maxResolveHops.
func (s *Service) Resolve(idType, value string) []Instrument {
	s.mu.RLock()
	defer s.mu.RUnlock()
	seeds := map[string]struct{}{}
	if idType != "" {
		if ids, ok := s.byIdent[idType+":"+strings.ToLower(value)]; ok {
			for id := range ids {
				seeds[id] = struct{}{}
			}
		}
	} else {
		v := strings.ToLower(value)
		for key, ids := range s.byIdent {
			if strings.HasSuffix(key, ":"+v) {
				for id := range ids {
					seeds[id] = struct{}{}
				}
			}
		}
	}
	// Follow same_as edges transitively to catch alias-of-alias chains.
	// Frontier nodes are instrument ids and "type:value" identifier keys;
	// edges connect the two kinds, and identifier keys map back to
	// instruments through byIdent.
	hits := map[string]struct{}{}
	frontier := map[string]struct{}{}
	for id := range seeds {
		frontier[id] = struct{}{}
		for key := range s.identsFor[id] {
			frontier[key] = struct{}{}
		}
	}
	seen := map[string]struct{}{}
	for hops := 0; hops < maxResolveHops && len(frontier) > 0; hops++ {
		next := map[string]struct{}{}
		for node := range frontier {
			if _, dup := seen[node]; dup {
				continue
			}
			seen[node] = struct{}{}
			if _, isInst := s.instruments[node]; isInst {
				hits[node] = struct{}{}
			}
			for id := range s.byIdent[node] {
				hits[id] = struct{}{}
			}
			for _, e := range s.edges {
				if e.Predicate != EdgeSameAs {
					continue
				}
				if e.Subject == node {
					next[e.Object] = struct{}{}
				}
				if e.Object == node {
					next[e.Subject] = struct{}{}
				}
			}
		}
		frontier = next
	}
	out := []Instrument{}
	for id := range hits {
		if inst := s.instruments[id]; inst != nil {
			out = append(out, *inst)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

// Correlations returns every edge touching the instrument: identifiers,
// venues, documents, and links other systems registered. This is the
// "link disparate data" answer: one call turns a canonical id into the full
// set of handles the outside world uses for it.
func (s *Service) Correlations(instID string) []Edge {
	s.mu.RLock()
	defer s.mu.RUnlock()
	instID = strings.ToLower(instID)
	ident := "id:" + instID
	out := []Edge{}
	for _, e := range s.edges {
		if e.Subject == instID || e.Subject == ident ||
			e.Object == instID || e.Object == ident {
			out = append(out, e)
		}
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Predicate != out[j].Predicate {
			return out[i].Predicate < out[j].Predicate
		}
		if out[i].Subject != out[j].Subject {
			return out[i].Subject < out[j].Subject
		}
		return out[i].Object < out[j].Object
	})
	return out
}

// PutDocument upserts a document, hashing the body. If doc.ID is empty one
// is minted as doc-<sha256[:12]>. A has_document edge is recorded when the
// document names an instrument.
func (s *Service) PutDocument(doc Document) (*Document, error) {
	doc.SHA256 = sha256Hex(doc.Body)
	if doc.ID == "" {
		doc.ID = "doc-" + doc.SHA256[:12]
	}
	if doc.Kind == "" {
		doc.Kind = "note"
	}
	if doc.ContentType == "" {
		doc.ContentType = "text/markdown"
	}
	if doc.InstrumentID != "" {
		doc.InstrumentID = strings.ToLower(doc.InstrumentID)
		if _, err := s.GetInstrument(doc.InstrumentID); err != nil {
			return nil, err
		}
	}
	s.mu.RLock()
	prev := s.documents[doc.ID]
	s.mu.RUnlock()
	now := nowMS()
	if prev != nil && doc.CreatedMS == 0 {
		doc.CreatedMS = prev.CreatedMS
	} else if doc.CreatedMS == 0 {
		doc.CreatedMS = now
	}
	doc.UpdatedMS = now
	if err := s.append(Entry{Kind: "document", Document: &doc}); err != nil {
		return nil, err
	}
	if doc.InstrumentID != "" {
		edge := &Edge{
			Subject:   doc.InstrumentID,
			Predicate: EdgeHasDocument,
			Object:    doc.ID,
			CreatedMS: now,
		}
		if err := s.append(Entry{Kind: "edge", Edge: edge}); err != nil {
			return nil, err
		}
	}
	out := doc
	return &out, nil
}

// GetDocument returns one document by id.
func (s *Service) GetDocument(id string) (*Document, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	doc := s.documents[id]
	if doc == nil {
		return nil, fmt.Errorf("refdata: document %q not found", id)
	}
	out := *doc
	return &out, nil
}

// DocumentsFor returns documents attached to an instrument, sorted by id.
func (s *Service) DocumentsFor(instID string) []Document {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := []Document{}
	for docID := range s.docsFor[strings.ToLower(instID)] {
		if doc := s.documents[docID]; doc != nil {
			out = append(out, *doc)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

// DocFilter selects documents for ListDocuments.
type DocFilter struct {
	InstrumentID string
	Class        Class
	Kind         string
	Q            string // case-insensitive substring over id, title
}

// ListDocuments returns documents matching f, sorted by id.
func (s *Service) ListDocuments(f DocFilter) []Document {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := []Document{}
	q := strings.ToLower(f.Q)
	for _, doc := range s.documents {
		if f.InstrumentID != "" && doc.InstrumentID != strings.ToLower(f.InstrumentID) {
			continue
		}
		if f.Class != "" && doc.Class != f.Class {
			continue
		}
		if f.Kind != "" && doc.Kind != f.Kind {
			continue
		}
		if q != "" && !strings.Contains(strings.ToLower(doc.ID), q) &&
			!strings.Contains(strings.ToLower(doc.Title), q) {
			continue
		}
		out = append(out, *doc)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

// PutEdge records one correlation triple, validated.
func (s *Service) PutEdge(e Edge) (*Edge, error) {
	if e.Subject == "" || e.Predicate == "" || e.Object == "" {
		return nil, errors.New("refdata: edge subject, predicate and object are required")
	}
	switch e.Predicate {
	case EdgeSameAs, EdgeListedOn, EdgeHasDocument, EdgeIssuedBy, EdgeTracks, EdgeDerivedFrom:
	default:
		return nil, fmt.Errorf("refdata: unknown predicate %q", e.Predicate)
	}
	if e.Predicate == EdgeSameAs {
		e.Subject = strings.ToLower(e.Subject)
		e.Object = strings.ToLower(e.Object)
	} else {
		if isInstrumentKey(e.Subject) {
			e.Subject = strings.ToLower(e.Subject)
		}
		if isInstrumentKey(e.Object) {
			e.Object = strings.ToLower(e.Object)
		}
	}
	e.CreatedMS = nowMS()
	if err := s.append(Entry{Kind: "edge", Edge: &e}); err != nil {
		return nil, err
	}
	out := e
	return &out, nil
}

// Edges lists edges filtered by subject/predicate/object; empty filters match all.
func (s *Service) Edges(subject, predicate, object string) []Edge {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := []Edge{}
	for _, e := range s.edges {
		if subject != "" && e.Subject != subject &&
			!(e.Predicate == EdgeSameAs && e.Object == subject) {
			continue
		}
		if predicate != "" && e.Predicate != predicate {
			continue
		}
		if object != "" && e.Object != object &&
			!(e.Predicate == EdgeSameAs && e.Subject == object) {
			continue
		}
		out = append(out, e)
	}
	return out
}

// SearchResult is one hit across the registry.
type SearchResult struct {
	Instruments []Instrument `json:"instruments"`
	Documents   []Document   `json:"documents"`
	Edges       []Edge       `json:"edges"`
}

// Search matches a query string against instrument ids/symbols/names,
// document titles, and identifier values.
func (s *Service) Search(q string) SearchResult {
	q = strings.ToLower(strings.TrimSpace(q))
	res := SearchResult{Instruments: []Instrument{}, Documents: []Document{}, Edges: []Edge{}}
	if q == "" {
		return res
	}
	res.Instruments = s.List(Filter{Q: q})
	seen := map[string]struct{}{}
	for _, inst := range res.Instruments {
		seen[inst.ID] = struct{}{}
	}
	for _, inst := range s.Resolve("", q) {
		if _, dup := seen[inst.ID]; !dup {
			seen[inst.ID] = struct{}{}
			res.Instruments = append(res.Instruments, inst)
		}
	}
	s.mu.RLock()
	for _, doc := range s.documents {
		if strings.Contains(strings.ToLower(doc.Title), q) {
			res.Documents = append(res.Documents, *doc)
		}
	}
	for _, e := range s.edges {
		if strings.Contains(strings.ToLower(e.Subject), q) ||
			strings.Contains(strings.ToLower(e.Object), q) {
			res.Edges = append(res.Edges, e)
		}
	}
	s.mu.RUnlock()
	sort.Slice(res.Documents, func(i, j int) bool { return res.Documents[i].ID < res.Documents[j].ID })
	sort.Slice(res.Edges, func(i, j int) bool {
		if res.Edges[i].Subject != res.Edges[j].Subject {
			return res.Edges[i].Subject < res.Edges[j].Subject
		}
		return res.Edges[i].Object < res.Edges[j].Object
	})
	return res
}

// Stats describes the registry and its storage.
type Stats struct {
	Backend     string `json:"backend"`
	Instruments int    `json:"instruments"`
	Documents   int    `json:"documents"`
	Edges       int    `json:"edges"`
	Identifiers int    `json:"identifiers"`
	EntriesNew  int64  `json:"entries_written"`
	Replayed    int64  `json:"entries_replayed"`
}

// Stats snapshots the registry.
func (s *Service) Stats() Stats {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return Stats{
		Backend:     s.backend.Name(),
		Instruments: len(s.instruments),
		Documents:   len(s.documents),
		Edges:       len(s.edges),
		Identifiers: len(s.byIdent),
		EntriesNew:  s.written,
		Replayed:    s.replayed,
	}
}

// Close flushes and closes the backend.
func (s *Service) Close() error { return s.backend.Close() }

// ErrNotFound is returned for missing instruments/documents.
var ErrNotFound = errors.New("refdata: not found")

func sha256Hex(body string) string {
	sum := sha256.Sum256([]byte(body))
	return fmt.Sprintf("%x", sum)
}

// Compile-time guard: Backend is satisfied by both shipped backends.
var (
	_ Backend   = (*Memory)(nil)
	_ Backend   = (*Journal)(nil)
	_ io.Closer = (*Service)(nil)
)
