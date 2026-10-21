package store

import (
	"encoding/json"
	"fmt"
)

// Sinks are the lake's placement targets. Each one answers "do I take this topic"
// and writes only what it is for: SQL rows, trade rows, documents, or the latest
// value per key. None of them receives every envelope.

type SQLSink struct{ DB *SQL }

func (s *SQLSink) Name() string { return "sql" }
func (s *SQLSink) Takes(topic string) bool {
	// the relational home: decisions, fills, and the periodic model summaries
	return topic == "model.decision" || topic == "model.fill" || topic == "model.pnl" ||
		topic == "model.rank"
}
func (s *SQLSink) Write(e Envelope) error { return s.DB.Put(e) }

// TradeSink is the flat projection of a fill: one row per closed trade, joinable
// with the decision by symbol and time.
type TradeSink struct{ DB *SQL }

func (t *TradeSink) Name() string            { return "trades" }
func (t *TradeSink) Takes(topic string) bool { return topic == "model.fill" }
func (t *TradeSink) Write(e Envelope) error {
	var p struct {
		Data struct {
			Engine string  `json:"engine"`
			Symbol string  `json:"symbol"`
			Side   string  `json:"side"`
			Entry  float64 `json:"entry"`
			Exit   float64 `json:"exit"`
			Gross  float64 `json:"gross_pct"`
			Net    float64 `json:"net_pct"`
			Held   float64 `json:"held_s"`
			Reason string  `json:"reason"`
			Notion float64 `json:"notional"`
			Pnl    float64 `json:"pnl_usd"`
			ExitMS int64   `json:"exit_ms"`
		} `json:"data"`
	}
	if err := json.Unmarshal(e.Data, &p); err != nil {
		return fmt.Errorf("trade sink: %w", err)
	}
	d := p.Data
	if d.ExitMS == 0 {
		d.ExitMS = e.TS
	}
	return t.DB.InsertTrade(Trade{
		Engine: d.Engine, Symbol: d.Symbol, ExitMS: d.ExitMS, Side: d.Side,
		Entry: d.Entry, Exit: d.Exit, Gross: d.Gross, Net: d.Net,
		HeldS: d.Held, Reason: d.Reason, Notion: d.Notion, PnlUSD: d.Pnl,
	})
}

// DocsSink is the field-searchable plane: whole envelopes, for the things a human
// searches by field.
type DocsSink struct{ Docs Docs }

func (d *DocsSink) Name() string { return "docs" }
func (d *DocsSink) Takes(topic string) bool {
	return topic == "model.decision" ||
		len(topic) > 6 && topic[:6] == "intel."
}
func (d *DocsSink) Write(e Envelope) error { return d.Docs.Put(e) }

// LatestSink keeps only the newest value per (topic, key): current state, not a
// record of it. RocksDB in the target layout.
type LatestSink struct{ Store Store }

func (l *LatestSink) Name() string           { return "latest" }
func (l *LatestSink) Takes(string) bool      { return true }
func (l *LatestSink) Write(e Envelope) error { return l.Store.Put(e) }
