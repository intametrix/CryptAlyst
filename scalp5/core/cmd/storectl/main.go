// storectl inspects and syncs the platform's stores.
//
//	storectl stats   --kv ../state/kv.db --sql ../state/store.db --docs ../state/docs
//	storectl latest  --kv ../state/kv.db --topic model.fill --key BTCUSDT
//	storectl sync    --sql ../state/store.db --packs ../state
//	storectl trades  --sql ../state/store.db
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"sort"

	"cryptalyst/core/internal/store"
)

func main() {
	cmd := flag.String("cmd", "stats", "stats | latest | sync | trades")
	kvPath := flag.String("kv", "", "kv store path")
	sqlPath := flag.String("sql", "", "sqlite path")
	docsDir := flag.String("docs", "", "documents directory")
	lsmPath := flag.String("lsm", "", "rocksdb-class store (pebble) directory")
	topic := flag.String("topic", "", "topic for latest")
	key := flag.String("key", "", "key for latest")
	packsDir := flag.String("packs", "", "directory holding pack_*.json for sync")
	flag.Parse()

	switch *cmd {
	case "stats":
		if *kvPath != "" {
			if k, err := store.OpenKVReadOnly(*kvPath); err == nil {
				n, _ := k.Count()
				fmt.Printf("kv     %-40s %d envelopes\n", *kvPath, n)
				k.Close()
			} else {
				fmt.Fprintf(os.Stderr, "kv: %v\n", err)
			}
		}
		if *sqlPath != "" {
			if s, err := store.OpenSQL(*sqlPath); err == nil {
				n, _ := s.Count()
				fmt.Printf("sql    %-40s %d envelopes\n", *sqlPath, n)
				s.Close()
			} else {
				fmt.Fprintf(os.Stderr, "sql: %v\n", err)
			}
		}
		if *lsmPath != "" {
			if l, err := store.OpenLSM(*lsmPath); err == nil {
				n, _ := l.Count()
				fmt.Printf("lsm    %-40s %d envelopes\n", *lsmPath, n)
				l.Close()
			} else {
				fmt.Fprintf(os.Stderr, "lsm: %v\n", err)
			}
		}
		if *docsDir != "" {
			if d, err := store.OpenFileDocs(*docsDir); err == nil {
				n, _ := d.Count()
				fmt.Printf("docs   %-40s %d documents\n", *docsDir, n)
				d.Close()
			}
		}

	case "latest":
		if *topic == "" {
			fmt.Fprintln(os.Stderr, "latest needs --topic and a store (--kv or --lsm)")
			os.Exit(2)
		}
		if *lsmPath != "" && *kvPath == "" {
			l, err := store.OpenLSM(*lsmPath)
			if err != nil {
				fmt.Fprintln(os.Stderr, err)
				os.Exit(1)
			}
			defer l.Close()
			keys, _ := l.TopicKeys(*topic, 12)
			if *key == "" {
				fmt.Printf("keys for %s: %v\n", *topic, keys)
				return
			}
			e, ok, err := l.Latest(*topic, *key)
			if err != nil || !ok {
				fmt.Printf("no envelope for %s %s\n", *topic, *key)
				return
			}
			fmt.Printf("%s  %s %s  ts=%d producer=%s\n%s\n", e.ID, e.Topic, e.Key, e.TS, e.Producer, e.Data)
			return
		}
		k, err := store.OpenKVReadOnly(*kvPath)
		if err != nil {
			fmt.Fprintf(os.Stderr, "kv: %v (a live recorder holds the file; read the sql projection instead)\n", err)
			os.Exit(1)
		}
		defer k.Close()
		e, ok, err := k.Latest(*topic, *key)
		if err != nil || !ok {
			fmt.Printf("no envelope for %s %s\n", *topic, *key)
			return
		}
		fmt.Printf("%s  %s %s  ts=%d producer=%s\n%s\n", e.ID, e.Topic, e.Key, e.TS, e.Producer, e.Data)

	case "sync":
		if *sqlPath == "" || *packsDir == "" {
			fmt.Fprintln(os.Stderr, "sync needs --sql and --packs")
			os.Exit(2)
		}
		s, err := store.OpenSQL(*sqlPath)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		defer s.Close()
		books, trades, err := syncPacks(s, *packsDir)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		fmt.Printf("synced %d books and %d trades from %s\n", books, trades, *packsDir)

	case "trades":
		if *sqlPath == "" {
			fmt.Fprintln(os.Stderr, "trades needs --sql")
			os.Exit(2)
		}
		s, err := store.OpenSQL(*sqlPath)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		defer s.Close()
		count, net, wins, losses, err := s.TradeStats()
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		fmt.Printf("trades %d  net %+.3f%%  wins %d  losses %d\n", count, net, wins, losses)
		list, _ := s.Books()
		sort.Slice(list, func(i, j int) bool { return list[i].Equity-list[i].Start > list[j].Equity-list[j].Start })
		for _, b := range list {
			fmt.Printf("  %-22s equity %9.2f  pnl %+9.2f  open %2d  closed %3d\n",
				b.Engine, b.Equity, b.Equity-b.Start, b.Open, b.Closed)
		}
	}
}

// syncPacks projects the engines' own state files into sqlite. The packs remain
// the engines' truth; sqlite is what makes them queryable.
func syncPacks(s *store.SQL, dir string) (books, trades int, err error) {
	files, _ := filepath.Glob(filepath.Join(dir, "pack_*.json"))
	for _, f := range files {
		body, readErr := os.ReadFile(f)
		if readErr != nil {
			continue
		}
		var pack struct {
			Engine string  `json:"engine"`
			Equity float64 `json:"equity"`
			Start  float64 `json:"start_equity"`
			Pos    []any   `json:"positions"`
			Trades []struct {
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
			} `json:"trades"`
			TS int64 `json:"ts"`
		}
		if json.Unmarshal(body, &pack) != nil || pack.Engine == "" {
			continue
		}
		if err = s.UpsertBook(store.Book{
			Engine: pack.Engine, Equity: pack.Equity, Start: pack.Start,
			Open: len(pack.Pos), Closed: len(pack.Trades), UpdatedAt: pack.TS,
		}); err != nil {
			return
		}
		books++
		for _, tr := range pack.Trades {
			if tr.ExitMS == 0 {
				tr.ExitMS = pack.TS
			}
			if err = s.InsertTrade(store.Trade{
				Engine: pack.Engine, Symbol: tr.Symbol, ExitMS: tr.ExitMS, Side: tr.Side,
				Entry: tr.Entry, Exit: tr.Exit, Gross: tr.Gross, Net: tr.Net,
				HeldS: tr.Held, Reason: tr.Reason, Notion: tr.Notion, PnlUSD: tr.Pnl,
			}); err != nil {
				return
			}
			trades++
		}
	}
	return
}
