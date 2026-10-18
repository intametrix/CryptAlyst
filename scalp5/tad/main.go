// tad - technical-analysis daemon. Computes a wide set of signals per symbol and
// publishes only the ones that fire, so models can consume TA as a stream.
//
//	GET /latest?since=N   -> {"cursor":M,"signals":[{symbol,kind,side,strength,note,ts}]}
//	GET /scan?symbol=BTCUSDT -> every signal currently true for that symbol
//	GET /health
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"sort"
	"strconv"
	"sync"
	"sync/atomic"
	"time"
)

type bar struct{ O, H, L, C, V float64 }

type sig struct {
	Cursor   int64   `json:"cursor"`
	Symbol   string  `json:"symbol"`
	Kind     string  `json:"kind"`
	Side     string  `json:"side"`
	Strength float64 `json:"strength"`
	Note     string  `json:"note"`
	TS       int64   `json:"ts"`
}

var (
	cur    atomic.Value // []sig snapshot of recent signals
	seen   = map[string]bool{}
	mu     sync.Mutex
	cursor int64
	client = &http.Client{Timeout: 6 * time.Second}
)

func fetch(url string) ([]byte, error) {
	r, err := client.Get(url)
	if err != nil {
		return nil, err
	}
	defer r.Body.Close()
	return io.ReadAll(r.Body)
}

func klines(sym string) []bar {
	b, err := fetch("https://api.binance.com/api/v3/klines?symbol=" + sym + "&interval=1m&limit=120")
	if err != nil {
		return nil
	}
	var raw [][]interface{}
	if json.Unmarshal(b, &raw) != nil {
		return nil
	}
	out := make([]bar, 0, len(raw))
	f := func(v interface{}) float64 {
		switch x := v.(type) {
		case float64:
			return x
		case string:
			n, _ := strconv.ParseFloat(x, 64)
			return n
		}
		return 0
	}
	for _, r := range raw {
		if len(r) < 6 {
			continue
		}
		out = append(out, bar{O: f(r[1]), H: f(r[2]), L: f(r[3]), C: f(r[4]), V: f(r[5])})
	}
	return out
}

func emaLast(v []float64, n int) float64 {
	k := 2.0 / float64(n+1)
	e := v[0]
	for _, x := range v[1:] {
		e = x*k + e*(1-k)
	}
	return e
}

func rsi(v []float64, n int) float64 {
	g, l := 0.0, 0.0
	for i := len(v) - n; i < len(v); i++ {
		if i < 1 {
			continue
		}
		ch := v[i] - v[i-1]
		if ch > 0 {
			g += ch
		} else {
			l -= ch
		}
	}
	if l == 0 {
		return 100
	}
	return 100 - 100/(1+(g/float64(n))/(l/float64(n)))
}

func atr(bs []bar, n int) float64 {
	s, c := 0.0, 0
	for i := len(bs) - n; i < len(bs); i++ {
		if i < 1 {
			continue
		}
		tr := bs[i].H - bs[i].L
		if x := bs[i].H - bs[i-1].C; x > tr {
			tr = x
		}
		if x := bs[i-1].C - bs[i].L; x > tr {
			tr = x
		}
		s += tr
		c++
	}
	if c == 0 {
		return 0
	}
	return s / float64(c)
}

func emit(sym string, out *[]sig, kind, side string, strength float64, note string) {
	key := sym + "|" + kind + "|" + side + "|" + time.Now().Format("2006-01-02T15")
	mu.Lock()
	if seen[key] {
		mu.Unlock()
		return
	}
	seen[key] = true
	cursor++
	s := sig{Cursor: cursor, Symbol: sym, Kind: kind, Side: side,
		Strength: strength, Note: note, TS: time.Now().UnixMilli()}
	mu.Unlock()
	*out = append(*out, s)
}

// analyse computes every signal that is true right now for one symbol.
func analyse(sym string) []sig {
	bs := klines(sym)
	if len(bs) < 60 {
		return nil
	}
	n := len(bs)
	cl := make([]float64, n)
	for i, b := range bs {
		cl[i] = b.C
	}
	px := cl[n-1]
	e9, e21 := emaLast(cl[n-30:], 9), emaLast(cl[n-30:], 21)
	e50 := emaLast(cl, 50)
	r := rsi(cl, 14)
	a := atr(bs, 14)
	atrPct := a / px * 100
	hi := bs[n-20].H
	lo := bs[n-20].L
	for _, b := range bs[n-20:] {
		if b.H > hi {
			hi = b.H
		}
		if b.L < lo {
			lo = b.L
		}
	}
	vol := 0.0
	for _, b := range bs[n-10:] {
		vol += b.V
	}
	vol /= 10
	pv, vv := 0.0, 0.0
	for _, b := range bs[n-30:] {
		pv += b.C * b.V
		vv += b.V
	}
	vwap := 0.0
	if vv > 0 {
		vwap = pv / vv
	}
	var out []sig

	switch {
	case e9 > e21 && px > vwap:
		emit(sym, &out, "ema_cross_up", "buy", 0.6, fmt.Sprintf("ema9>ema21, price>vwap %.4f", vwap))
	case e9 < e21 && px < vwap:
		emit(sym, &out, "ema_cross_down", "sell", 0.6, fmt.Sprintf("ema9<ema21, price<vwap %.4f", vwap))
	}
	if r >= 70 {
		emit(sym, &out, "rsi_overbought", "sell", (r - 70) / 30, fmt.Sprintf("rsi %.0f", r))
	}
	if r <= 30 {
		emit(sym, &out, "rsi_oversold", "buy", (30 - r) / 30, fmt.Sprintf("rsi %.0f", r))
	}
	if px > hi {
		emit(sym, &out, "donchian_break_up", "buy", 0.8, fmt.Sprintf("above 20-bar high %.6g", hi))
	}
	if px < lo {
		emit(sym, &out, "donchian_break_down", "sell", 0.8, fmt.Sprintf("below 20-bar low %.6g", lo))
	}
	if atrPct > 0.30 {
		emit(sym, &out, "vol_expansion", "buy", atrPct/0.60, fmt.Sprintf("atr %.2f%%", atrPct))
	}
	if px > vwap*1.004 {
		emit(sym, &out, "vwap_stretch_up", "sell", 0.5, fmt.Sprintf("%.2f%% above vwap", (px/vwap-1)*100))
	}
	if px < vwap*0.996 {
		emit(sym, &out, "vwap_stretch_down", "buy", 0.5, fmt.Sprintf("%.2f%% below vwap", (1-px/vwap)*100))
	}
	last := bs[n-1]
	if vol > 0 && last.V > 2.5*vol {
		side := "buy"
		if last.C < last.O {
			side = "sell"
		}
		emit(sym, &out, "volume_spike", side, 0.7, fmt.Sprintf("%.1fx avg volume", last.V/vol))
	}
	if px > e50 && e9 > e50 {
		emit(sym, &out, "trend_up_h1", "buy", 0.5, fmt.Sprintf("above ema50 %.6g", e50))
	}
	if px < e50 && e9 < e50 {
		emit(sym, &out, "trend_down_h1", "sell", 0.5, fmt.Sprintf("below ema50 %.6g", e50))
	}
	return out
}

var symbols = []string{}

func loadSymbols() {
	b, err := fetch("http://127.0.0.1:8799/api/universe800")
	if err != nil {
		return
	}
	var d struct {
		Tickers []struct {
			S string  `json:"s"`
			V float64 `json:"v"`
		} `json:"tickers"`
	}
	if json.Unmarshal(b, &d) == nil {
		sort.Slice(d.Tickers, func(i, j int) bool { return d.Tickers[i].V > d.Tickers[j].V })
		top := []string{}
		for _, t := range d.Tickers {
			if len(t.S) > 4 && t.S[len(t.S)-4:] == "USDT" {
				top = append(top, t.S)
			}
			if len(top) >= 30 {
				break
			}
		}
		symbols = top
	}
}

func loop(every time.Duration) {
	for {
		if len(symbols) == 0 {
			loadSymbols()
		}
		var all []sig
		for _, s := range symbols {
			all = append(all, analyse(s)...)
		}
		if len(all) > 0 {
			mu.Lock()
			prev, _ := cur.Load().([]sig)
			prev = append(prev, all...)
			if len(prev) > 500 {
				prev = prev[len(prev)-500:]
			}
			cur.Store(prev)
			mu.Unlock()
		}
		mu.Lock()
		if len(seen) > 5000 {
			seen = map[string]bool{}
		}
		mu.Unlock()
		time.Sleep(every)
	}
}

func main() {
	addr := flag.String("addr", "127.0.0.1:8792", "listen address")
	every := flag.Duration("every", 5*time.Second, "scan interval")
	flag.Parse()
	log.SetFlags(log.Ltime)
	go loop(*every)

	mux := http.NewServeMux()
	cors := func(w http.ResponseWriter) { w.Header().Set("Access-Control-Allow-Origin", "*") }
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		cors(w)
		mu.Lock()
		n := 0
		if v, ok := cur.Load().([]sig); ok {
			n = len(v)
		}
		out := map[string]any{"ok": true, "service": "tad", "symbols": len(symbols),
			"buffered": n, "cursor": cursor, "interval": every.String()}
		mu.Unlock()
		json.NewEncoder(w).Encode(out)
	})
	mux.HandleFunc("/latest", func(w http.ResponseWriter, r *http.Request) {
		cors(w)
		since, _ := strconv.ParseInt(r.URL.Query().Get("since"), 10, 64)
		mu.Lock()
		list, _ := cur.Load().([]sig)
		out := []sig{}
		for _, s := range list {
			if s.Cursor > since {
				out = append(out, s)
			}
		}
		c := cursor
		mu.Unlock()
		json.NewEncoder(w).Encode(map[string]any{"cursor": c, "signals": out, "buffered": len(list)})
	})
	mux.HandleFunc("/scan", func(w http.ResponseWriter, r *http.Request) {
		cors(w)
		sym := r.URL.Query().Get("symbol")
		if sym == "" {
			w.WriteHeader(400)
			return
		}
		json.NewEncoder(w).Encode(map[string]any{"symbol": sym, "signals": analyseNoDedupe(sym)})
	})
	log.Printf("tad on http://%s scanning %s", *addr, *every)
	log.Fatal(http.ListenAndServe(*addr, mux))
}

// analyseNoDedupe returns the same signals without the one-per-hour dedupe.
func analyseNoDedupe(sym string) []sig {
	bs := klines(sym)
	if len(bs) < 60 {
		return nil
	}
	mu.Lock()
	saved := seen
	seen = map[string]bool{}
	mu.Unlock()
	out := analyse(sym)
	mu.Lock()
	seen = saved
	mu.Unlock()
	return out
}
