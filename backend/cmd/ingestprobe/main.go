// Command ingestprobe tests the flow engine's tick ingestion in isolation, so
// a "stream receives frames but the engine counts zero" bug can be attributed
// to either the engine or the wiring around it.
//
// Usage: ingestprobe
package main

import (
	"encoding/json"
	"fmt"

	"pumpdump/backend/internal/flow"
)

// A real @ticker payload shape, copied from a live frame.
const liveTicker = `{"e":"24hrTicker","E":1792094449017,"s":"BTCUSDT",` +
	`"p":"498.32000000","P":"0.609","w":"81346.03195921","x":"81887.46000000",` +
	`"c":"82400.00000000","Q":"0.00008000","b":"82399.99","B":"1.5",` +
	`"a":"82400.01","A":"2.5","o":"81800.00","h":"82634.30","l":"79800.00",` +
	`"v":"22568.95","q":"1835539603.39","O":1792005344010,"C":1792091744010,` +
	`"F":1,"L":2,"n":4605506}`

func main() {
	engine := flow.NewEngine(flow.DefaultConfig())
	engine.SetUniverse([]string{"BTCUSDT", "ETHUSDT"})

	fmt.Printf("tracked before ingest: %v\n", engine.Tracked())
	fmt.Printf("messages before      : %d\n", engine.Stats().Messages)

	sym := engine.IngestTicker(json.RawMessage(liveTicker))
	fmt.Printf("IngestTicker returned: %q\n", sym)
	fmt.Printf("messages after       : %d\n", engine.Stats().Messages)
	stats := engine.Stats()
	fmt.Printf("stats: symbols=%d ready=%d messages=%d\n",
		stats.SymbolsTracked, stats.BaselinesReady, stats.Messages)

	// Second tick for the same symbol, so a delta exists.
	const second = `{"e":"24hrTicker","E":1792094449999,"s":"BTCUSDT",` +
		`"c":"82410.00000000","P":"0.62","b":"82409.99","B":"1.5","a":"82410.01",` +
		`"A":"2.5","o":"81800.00","h":"82634.30","l":"79800.00","v":"22570.00",` +
		`"q":"1835547000.00","n":4605600}`
	engine.IngestTicker(json.RawMessage(second))
	fmt.Printf("messages after 2nd   : %d\n", engine.Stats().Messages)

	rows := engine.Snapshot(5, 1792094449999)
	fmt.Printf("snapshot rows        : %d\n", len(rows))
	for _, r := range rows {
		fmt.Printf("  %s flow=%.0f burst=%.2fx price=%.2f\n",
			r.Symbol, r.FlowQuote, r.BurstMultiple, r.Price)
	}

	// Negative control: a symbol that is NOT in the universe must be rejected.
	sym2 := engine.IngestTicker(json.RawMessage(
		`{"e":"24hrTicker","E":1792094449999,"s":"FOOUSDT","c":"1.0","q":"100","n":1}`))
	fmt.Printf("untracked symbol     : %q (expected empty)\n", sym2)
}
