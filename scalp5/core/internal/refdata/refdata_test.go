// Tests for the refdata registry. All data is generated in-test (Rule A3):
// no store is read, no live symbol list is consulted.
package refdata

import (
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"testing"
)

func newTestService(t *testing.T, backend Backend) *Service {
	t.Helper()
	svc, err := NewService(backend)
	if err != nil {
		t.Fatalf("NewService: %v", err)
	}
	t.Cleanup(func() { svc.Close() })
	return svc
}

func putInstrument(t *testing.T, s *Service, id string, idents map[string]string) *Instrument {
	t.Helper()
	inst, err := s.PutInstrument(Instrument{
		ID:          id,
		Class:       ClassCryptoPerp,
		Symbol:      "GENUSDT",
		Status:      StatusActive,
		Venues:      []string{"genvenue"},
		Limits:      map[string]float64{"min_notional": 5},
		Meta:        map[string]string{"source": "generated-test"},
		Identifiers: idents,
	})
	if err != nil {
		t.Fatalf("PutInstrument(%s): %v", id, err)
	}
	return inst
}

func TestInstrumentRoundTripAndList(t *testing.T) {
	s := newTestService(t, NewMemory())
	putInstrument(t, s, "crypto_perp:genvenue:GENUSDT", map[string]string{IDTicker: "GEN"})

	got, err := s.GetInstrument("CRYPTO_PERP:GENVENUE:GENUSDT") // case-insensitive
	if err != nil {
		t.Fatalf("GetInstrument: %v", err)
	}
	if got.Class != ClassCryptoPerp || got.Status != StatusActive {
		t.Fatalf("round trip mangled: %+v", got)
	}
	if got.Limits["min_notional"] != 5 {
		t.Fatalf("limits lost: %+v", got.Limits)
	}

	list := s.List(Filter{Class: ClassCryptoPerp, Venue: "genvenue"})
	if len(list) != 1 {
		t.Fatalf("list by class+venue: got %d", len(list))
	}
	if n := len(s.List(Filter{Q: "genusdt"})); n != 1 {
		t.Fatalf("list by query: got %d", n)
	}
	if n := len(s.List(Filter{Class: ClassEquity})); n != 0 {
		t.Fatalf("class filter leaked: got %d", n)
	}
}

func TestUpsertLastWriteWinsAndKeepsCreated(t *testing.T) {
	s := newTestService(t, NewMemory())
	first := putInstrument(t, s, "crypto_perp:genvenue:GENUSDT", nil)
	second, err := s.PutInstrument(Instrument{
		ID:     first.ID,
		Class:  ClassCryptoPerp,
		Symbol: "GENUSDT",
		Status: StatusHalted,
	})
	if err != nil {
		t.Fatalf("upsert: %v", err)
	}
	_ = second
	got, _ := s.GetInstrument(first.ID)
	if got.Status != StatusHalted {
		t.Fatalf("upsert did not replace: %+v", got)
	}
	if got.CreatedMS != first.CreatedMS || got.CreatedMS == 0 {
		t.Fatalf("created_ms not preserved: first=%d got=%d", first.CreatedMS, got.CreatedMS)
	}
	if got.UpdatedMS < first.UpdatedMS {
		t.Fatalf("updated_ms did not advance")
	}
}

func TestValidation(t *testing.T) {
	s := newTestService(t, NewMemory())
	if _, err := s.PutInstrument(Instrument{}); err == nil {
		t.Fatal("empty id accepted")
	}
	if _, err := s.PutInstrument(Instrument{ID: "nonsense"}); err == nil {
		t.Fatal("missing class prefix accepted")
	}
	if _, err := s.PutInstrument(Instrument{ID: "crypto_perp:genvenue:GENUSDT"}); err == nil {
		t.Fatal("missing class accepted")
	}
	if _, err := s.AddIdentifier("crypto_perp:genvenue:MISSING", IDISIN, "X"); err == nil {
		t.Fatal("identifier on unknown instrument accepted")
	}
	if _, err := s.PutEdge(Edge{Subject: "a", Predicate: "bogus", Object: "b"}); err == nil {
		t.Fatal("unknown predicate accepted")
	}
	if _, err := s.PutDocument(Document{InstrumentID: "crypto_perp:genvenue:MISSING", Body: "x"}); err == nil {
		t.Fatal("document on unknown instrument accepted")
	}
}

func TestResolveByAnyIdentifier(t *testing.T) {
	s := newTestService(t, NewMemory())
	inst := putInstrument(t, s, "crypto_perp:genvenue:GENUSDT", nil)
	if _, err := s.AddIdentifier(inst.ID, IDISIN, "GEN000000001"); err != nil {
		t.Fatalf("AddIdentifier: %v", err)
	}
	if _, err := s.AddIdentifier(inst.ID, IDTicker, "GEN"); err != nil {
		t.Fatalf("AddIdentifier ticker: %v", err)
	}

	for _, tc := range []struct{ idType, value string }{
		{IDISIN, "GEN000000001"},
		{IDTicker, "gen"},    // case-insensitive
		{"", "GEN000000001"}, // untyped lookup
	} {
		got := s.Resolve(tc.idType, tc.value)
		if len(got) != 1 || got[0].ID != inst.ID {
			t.Fatalf("Resolve(%q,%q): got %+v", tc.idType, tc.value, got)
		}
	}
	if got := s.Resolve(IDCUSIP, "NONE"); len(got) != 0 {
		t.Fatalf(" Resolve of unknown identifier: %+v", got)
	}
}

func TestResolveTransitiveAlias(t *testing.T) {
	s := newTestService(t, NewMemory())
	a := putInstrument(t, s, "crypto_perp:genvenue:AAAUSDT", map[string]string{IDTicker: "AAA"})
	bInst, err := s.PutInstrument(Instrument{
		ID: "equity:genexchange:AAAG", Class: ClassEquity, Symbol: "AAAG", Status: StatusActive,
		Identifiers: map[string]string{IDISIN: "GEN111"},
	})
	if err != nil {
		t.Fatalf("PutInstrument b: %v", err)
	}
	// Alias chain: ISIN of b == ticker of a, registered as a raw edge.
	if _, err := s.PutEdge(Edge{
		Subject: "isin:GEN111", Predicate: EdgeSameAs, Object: "ticker:aaa",
	}); err != nil {
		t.Fatalf("PutEdge alias: %v", err)
	}
	// Identifier-only edge (no instrument side) still indexes once the
	// instrument carries the matching identifier.
	if _, err := s.AddIdentifier(bInst.ID, IDFIGI, "FIGIAAA"); err != nil {
		t.Fatalf("AddIdentifier figi: %v", err)
	}
	got := s.Resolve(IDISIN, "GEN111")
	if len(got) != 2 {
		t.Fatalf("transitive same_as should reach both instruments, got %+v", got)
	}
	if a.ID == bInst.ID {
		t.Fatal("test instruments collided")
	}
}

func TestCorrelationsAndDocuments(t *testing.T) {
	s := newTestService(t, NewMemory())
	inst := putInstrument(t, s, "crypto_perp:genvenue:GENUSDT", nil)
	if _, err := s.AddIdentifier(inst.ID, IDISIN, "GEN000000001"); err != nil {
		t.Fatalf("AddIdentifier: %v", err)
	}
	doc, err := s.PutDocument(Document{
		InstrumentID: inst.ID,
		Title:        "GEN perpetual definition",
		Kind:         "definition",
		Body:         "# GEN\n\nGenerated test definition.\n",
		Source:       "generated-test",
	})
	if err != nil {
		t.Fatalf("PutDocument: %v", err)
	}
	if doc.SHA256 == "" || doc.ID == "" {
		t.Fatalf("document not hashed/minted: %+v", doc)
	}

	docs := s.DocumentsFor(inst.ID)
	if len(docs) != 1 || docs[0].ID != doc.ID {
		t.Fatalf("DocumentsFor: %+v", docs)
	}
	if _, err := s.GetDocument(doc.ID); err != nil {
		t.Fatalf("GetDocument: %v", err)
	}

	edges := s.Correlations(inst.ID)
	predicates := map[string]bool{}
	for _, e := range edges {
		predicates[e.Predicate] = true
	}
	if !predicates[EdgeSameAs] || !predicates[EdgeHasDocument] {
		t.Fatalf("correlations missing predicates: %+v", edges)
	}
}

func TestClassLevelDocument(t *testing.T) {
	s := newTestService(t, NewMemory())
	doc, err := s.PutDocument(Document{
		Class: ClassCryptoPerp,
		Title: "crypto_perp class definition",
		Kind:  "definition",
		Body:  "Generated class definition body.\n",
	})
	if err != nil {
		t.Fatalf("PutDocument: %v", err)
	}
	if doc.InstrumentID != "" {
		t.Fatalf("class doc got instrument: %+v", doc)
	}
	if len(s.DocumentsFor("crypto_perp:genvenue:X")) != 0 {
		t.Fatal("class doc leaked into instrument docs")
	}
}

func TestCorrelationsAreCaseInsensitive(t *testing.T) {
	s := newTestService(t, NewMemory())
	inst := putInstrument(t, s, "crypto_perp:genvenue:GENUSDT", nil)
	// Mixed-case instrument endpoint, as a foreign adapter would write it.
	if _, err := s.PutEdge(Edge{
		Subject:   "crypto_perp:genvenue:GENUSDT",
		Predicate: EdgeListedOn,
		Object:    "venue:genvenue",
	}); err != nil {
		t.Fatalf("PutEdge: %v", err)
	}
	edges := s.Correlations(inst.ID)
	if len(edges) != 1 || edges[0].Subject != inst.ID {
		t.Fatalf("mixed-case edge not normalised: %+v", edges)
	}
}

func TestSearch(t *testing.T) {
	s := newTestService(t, NewMemory())
	inst := putInstrument(t, s, "crypto_perp:genvenue:GENUSDT", map[string]string{IDTicker: "GEN"})
	if _, err := s.PutDocument(Document{
		InstrumentID: inst.ID, Title: "GEN risk note", Kind: "risk", Body: "generated\n",
	}); err != nil {
		t.Fatalf("PutDocument: %v", err)
	}
	res := s.Search("gen")
	if len(res.Instruments) != 1 || len(res.Documents) != 1 {
		t.Fatalf("search: %+v", res)
	}
	if len(s.Search("").Instruments) != 0 {
		t.Fatal("empty query should return empty")
	}
}

func TestJournalPersistenceAndReplay(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "refdata.jsonl")

	s := newTestService(t, mustJournal(t, path))
	inst := putInstrument(t, s, "crypto_perp:genvenue:GENUSDT", map[string]string{IDTicker: "GEN"})
	if _, err := s.AddIdentifier(inst.ID, IDISIN, "GEN000000001"); err != nil {
		t.Fatalf("AddIdentifier: %v", err)
	}
	if _, err := s.PutDocument(Document{
		InstrumentID: inst.ID, Title: "def", Kind: "definition", Body: "generated\n",
	}); err != nil {
		t.Fatalf("PutDocument: %v", err)
	}
	if err := s.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}

	// Reopen on the same journal: everything must rebuild by replay.
	s2, err := NewService(mustJournal(t, path))
	if err != nil {
		t.Fatalf("reopen: %v", err)
	}
	defer s2.Close()
	got, err := s2.GetInstrument(inst.ID)
	if err != nil || got.Symbol != "GENUSDT" {
		t.Fatalf("replay instrument: %v %+v", err, got)
	}
	if n := len(s2.Resolve(IDISIN, "GEN000000001")); n != 1 {
		t.Fatalf("replay resolve: got %d", n)
	}
	if n := len(s2.DocumentsFor(inst.ID)); n != 1 {
		t.Fatalf("replay docs: got %d", n)
	}
	stats := s2.Stats()
	if stats.Replayed == 0 || stats.Instruments != 1 || stats.Identifiers < 2 {
		t.Fatalf("replay stats: %+v", stats)
	}
}

func TestJournalTornLineIsSkipped(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "refdata.jsonl")
	s := newTestService(t, mustJournal(t, path))
	putInstrument(t, s, "crypto_perp:genvenue:GENUSDT", nil)
	if err := s.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	appendFile(t, path, "{\"kind\":\"instrument\",\"instrument\":{\"id\":\"broken\"\n")

	s2, err := NewService(mustJournal(t, path))
	if err != nil {
		t.Fatalf("reopen with torn line: %v", err)
	}
	defer s2.Close()
	if stats := s2.Stats(); stats.Instruments != 1 {
		t.Fatalf("torn line not isolated: %+v", stats)
	}
}

func TestConcurrentAccess(t *testing.T) {
	s := newTestService(t, NewMemory())
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			ticker := fmt.Sprintf("SYM%02d", i)
			id := "crypto_perp:genvenue:" + ticker + "USDT"
			if _, err := s.PutInstrument(Instrument{
				ID:          id,
				Class:       ClassCryptoPerp,
				Symbol:      ticker + "USDT",
				Status:      StatusActive,
				Identifiers: map[string]string{IDTicker: ticker},
			}); err != nil {
				t.Errorf("PutInstrument(%s): %v", id, err)
			}
			_, _ = s.GetInstrument(id)
			_ = s.List(Filter{Class: ClassCryptoPerp})
			_ = s.Resolve(IDTicker, ticker)
			_ = s.Stats()
		}(i)
	}
	wg.Wait()
	if got := len(s.List(Filter{Class: ClassCryptoPerp})); got != 8 {
		t.Fatalf("concurrent writes lost: got %d instruments", got)
	}
}

func mustJournal(t *testing.T, path string) *Journal {
	t.Helper()
	j, err := OpenJournal(path)
	if err != nil {
		t.Fatalf("OpenJournal: %v", err)
	}
	return j
}

func appendFile(t *testing.T, path, content string) {
	t.Helper()
	fh, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		t.Fatalf("open append: %v", err)
	}
	if _, err := fh.WriteString(content); err != nil {
		t.Fatalf("write: %v", err)
	}
	if err := fh.Close(); err != nil {
		t.Fatalf("close: %v", err)
	}
}
