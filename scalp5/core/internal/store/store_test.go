package store

import (
	"encoding/json"
	"path/filepath"
	"testing"
)

// every store must answer the same four questions, so one test drives them all
func fixture() []Envelope {
	body := func(sym string) json.RawMessage {
		b, _ := json.Marshal(map[string]any{"symbol": sym, "net_pct": 0.05})
		return b
	}
	return []Envelope{
		{ID: "a1", Topic: "market.price", Key: "BTCUSDT", Plane: "market", Producer: "market_data", TS: 1000, Data: body("BTCUSDT")},
		{ID: "a2", Topic: "market.price", Key: "BTCUSDT", Plane: "market", Producer: "market_data", TS: 2000, Data: body("BTCUSDT")},
		{ID: "a3", Topic: "model.fill", Key: "ETHUSDT", Plane: "model", Producer: "grid", TS: 1500, Data: body("ETHUSDT")},
	}
}

func drive(t *testing.T, s Store) {
	t.Helper()
	for _, e := range fixture() {
		if err := s.Put(e); err != nil {
			t.Fatalf("%s put: %v", s.Name(), err)
		}
	}
	latest, ok, err := s.Latest("market.price", "BTCUSDT")
	if err != nil || !ok {
		t.Fatalf("%s latest: ok=%v err=%v", s.Name(), ok, err)
	}
	if latest.TS != 2000 {
		t.Fatalf("%s latest returned ts %d, want the newest (2000)", s.Name(), latest.TS)
	}
	if _, ok, _ := s.Get("a3"); !ok {
		t.Fatalf("%s get: missing a3", s.Name())
	}
	if _, ok, _ := s.Get("nope"); ok {
		t.Fatalf("%s get: returned an envelope that does not exist", s.Name())
	}
	n, err := s.Count()
	if err != nil || n != 3 {
		t.Fatalf("%s count: %d err=%v", s.Name(), n, err)
	}
}

func TestJournal(t *testing.T) {
	j, err := OpenJournal(filepath.Join(t.TempDir(), "journal.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	defer j.Close()
	drive(t, j)
}

func TestKV(t *testing.T) {
	k, err := OpenKV(filepath.Join(t.TempDir(), "kv.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer k.Close()
	drive(t, k)
}

func TestSQL(t *testing.T) {
	s, err := OpenSQL(filepath.Join(t.TempDir(), "store.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	drive(t, s)
}

func TestFileDocs(t *testing.T) {
	d, err := OpenFileDocs(filepath.Join(t.TempDir(), "docs"))
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	drive(t, d)
}

// the projections must be rebuildable from the journal alone
func TestJournalRebuildsTheProjections(t *testing.T) {
	dir := t.TempDir()
	j, _ := OpenJournal(filepath.Join(dir, "journal.jsonl"))
	for _, e := range fixture() {
		if err := j.Put(e); err != nil {
			t.Fatal(err)
		}
	}
	j.Close()

	reopened, err := OpenJournal(filepath.Join(dir, "journal.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()

	k, _ := OpenKV(filepath.Join(dir, "kv.db"))
	defer k.Close()
	all, err := reopened.readAll()
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range all {
		if err := k.Put(e); err != nil {
			t.Fatal(err)
		}
	}
	latest, ok, _ := k.Latest("market.price", "BTCUSDT")
	if !ok || latest.TS != 2000 {
		t.Fatalf("rebuilt kv disagrees: ok=%v ts=%d", ok, latest.TS)
	}
}

// a failing backend must not lose the write for the others
type broken struct{ *Journal }

func (b broken) Name() string       { return "broken" }
func (b broken) Put(Envelope) error { return errBroken }
func (b broken) Close() error       { return nil }

var errBroken = jsonError("broken store")

type jsonError string

func (e jsonError) Error() string { return string(e) }

func TestMultiSurvivesAFailingBackend(t *testing.T) {
	dir := t.TempDir()
	j, _ := OpenJournal(filepath.Join(dir, "journal.jsonl"))
	k, _ := OpenKV(filepath.Join(dir, "kv.db"))
	defer k.Close()
	defer j.Close()

	m := NewMulti(j, broken{}, k)
	for _, e := range fixture() {
		_ = m.Put(e) // the error is expected and reported, not fatal
	}
	if got := m.Failures()["broken"]; got != 3 {
		t.Fatalf("failures recorded %d, want 3", got)
	}
	if _, ok, _ := k.Get("a2"); !ok {
		t.Fatal("kv lost a write because another backend failed")
	}
	if _, ok, _ := j.Get("a2"); !ok {
		t.Fatal("journal lost a write because another backend failed")
	}
}

func TestSQLBooksAndTrades(t *testing.T) {
	s, err := OpenSQL(filepath.Join(t.TempDir(), "store.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()

	if err := s.UpsertBook(Book{Engine: "grid", Equity: 5100, Start: 5000, Open: 2, Closed: 8, UpdatedAt: 10}); err != nil {
		t.Fatal(err)
	}
	if err := s.UpsertBook(Book{Engine: "grid", Equity: 5050, Start: 5000, Open: 1, Closed: 9, UpdatedAt: 20}); err != nil {
		t.Fatal(err)
	}
	books, err := s.Books()
	if err != nil || len(books) != 1 {
		t.Fatalf("books: %d err=%v", len(books), err)
	}
	if books[0].Equity != 5050 || books[0].Closed != 9 {
		t.Fatalf("upsert did not replace: %+v", books[0])
	}

	for _, tr := range []Trade{
		{Engine: "grid", Symbol: "BTCUSDT", ExitMS: 1, Net: 0.11},
		{Engine: "grid", Symbol: "ETHUSDT", ExitMS: 2, Net: -0.04},
	} {
		if err := s.InsertTrade(tr); err != nil {
			t.Fatal(err)
		}
	}
	count, net, wins, losses, err := s.TradeStats()
	if err != nil {
		t.Fatal(err)
	}
	if count != 2 || wins != 1 || losses != 1 || net < 0.06 || net > 0.08 {
		t.Fatalf("stats: count=%d net=%.3f w=%d l=%d", count, net, wins, losses)
	}
}
