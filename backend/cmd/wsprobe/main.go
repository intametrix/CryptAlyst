// Command wsprobe isolates a websocket problem: it connects to Binance's
// combined stream endpoint using BOTH a raw gorilla client and the scanner's
// own binance.Stream, at two different stream counts, and reports how many
// frames each actually received.
//
// The point is to separate three candidate causes that all present identically
// as "connects but no data":
//
//  1. the environment rejects or ignores the subscription
//  2. the stream count (150) is the problem
//  3. the scanner's own client code is the problem
//
// Usage: wsprobe [-seconds 12] [-streams 150]
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"

	"pumpdump/backend/internal/binance"
)

func main() {
	seconds := flag.Int("seconds", 12, "how long to listen per test")
	count := flag.Int("streams", 150, "number of streams to subscribe")
	base := flag.String("base", binance.SpotStreamBase, "stream host")
	flag.Parse()

	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(*seconds+20)*time.Second)
	defer cancel()

	client := binance.NewClient(binance.SpotBase)
	symbols := fetchSymbols(ctx, client, *count)
	if len(symbols) == 0 {
		fmt.Println("FATAL: could not fetch symbols")
		os.Exit(1)
	}
	fmt.Printf("using %d symbols: %s ... %s\n", len(symbols), symbols[0], symbols[len(symbols)-1])

	for _, n := range []int{3, len(symbols)} {
		if n > len(symbols) {
			continue
		}
		names := make([]string, 0, n)
		for _, s := range symbols[:n] {
			names = append(names, binance.TickerStream(s))
		}
		url := *base + "/stream?streams=" + strings.Join(names, "/")
		fmt.Printf("\n--- %d streams, url length %d ---\n", n, len(url))

		rawTest(ctx, url, n, *seconds)
		scannerTest(ctx, url, *base, names, n, *seconds)
	}
}

func rawTest(ctx context.Context, url string, n, seconds int) {
	var frames, bytes int64
	dialer := websocket.Dialer{HandshakeTimeout: 15 * time.Second}
	conn, resp, err := dialer.DialContext(ctx, url, nil)
	if err != nil {
		fmt.Printf("  raw gorilla      : DIAL FAILED: %v\n", err)
		if resp != nil {
			fmt.Printf("                     http status %s\n", resp.Status)
		}
		return
	}
	status := "?"
	if resp != nil {
		status = resp.Status
	}
	defer conn.Close()

	deadline := time.Now().Add(time.Duration(seconds) * time.Second)
	conn.SetReadDeadline(deadline)
	for time.Now().Before(deadline) {
		_, payload, err := conn.ReadMessage()
		if err != nil {
			fmt.Printf("  raw gorilla      : http=%s frames=%d bytes=%d stopped: %v\n",
				status, frames, bytes, err)
			return
		}
		frames++
		bytes += int64(len(payload))
		if frames == 1 {
			preview := string(payload)
			if len(preview) > 160 {
				preview = preview[:160]
			}
			fmt.Printf("  raw gorilla      : http=%s FIRST FRAME: %s\n", status, preview)
		}
	}
	fmt.Printf("  raw gorilla      : http=%s frames=%d bytes=%d (listened %ds)\n",
		status, frames, bytes, seconds)
}

func scannerTest(ctx context.Context, url, base string, names []string, n, seconds int) {
	var frames int64
	var first atomic.Value
	stream := binance.NewStream(base, func(stream string, data json.RawMessage) {
		frames++
		raw := string(data)
		if len(raw) > 120 {
			raw = raw[:120]
		}
		first.Store(stream + " -> " + raw)
	}, nil)
	stream.SetStreams(names)

	runCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	go stream.Run(runCtx)

	time.Sleep(time.Duration(seconds) * time.Second)
	cancel()
	time.Sleep(300 * time.Millisecond)

	fmt.Printf("  binance.Stream   : frames=%d reported_messages=%d streams=%d reconnects=%d connected=%v\n",
		frames, stream.Messages(), stream.Streams(), stream.Reconnects(), stream.IsConnected())
	if v := first.Load(); v != nil {
		fmt.Printf("                     first: %v\n", v)
	}
}

// fetchSymbols pulls the real USDT spot universe so the probe exercises exactly
// the same stream names the scanner uses.
func fetchSymbols(ctx context.Context, client *binance.Client, want int) []string {
	metas, err := client.ExchangeInfo(ctx)
	if err != nil {
		fmt.Printf("exchangeInfo failed: %v\n", err)
		return nil
	}
	tickers, err := client.Tickers24h(ctx)
	if err != nil {
		fmt.Printf("ticker failed: %v\n", err)
		return nil
	}
	byVolume := make(map[string]float64, len(tickers))
	for _, t := range tickers {
		byVolume[t.Symbol] = binance.Float(t.QuoteVolume)
	}
	type row struct {
		symbol string
		volume float64
	}
	rows := make([]row, 0, len(metas))
	for _, m := range metas {
		if m.QuoteAsset != "USDT" || !strings.EqualFold(m.Status, "TRADING") {
			continue
		}
		if v, ok := byVolume[m.Symbol]; ok {
			rows = append(rows, row{m.Symbol, v})
		}
	}
	// Sort by volume descending, without pulling in the market package.
	for i := 1; i < len(rows); i++ {
		for j := i; j > 0 && rows[j].volume > rows[j-1].volume; j-- {
			rows[j], rows[j-1] = rows[j-1], rows[j]
		}
	}
	out := make([]string, 0, want)
	for _, r := range rows {
		if len(out) >= want {
			break
		}
		out = append(out, r.symbol)
	}
	return out
}
