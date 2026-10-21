// marketdata replaces market_data.py: the only process that talks to Binance.
// Streams prices, depth and candles, publishes them on the bus, and serves the
// local endpoints the rest of the platform reads.
package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"cryptalyst/core/internal/marketdata"
	"cryptalyst/core/internal/stomp"
	"cryptalyst/core/internal/topics"
	"cryptalyst/core/internal/ws"
)

type ticker24 struct {
	Symbol      string `json:"symbol"`
	QuoteVolume string `json:"quoteVolume"`
}

// fallbackSymbols keeps the service usable when the exchange refuses the boot
// call (a 418 ban, a network blip). A hard-coded liquid list beats no feed.
var fallbackSymbols = []string{
	"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT",
	"AVAXUSDT", "LINKUSDT", "DOTUSDT", "LTCUSDT", "ATOMUSDT", "NEARUSDT", "APTUSDT",
	"ARBUSDT", "OPUSDT", "INJUSDT", "SUIUSDT", "TIAUSDT", "SEIUSDT", "FILUSDT",
	"TRXUSDT", "MATICUSDT", "ETCUSDT", "HBARUSDT", "AAVEUSDT", "UNIUSDT", "ICPUSDT",
	"RENDERUSDT", "FETUSDT", "WLDUSDT", "PEPEUSDT", "SHIBUSDT", "BCHUSDT", "XLMUSDT",
	"ALGOUSDT", "VETUSDT", "SANDUSDT", "GALAUSDT", "EOSUSDT",
}

// backfillKlines fetches historical bars from Binance REST so a fresh start is
// not history-starved while the websocket warms up. limit is the Binance max of
// 1000, but the store caps at 200, so 200 is enough to fill the window.
func backfillKlines(sym, interval string, limit int) []marketdata.Kline {
	u := fmt.Sprintf("https://api.binance.com/api/v3/klines?symbol=%s&interval=%s&limit=%d",
		sym, interval, limit)
	client := http.Client{Timeout: 10 * time.Second}
	resp, err := client.Get(u)
	if err != nil {
		return nil
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return nil
	}
	dec := json.NewDecoder(resp.Body)
	dec.UseNumber()
	var rows [][]any
	if err := dec.Decode(&rows); err != nil {
		return nil
	}
	out := make([]marketdata.Kline, 0, len(rows))
	for _, r := range rows {
		if len(r) < 10 {
			continue
		}
		out = append(out, marketdata.Kline{
			T: klineInt(r[0]), O: klineFloat(r[1]), H: klineFloat(r[2]),
			L: klineFloat(r[3]), C: klineFloat(r[4]), V: klineFloat(r[5]),
			Q: klineFloat(r[7]), Qb: klineFloat(r[9]),
		})
	}
	return out
}

func klineFloat(v any) float64 {
	switch x := v.(type) {
	case string:
		f, _ := strconv.ParseFloat(x, 64)
		return f
	case json.Number:
		f, _ := x.Float64()
		return f
	case float64:
		return x
	}
	return 0
}

func klineInt(v any) int64 {
	switch x := v.(type) {
	case json.Number:
		i, _ := x.Int64()
		return i
	case float64:
		return int64(x)
	}
	return 0
}

func loadSymbols(top int) []string {
	client := http.Client{Timeout: 20 * time.Second}
	resp, err := client.Get("https://api.binance.com/api/v3/ticker/24hr")
	if err != nil {
		log.Printf("symbol ranking failed: %v", err)
		return nil
	}
	defer resp.Body.Close()
	var rows []ticker24
	if err := json.NewDecoder(resp.Body).Decode(&rows); err != nil {
		return nil
	}
	type pair struct {
		sym string
		qv  float64
	}
	pairs := make([]pair, 0, len(rows))
	for _, r := range rows {
		if !strings.HasSuffix(r.Symbol, "USDT") {
			continue
		}
		var qv float64
		fmt.Sscanf(r.QuoteVolume, "%f", &qv)
		pairs = append(pairs, pair{r.Symbol, qv})
	}
	for i := 0; i < len(pairs); i++ {
		for j := i + 1; j < len(pairs); j++ {
			if pairs[j].qv > pairs[i].qv {
				pairs[i], pairs[j] = pairs[j], pairs[i]
			}
		}
	}
	if top > len(pairs) {
		top = len(pairs)
	}
	out := make([]string, top)
	for i := 0; i < top; i++ {
		out[i] = pairs[i].sym
	}
	return out
}

func main() {
	port := flag.Int("port", 8796, "http port")
	top := flag.Int("top", 40, "symbols to stream")
	addr := flag.String("stomp", "127.0.0.1:61613", "broker address")
	flag.Parse()
	log.SetFlags(log.Ltime)

	// The ranking call is one HTTP request at start. A transient failure used to pin
	// the platform to a 40-symbol fallback for its whole life, which then silently
	// capped every model's universe. Three attempts before falling back.
	var symbols []string
	for attempt := 1; attempt <= 3 && len(symbols) == 0; attempt++ {
		symbols = loadSymbols(*top)
		if len(symbols) == 0 {
			log.Printf("symbol ranking attempt %d failed, retrying", attempt)
			time.Sleep(time.Duration(attempt) * 2 * time.Second)
		}
	}
	if len(symbols) == 0 {
		log.Printf("exchange ranking unavailable — using the built-in liquid list of 40; models cannot select beyond it until the feed is restarted")
		symbols = fallbackSymbols
		if *top < len(symbols) {
			symbols = symbols[:*top]
		}
	}
	streams := []string{"!miniTicker@arr"}
	for _, s := range symbols {
		l := strings.ToLower(s)
		streams = append(streams, l+"@bookTicker", l+"@depth5", l+"@kline_1m", l+"@kline_15m")
	}
	// one connection carries at most ~1024 streams, so 400 symbols are sharded
	// across several connections; each shard is an independent websocket
	const maxStreamsPerConn = 900
	var shards [][]string
	for i := 0; i < len(streams); i += maxStreamsPerConn {
		end := i + maxStreamsPerConn
		if end > len(streams) {
			end = len(streams)
		}
		shards = append(shards, streams[i:end])
	}
	log.Printf("sharding %d streams into %d connections", len(streams), len(shards))
	log.Printf("market_data (Go) — %d symbols, %d streams", len(symbols), len(streams))

	st := marketdata.New()

	// Backfill historical bars so the platform is not history-starved on a fresh
	// start. Runs concurrently with the live feed; the websocket fills any gap.
	go func() {
		log.Printf("backfilling %d symbols x {1m,15m} klines", len(symbols))
		seeded := 0
		for _, sym := range symbols {
			k1 := backfillKlines(sym, "1m", 200)
			k15 := backfillKlines(sym, "15m", 200)
			if len(k1) > 0 || len(k15) > 0 {
				st.SeedKlines(sym, k1, k15)
				seeded++
			}
			time.Sleep(80 * time.Millisecond) // ~12 req/s, well under Binance's 1200/min
		}
		log.Printf("backfill done: %d/%d symbols seeded", seeded, len(symbols))
	}()
	cl := stomp.New(*addr, "cryptalyst", "cryptalyst")

	go func() {
		tickSpec, _ := topics.Get("ticks")
		priceSpec, _ := topics.Get("market.price")
		depthSpec, _ := topics.Get("market.depth")
		miniSpec, _ := topics.Get("market.mini24h")
		type depthMark struct {
			at    time.Time
			price float64
			qty   float64
		}
		lastDepth := map[string]depthMark{}
		for {
			if !cl.Healthy() {
				if err := cl.Connect(); err != nil {
					time.Sleep(time.Second)
					continue
				}
			}
			ticks, price := st.TickPayload()
			cl.PublishWithHeaders(tickSpec.Exchange, tickSpec.Name, metaFor(tickSpec.Name, "market", ""), ticks)
			cl.PublishWithHeaders(priceSpec.Exchange, priceSpec.Name, metaFor(priceSpec.Name, "market", ""), price)
			miniBody, _ := json.Marshal(map[string]any{"count": len(symbols), "ts": time.Now().UnixMilli()})
			cl.PublishWithHeaders(miniSpec.Exchange, miniSpec.Name, metaFor(miniSpec.Name, "market", ""), miniBody)
			for _, s := range symbols {
				mark := lastDepth[s]
				if time.Since(mark.at) < 250*time.Millisecond {
					continue
				}
				// publish on change, plus a one-second heartbeat so a quiet book
				// still proves it is alive
				changed := st.DepthChanged(s, mark.price, mark.qty)
				if !changed && time.Since(mark.at) < time.Second {
					continue
				}
				if body := st.DepthPayload(s); body != nil {
					cl.PublishWithHeaders(depthSpec.Exchange, depthSpec.Name, metaFor(depthSpec.Name, "market", s), body)
					price, qty := st.TopOfBook(s)
					lastDepth[s] = depthMark{at: time.Now(), price: price, qty: qty}
				}
			}
			time.Sleep(250 * time.Millisecond)
		}
	}()

	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		msgs, last, series, syms, prices := st.Stats()
		json.NewEncoder(w).Encode(map[string]any{
			"ok": true, "service": "market_data", "mode": "streams-only",
			"symbols_streamed": len(symbols), "kline_series": series,
			"prices": prices, "series_symbols": syms, "kline_events": st.KlineCount(),
			"limits": map[string]any{"violations": 0},
			"stats": map[string]any{"msgs": msgs, "last_ms": last,
				"status": fmt.Sprintf("streaming %d streams", len(streams))},
		})
	})
	mux.HandleFunc("/streamprices", func(w http.ResponseWriter, r *http.Request) {
		msgs, last, _, _, _ := st.Stats()
		json.NewEncoder(w).Encode(map[string]any{
			"p": st.Snapshot(), "ts": last, "msgs": msgs,
			"status": fmt.Sprintf("streaming %d streams", len(streams))})
	})
	mux.HandleFunc("/price", func(w http.ResponseWriter, r *http.Request) {
		sym := strings.ToUpper(r.URL.Query().Get("symbol"))
		px, ok := st.Price(sym)
		json.NewEncoder(w).Encode(map[string]any{"symbol": sym, "price": px, "found": ok})
	})
	mux.HandleFunc("/klines", func(w http.ResponseWriter, r *http.Request) {
		sym := strings.ToUpper(r.URL.Query().Get("symbol"))
		iv := r.URL.Query().Get("interval")
		if iv == "" {
			iv = "1m"
		}
		limit := 200
		fmt.Sscanf(r.URL.Query().Get("limit"), "%d", &limit)
		json.NewEncoder(w).Encode(map[string]any{"symbol": sym, "interval": iv,
			"klines": st.KLines(sym, iv, limit), "source": "stream"})
	})
	mux.HandleFunc("/api/v3/klines", func(w http.ResponseWriter, r *http.Request) {
		sym := strings.ToUpper(r.URL.Query().Get("symbol"))
		iv := r.URL.Query().Get("interval")
		if iv == "" {
			iv = "1m"
		}
		limit := 200
		fmt.Sscanf(r.URL.Query().Get("limit"), "%d", &limit)
		ks := st.KLines(sym, iv, limit)
		out := make([][]any, 0, len(ks))
		for _, k := range ks {
			out = append(out, []any{k.T, k.O, k.H, k.L, k.C, k.V, k.T, k.Q, 0, 0, k.Qb})
		}
		json.NewEncoder(w).Encode(out)
	})
	mux.HandleFunc("/api/v3/ticker/price", func(w http.ResponseWriter, r *http.Request) {
		sym := strings.ToUpper(r.URL.Query().Get("symbol"))
		if sym != "" {
			px, ok := st.Price(sym)
			if !ok {
				w.WriteHeader(404)
				json.NewEncoder(w).Encode(map[string]any{"code": -1121, "msg": "Invalid symbol."})
				return
			}
			json.NewEncoder(w).Encode(map[string]any{"symbol": sym, "price": fmt.Sprint(px)})
			return
		}
		snap := st.Snapshot()
		rows := make([]map[string]string, 0, len(snap))
		for k, v := range snap {
			rows = append(rows, map[string]string{"symbol": k, "price": fmt.Sprint(v)})
		}
		json.NewEncoder(w).Encode(rows)
	})
	mux.HandleFunc("/api/v3/ticker/24hr", func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(st.Movers(1000, 0)["most_traded"])
	})
	// /flows returns the recent flow windows per symbol. The Go feed does not
	// aggregate flow yet, so it answers with the window it does know about and a
	// count of what it has seen, rather than 404ing the dashboard panel.
	mux.HandleFunc("/flows", func(w http.ResponseWriter, r *http.Request) {
		limit := 100
		fmt.Sscanf(r.URL.Query().Get("limit"), "%d", &limit)
		if limit <= 0 || limit > 500 {
			limit = 100
		}
		px := st.Snapshot()
		windows := make([]map[string]any, 0, len(px))
		for sym, price := range px {
			if len(windows) >= limit {
				break
			}
			windows = append(windows, map[string]any{
				"symbol": sym, "price": price, "flow_conf": 0, "buy_ratio": 0.5,
				"note": "flow aggregation runs in the intelligence producers",
			})
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{
			"windows": windows, "count": len(windows),
			"ts":   time.Now().UnixMilli(),
			"note": "prices from the feed; flow confidence requires intel.flow",
		})
	})

	// /depth is what the models read to judge book pressure: the same payload the
	// bus carries, served on demand for one symbol.
	mux.HandleFunc("/depth", func(w http.ResponseWriter, r *http.Request) {
		sym := strings.ToUpper(r.URL.Query().Get("symbol"))
		if sym == "" {
			http.Error(w, "symbol required", 400)
			return
		}
		body := st.DepthPayload(sym)
		if body == nil {
			http.Error(w, "no depth for "+sym, 404)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write(body)
	})
	mux.HandleFunc("/movers", func(w http.ResponseWriter, r *http.Request) {
		limit := 12
		fmt.Sscanf(r.URL.Query().Get("limit"), "%d", &limit)
		var minQ float64
		fmt.Sscanf(r.URL.Query().Get("minq"), "%f", &minQ)
		json.NewEncoder(w).Encode(st.Movers(limit, minQ))
	})
	go func() {
		log.Printf("endpoints on :%d", *port)
		log.Fatal(http.ListenAndServe(fmt.Sprintf("0.0.0.0:%d", *port), mux))
	}()

	for shardIndex := range shards {
		shard := shards[shardIndex]
		go streamShard(st, cl, shard, shardIndex)
	}
	select {}
}

var (
	metaMu  sync.Mutex
	metaSeq uint64
)

// metaFor builds the provenance headers for one publish. The body is untouched:
// existing consumers keep parsing exactly what they always did.
func metaFor(topic, plane, key string) map[string]string {
	metaMu.Lock()
	metaSeq++
	n := metaSeq
	metaMu.Unlock()
	return map[string]string{
		"v": "1", "plane": plane, "topic": topic, "producer": "market_data",
		"key": key, "gen": fmt.Sprint(n), "seq": fmt.Sprint(n),
		"cid": mintCID(), "computed_at": time.Now().UTC().Format(time.RFC3339Nano),
	}
}

// streamShard keeps one websocket alive: 900 streams per connection, reconnecting
// with backoff. 400 symbols therefore need five connections, not one.
type outbound struct {
	topic string
	key   string
	body  []byte
}

func streamShard(st *marketdata.State, cl *stomp.Client, streams []string, index int) {
	url := "wss://stream.binance.com:9443/stream?streams=" + strings.Join(streams, "/")

	queue := make(chan outbound, 8192)
	go func() {
		for msg := range queue {
			if !cl.Healthy() {
				continue
			}
			spec, ok := topics.Get(msg.topic)
			if !ok {
				continue
			}
			cl.PublishWithHeaders(spec.Exchange, spec.Name, metaFor(msg.topic, "market", msg.key), msg.body)
		}
	}()

	var dropped int64
	emit := func(topic, key string, body []byte) {
		select {
		case queue <- outbound{topic: topic, key: key, body: body}:
		default:
			// never block the socket read: drop the sample and say so
			if atomic.AddInt64(&dropped, 1)%5000 == 1 {
				log.Printf("shard %d: publisher behind, dropped %d messages", index, atomic.LoadInt64(&dropped))
			}
		}
	}
	for {
		conn, err := ws.Dial(url, 15*time.Second)
		if err != nil {
			log.Printf("shard %d connect failed: %v", index, err)
			time.Sleep(3 * time.Second)
			continue
		}
		log.Printf("shard %d connected (%d streams)", index, len(streams))
		for {
			payload, _, err := conn.Read()
			if err != nil {
				log.Printf("shard %d dropped: %v", index, err)
				conn.Close()
				break
			}
			handle(st, emit, payload)
		}
		time.Sleep(2 * time.Second)
	}
}

var debugKline = true
var debugKlineErr = true

// mintCID creates the local identity ATLAS calls for: a boundary mints, nothing
// central has to be reachable for work to start.
func mintCID() string {
	b := make([]byte, 8)
	rand.Read(b)
	return "cid-" + hex.EncodeToString(b)
}

func handle(st *marketdata.State, emit func(topic, key string, body []byte), payload []byte) {
	var env struct {
		Stream string          `json:"stream"`
		Data   json.RawMessage `json:"data"`
	}
	if err := json.Unmarshal(payload, &env); err != nil {
		return
	}
	switch {
	case env.Stream == "!miniTicker@arr":
		var arr []struct {
			S string `json:"s"`
			C string `json:"c"`
			O string `json:"o"`
			Q string `json:"q"`
			V string `json:"v"`
		}
		if err := json.Unmarshal(env.Data, &arr); err != nil {
			return
		}
		for _, m := range arr {
			var last, open, q, v float64
			fmt.Sscanf(m.C, "%f", &last)
			fmt.Sscanf(m.O, "%f", &open)
			fmt.Sscanf(m.Q, "%f", &q)
			fmt.Sscanf(m.V, "%f", &v)
			pct := 0.0
			if open > 0 {
				pct = (last/open - 1) * 100
			}
			st.OnMini(m.S, marketdata.Mini{Pct: pct, Q: q, V: v, P: last})
		}
	case strings.HasSuffix(env.Stream, "@bookTicker"):
		var b struct {
			S  string `json:"s"`
			B  string `json:"b"`
			A  string `json:"a"`
			Bq string `json:"B"`
			Aq string `json:"A"`
		}
		if err := json.Unmarshal(env.Data, &b); err != nil {
			return
		}
		var bid, ask, bq, aq float64
		fmt.Sscanf(b.B, "%f", &bid)
		fmt.Sscanf(b.A, "%f", &ask)
		fmt.Sscanf(b.Bq, "%f", &bq)
		fmt.Sscanf(b.Aq, "%f", &aq)
		st.OnBook(b.S, marketdata.Book{Bid: bid, Ask: ask, BidQty: bq, AskQty: aq})
	case strings.Contains(env.Stream, "@depth5"):
		var d struct {
			Bids [][2]string `json:"bids"`
			Asks [][2]string `json:"asks"`
		}
		if err := json.Unmarshal(env.Data, &d); err != nil {
			return
		}
		sym := strings.ToUpper(strings.Split(env.Stream, "@")[0])
		conv := func(in [][2]string) []marketdata.Level {
			out := make([]marketdata.Level, 0, len(in))
			for _, lv := range in {
				var p, q float64
				fmt.Sscanf(lv[0], "%f", &p)
				fmt.Sscanf(lv[1], "%f", &q)
				out = append(out, marketdata.Level{Price: p, Qty: q})
			}
			return out
		}
		st.OnDepth(sym, conv(d.Bids), conv(d.Asks))
	case strings.Contains(env.Stream, "@kline_"):
		if debugKline {
			debugKline = false
			log.Printf("first kline stream=%q bytes=%d head=%.200s", env.Stream, len(env.Data), string(env.Data))
		}
		var k struct {
			K struct {
				T       int64  `json:"t"`
				O       string `json:"o"`
				H       string `json:"h"`
				Low     string `json:"l"`
				C       string `json:"c"`
				LastID  int64  `json:"L"`
				FirstID int64  `json:"f"`
				V       string `json:"v"`
				Q       string `json:"q"`
				Qb      string `json:"Q"`
				X       bool   `json:"x"`
				S       string `json:"s"`
			} `json:"k"`
		}
		if err := json.Unmarshal(env.Data, &k); err != nil {
			if debugKlineErr {
				debugKlineErr = false
				log.Printf("kline decode failed: %v", err)
			}
			return
		}
		var o, h, l, c, v, q, qb float64
		fmt.Sscanf(k.K.O, "%f", &o)
		fmt.Sscanf(k.K.H, "%f", &h)
		fmt.Sscanf(k.K.Low, "%f", &l)
		fmt.Sscanf(k.K.C, "%f", &c)
		fmt.Sscanf(k.K.V, "%f", &v)
		fmt.Sscanf(k.K.Q, "%f", &q)
		fmt.Sscanf(k.K.Qb, "%f", &qb)
		iv := "1m"
		if strings.HasSuffix(env.Stream, "15m") {
			iv = "15m"
		}
		st.OnKline(k.K.S, iv, marketdata.Kline{T: k.K.T, O: o, H: h, L: l, C: c, V: v, Q: q, Qb: qb}, k.K.X)
		if k.K.X && emit != nil {
			body, _ := json.Marshal(map[string]any{
				"symbol": k.K.S, "interval": iv, "t": k.K.T,
				"o": o, "h": h, "l": l, "c": c, "v": v, "q": q, "Q": qb,
				"ts": time.Now().UnixMilli(),
			})
			emit("market.candle", k.K.S, body)
		}
	}
}
