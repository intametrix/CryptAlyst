package store

import (
	"database/sql"
	"encoding/json"
	"fmt"

	_ "modernc.org/sqlite"
)

// SQL holds the things an investigator wants to join: envelopes, books and trades.
// It is a projection of the record, never the record itself — the journal can
// rebuild every row here, and a test proves it.

type SQL struct {
	db *sql.DB
}

const schema = `
CREATE TABLE IF NOT EXISTS envelopes (
  id TEXT PRIMARY KEY, topic TEXT NOT NULL, key TEXT NOT NULL,
  plane TEXT, producer TEXT, ts INTEGER NOT NULL, data TEXT);
CREATE INDEX IF NOT EXISTS envelopes_topic_ts ON envelopes(topic, ts DESC);
CREATE INDEX IF NOT EXISTS envelopes_key_ts ON envelopes(key, ts DESC);

CREATE TABLE IF NOT EXISTS books (
  engine TEXT PRIMARY KEY, equity REAL, start_equity REAL,
  open_positions INTEGER, closed_trades INTEGER, updated_at INTEGER);

CREATE TABLE IF NOT EXISTS trades (
  engine TEXT NOT NULL, symbol TEXT NOT NULL, exit_ms INTEGER NOT NULL,
  side TEXT, entry REAL, exit REAL, gross_pct REAL, net_pct REAL,
  held_s REAL, reason TEXT, notional REAL, pnl_usd REAL,
  PRIMARY KEY (engine, symbol, exit_ms));
`

func OpenSQL(path string) (*SQL, error) {
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1) // sqlite: one writer, no surprises
	if _, err := db.Exec(schema); err != nil {
		return nil, err
	}
	return &SQL{db: db}, nil
}

func (s *SQL) Name() string { return "sql" }

func (s *SQL) Put(e Envelope) error {
	_, err := s.db.Exec(
		`INSERT OR REPLACE INTO envelopes (id, topic, key, plane, producer, ts, data)
		 VALUES (?, ?, ?, ?, ?, ?, ?)`,
		e.ID, e.Topic, e.Key, e.Plane, e.Producer, e.TS, string(e.Data))
	return err
}

func (s *SQL) Latest(topic, key string) (Envelope, bool, error) {
	var e Envelope
	var data string
	err := s.db.QueryRow(
		`SELECT id, topic, key, plane, producer, ts, data FROM envelopes
		 WHERE topic = ? AND key = ? ORDER BY ts DESC LIMIT 1`, topic, key).
		Scan(&e.ID, &e.Topic, &e.Key, &e.Plane, &e.Producer, &e.TS, &data)
	if err == sql.ErrNoRows {
		return Envelope{}, false, nil
	}
	if err != nil {
		return Envelope{}, false, err
	}
	e.Data = json.RawMessage(data)
	return e, true, nil
}

func (s *SQL) Get(id string) (Envelope, bool, error) {
	var e Envelope
	var data string
	err := s.db.QueryRow(
		`SELECT id, topic, key, plane, producer, ts, data FROM envelopes WHERE id = ?`, id).
		Scan(&e.ID, &e.Topic, &e.Key, &e.Plane, &e.Producer, &e.TS, &data)
	if err == sql.ErrNoRows {
		return Envelope{}, false, nil
	}
	if err != nil {
		return Envelope{}, false, err
	}
	e.Data = json.RawMessage(data)
	return e, true, nil
}

func (s *SQL) Count() (int, error) {
	var n int
	err := s.db.QueryRow(`SELECT COUNT(*) FROM envelopes`).Scan(&n)
	return n, err
}

func (s *SQL) Close() error { return s.db.Close() }

// ---------------------------------------------------------------- books and trades

// Book is the projection the dashboard reads.
type Book struct {
	Engine    string  `json:"engine"`
	Equity    float64 `json:"equity"`
	Start     float64 `json:"start_equity"`
	Open      int     `json:"open_positions"`
	Closed    int     `json:"closed_trades"`
	UpdatedAt int64   `json:"updated_at"`
}

type Trade struct {
	Engine string  `json:"engine"`
	Symbol string  `json:"symbol"`
	ExitMS int64   `json:"exit_ms"`
	Side   string  `json:"side"`
	Entry  float64 `json:"entry"`
	Exit   float64 `json:"exit"`
	Gross  float64 `json:"gross_pct"`
	Net    float64 `json:"net_pct"`
	HeldS  float64 `json:"held_s"`
	Reason string  `json:"reason"`
	Notion float64 `json:"notional"`
	PnlUSD float64 `json:"pnl_usd"`
}

func (s *SQL) UpsertBook(b Book) error {
	_, err := s.db.Exec(
		`INSERT INTO books (engine, equity, start_equity, open_positions, closed_trades, updated_at)
		 VALUES (?, ?, ?, ?, ?, ?)
		 ON CONFLICT(engine) DO UPDATE SET
		   equity=excluded.equity, start_equity=excluded.start_equity,
		   open_positions=excluded.open_positions, closed_trades=excluded.closed_trades,
		   updated_at=excluded.updated_at`,
		b.Engine, b.Equity, b.Start, b.Open, b.Closed, b.UpdatedAt)
	return err
}

func (s *SQL) InsertTrade(t Trade) error {
	_, err := s.db.Exec(
		`INSERT OR REPLACE INTO trades
		 (engine, symbol, exit_ms, side, entry, exit, gross_pct, net_pct, held_s, reason, notional, pnl_usd)
		 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		t.Engine, t.Symbol, t.ExitMS, t.Side, t.Entry, t.Exit, t.Gross, t.Net, t.HeldS, t.Reason, t.Notion, t.PnlUSD)
	return err
}

func (s *SQL) Books() ([]Book, error) {
	rows, err := s.db.Query(`SELECT engine, equity, start_equity, open_positions, closed_trades, updated_at
	                         FROM books ORDER BY equity - start_equity DESC`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Book
	for rows.Next() {
		var b Book
		if err := rows.Scan(&b.Engine, &b.Equity, &b.Start, &b.Open, &b.Closed, &b.UpdatedAt); err != nil {
			return nil, err
		}
		out = append(out, b)
	}
	return out, rows.Err()
}

// TradeStats is the question the compliance view asks first.
func (s *SQL) TradeStats() (count int, net float64, wins, losses int, err error) {
	row := s.db.QueryRow(`SELECT COUNT(*), COALESCE(SUM(net_pct),0),
	                         SUM(CASE WHEN net_pct > 0 THEN 1 ELSE 0 END),
	                         SUM(CASE WHEN net_pct <= 0 THEN 1 ELSE 0 END) FROM trades`)
	err = row.Scan(&count, &net, &wins, &losses)
	if err != nil {
		return 0, 0, 0, 0, fmt.Errorf("trade stats: %w", err)
	}
	return count, net, wins, losses, nil
}
