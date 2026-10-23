// Command scalp runs the trading models and engines DECOUPLED over the bus.
//
// Models subscribe to market.candle / market.depth (published by marketdata),
// compute a signal, and publish model.decision. The engine subscribes to
// model.decision, decides whether the signal matters, manages the book, and
// publishes model.fill. Nothing is coupled: any model can publish, any engine
// can listen.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"math"
	"net/http"
	"os"
	"sync"
	"sync/atomic"
	"time"

	"cryptalyst/core/internal/stomp"
	"cryptalyst/core/internal/topics"
)

const (
	lockPct  = 0.35
	trailArm = 1.0
	trail    = 0.5
	stopPct  = 0.5
	clockS   = 180.0
	fee      = 0.2
)

var taNames = []string{"donchian55", "orb", "boll_squeeze", "rsi2", "vwap_revert",
	"macd_trend", "vol_break", "ema_pullback", "atr_channel"}

// ---- candle + TA helpers ----

type Candle struct {
	T int64
	O float64
	H float64
	L float64
	C float64
	V float64
}

func ema(v []float64, n int) float64 {
	if len(v) == 0 {
		return 0
	}
	k := 2.0 / float64(n+1)
	e := v[0]
	for _, x := range v[1:] {
		e = x*k + e*(1-k)
	}
	return e
}

func rsi(cl []float64, n int) float64 {
	if len(cl) < n+1 {
		return 50
	}
	g, l := 0.0, 0.0
	for i := len(cl) - n; i < len(cl); i++ {
		ch := cl[i] - cl[i-1]
		if ch > 0 {
			g += ch
		} else {
			l -= ch
		}
	}
	if g == 0 && l == 0 {
		return 50
	}
	if l == 0 {
		return 100
	}
	return 100 - 100/(1+(g/float64(n))/(l/float64(n)))
}

func atr(bs []Candle, n int) float64 {
	if len(bs) <= n {
		return 0
	}
	s, c := 0.0, 0
	for i := len(bs) - n; i < len(bs); i++ {
		tr := math.Max(bs[i].H-bs[i].L, math.Max(math.Abs(bs[i].H-bs[i-1].C), math.Abs(bs[i-1].C-bs[i].L)))
		s += tr
		c++
	}
	if c == 0 {
		return 0
	}
	return s / float64(c)
}

func closes(bs []Candle) []float64 {
	out := make([]float64, 0, len(bs))
	for _, b := range bs {
		out = append(out, b.C)
	}
	return out
}

type rule func([]Candle) string

func ruleDonchian55(bs []Candle) string {
	if len(bs) < 55 {
		return ""
	}
	hi, lo := bs[len(bs)-55].H, bs[len(bs)-55].L
	for _, b := range bs[len(bs)-55:] {
		if b.H > hi {
			hi = b.H
		}
		if b.L < lo {
			lo = b.L
		}
	}
	if hi == lo {
		return ""
	}
	c := bs[len(bs)-1].C
	if c >= hi {
		return "buy"
	}
	if c <= lo {
		return "sell"
	}
	return ""
}

func ruleOrb(bs []Candle) string {
	if len(bs) < 20 {
		return ""
	}
	day := time.Now().UTC().Format("2006-01-02")
	var sess []Candle
	for _, b := range bs {
		if time.UnixMilli(b.T).UTC().Format("2006-01-02") == day {
			sess = append(sess, b)
		}
	}
	if len(sess) < 20 {
		return ""
	}
	n := 15
	if len(sess) < n {
		n = len(sess)
	}
	hi, lo := sess[0].H, sess[0].L
	for _, b := range sess[:n] {
		if b.H > hi {
			hi = b.H
		}
		if b.L < lo {
			lo = b.L
		}
	}
	c := bs[len(bs)-1].C
	if c > hi {
		return "buy"
	}
	if c < lo {
		return "sell"
	}
	return ""
}

func ruleBollSqueeze(bs []Candle) string {
	if len(bs) < 20 {
		return ""
	}
	cl := closes(bs[len(bs)-20:])
	m := 0.0
	for _, x := range cl {
		m += x
	}
	m /= float64(len(cl))
	if m == 0 {
		return ""
	}
	sd := 0.0
	for _, x := range cl {
		sd += (x - m) * (x - m)
	}
	sd = math.Sqrt(sd / float64(len(cl)))
	if sd == 0 {
		return ""
	}
	if bs[len(bs)-1].C > m+2*sd && 2*sd/m < 0.01 {
		return "buy"
	}
	if bs[len(bs)-1].C < m-2*sd && 2*sd/m < 0.01 {
		return "sell"
	}
	return ""
}

func ruleRsi2(bs []Candle) string {
	cl := closes(bs)
	if len(cl) < 3 {
		return ""
	}
	if len(cl) > 20 {
		cl = cl[len(cl)-20:]
	}
	r := rsi(cl, 2)
	if r < 10 {
		return "buy"
	}
	if r > 90 {
		return "sell"
	}
	return ""
}

func ruleVwapRevert(bs []Candle) string {
	if len(bs) < 30 {
		return ""
	}
	pv, vv := 0.0, 0.0
	for _, b := range bs[len(bs)-30:] {
		pv += b.C * b.V
		vv += b.V
	}
	if vv <= 0 {
		return ""
	}
	vw := pv / vv
	c := bs[len(bs)-1].C
	if c < vw*0.994 {
		return "buy"
	}
	if c > vw*1.006 {
		return "sell"
	}
	return ""
}

func ruleMacd(bs []Candle) string {
	cl := closes(bs)
	if len(cl) < 61 {
		return ""
	}
	fast := ema(cl[len(cl)-40:], 12)
	slow := ema(cl[len(cl)-60:], 26)
	hist := fast - slow
	prev := ema(cl[len(cl)-41:len(cl)-1], 12) - ema(cl[len(cl)-61:len(cl)-1], 26)
	if prev <= 0 && hist > 0 {
		return "buy"
	}
	if prev >= 0 && hist < 0 {
		return "sell"
	}
	return ""
}

func ruleVolBreak(bs []Candle) string {
	if len(bs) < 20 {
		return ""
	}
	avg := 0.0
	for _, b := range bs[len(bs)-20 : len(bs)-1] {
		avg += b.V
	}
	avg /= 19
	b := bs[len(bs)-1]
	if avg <= 0 || b.V < 3*avg {
		return ""
	}
	rng := b.H - b.L
	if rng <= 0 {
		return ""
	}
	if (b.C-b.L)/rng > 0.7 {
		return "buy"
	}
	if (b.H-b.C)/rng > 0.7 {
		return "sell"
	}
	return ""
}

func ruleEmaPullback(bs []Candle) string {
	cl := closes(bs)
	if len(cl) < 80 {
		return ""
	}
	e20 := ema(cl[len(cl)-40:], 20)
	e50 := ema(cl[len(cl)-80:], 50)
	c := cl[len(cl)-1]
	if e20 > e50 && c > e50 && math.Abs(c-e20)/e20 < 0.002 {
		return "buy"
	}
	if e20 < e50 && c < e50 && math.Abs(c-e20)/e20 < 0.002 {
		return "sell"
	}
	return ""
}

func ruleAtrChannel(bs []Candle) string {
	cl := closes(bs)
	if len(cl) < 40 {
		return ""
	}
	m := ema(cl[len(cl)-40:], 20)
	a := atr(bs, 14)
	c := cl[len(cl)-1]
	if a <= 0 {
		return ""
	}
	if c > m+1.5*a {
		return "buy"
	}
	if c < m-1.5*a {
		return "sell"
	}
	return ""
}

var rules = map[string]rule{
	"donchian55": ruleDonchian55, "orb": ruleOrb, "boll_squeeze": ruleBollSqueeze,
	"rsi2": ruleRsi2, "vwap_revert": ruleVwapRevert, "macd_trend": ruleMacd,
	"vol_break": ruleVolBreak, "ema_pullback": ruleEmaPullback, "atr_channel": ruleAtrChannel,
}

// ---- bus payloads ----

type Decision struct {
	Symbol   string  `json:"symbol"`
	Side     string  `json:"side"`
	Model    string  `json:"model"`
	Score    float64 `json:"score"`
	TSMillis int64   `json:"ts_ms"`
	Why      string  `json:"why"`
}

type Fill struct {
	Model    string  `json:"model"`
	Symbol   string  `json:"symbol"`
	Side     string  `json:"side"`
	Reason   string  `json:"reason"`
	NetPct   float64 `json:"net_pct"`
	PnLUSD   float64 `json:"pnl_usd"`
	TSMillis int64   `json:"ts_ms"`
}

// ---- shared market cache (fed by the bus) ----

type Market struct {
	mu  sync.Mutex
	kl  map[string][]Candle
	imb map[string]float64
	mid map[string]float64
}

func newMarket() *Market {
	return &Market{kl: map[string][]Candle{}, imb: map[string]float64{}, mid: map[string]float64{}}
}

func (m *Market) candle(sym string, c Candle) {
	m.mu.Lock()
	m.kl[sym] = append(m.kl[sym], c)
	if len(m.kl[sym]) > 200 {
		m.kl[sym] = m.kl[sym][len(m.kl[sym])-200:]
	}
	m.mu.Unlock()
}

func (m *Market) depth(sym string, imbalance, bid, ask float64) {
	m.mu.Lock()
	m.imb[sym] = imbalance
	if bid > 0 && ask > 0 {
		m.mid[sym] = (bid + ask) / 2
	}
	m.mu.Unlock()
}

func (m *Market) klines(sym string) []Candle {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]Candle, len(m.kl[sym]))
	copy(out, m.kl[sym])
	return out
}

func (m *Market) imbalance(sym string) float64 {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.imb[sym]
}

func (m *Market) price(sym string) float64 {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.mid[sym]
}

func (m *Market) bestImb() (string, float64, float64) {
	m.mu.Lock()
	defer m.mu.Unlock()
	best, bv := "", 0.0
	for s, imb := range m.imb {
		if m.mid[s] > 0 && math.Abs(imb) > math.Abs(bv) {
			best, bv = s, imb
		}
	}
	return best, bv, m.mid[best]
}

// ---- book (the engine's state) ----

type Position struct {
	Sym      string
	Side     string
	Entry    float64
	T        time.Time
	Notional float64
	Peak     float64
}

type Book struct {
	Name   string
	Start  float64
	Equity float64
	mu     sync.Mutex
	Pos    map[string]*Position
	Trades []map[string]any
}

func newBook(name string) *Book {
	return &Book{Name: name, Start: 5000, Equity: 5000, Pos: map[string]*Position{}}
}

func (b *Book) open(sym, side string, px float64) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if _, ok := b.Pos[sym]; ok {
		return
	}
	notional := math.Min(b.Equity*0.1, b.Equity*0.08)
	if side == "sell" {
		notional *= 0.5
	}
	b.Pos[sym] = &Position{Sym: sym, Side: side, Entry: px, T: time.Now(), Notional: notional}
}

func (b *Book) close(sym string, px, chg float64, reason string) (float64, bool) {
	b.mu.Lock()
	defer b.mu.Unlock()
	p, ok := b.Pos[sym]
	if !ok {
		return 0, false
	}
	delete(b.Pos, sym)
	net := chg - fee
	pnl := p.Notional * net / 100
	b.Equity += pnl
	b.Trades = append(b.Trades, map[string]any{"symbol": sym, "side": p.Side, "reason": reason,
		"net_pct": net, "pnl_usd": pnl})
	return pnl, true
}

func (b *Book) manage(m *Market) []Fill {
	b.mu.Lock()
	syms := make([]string, 0, len(b.Pos))
	for s := range b.Pos {
		syms = append(syms, s)
	}
	b.mu.Unlock()
	var fills []Fill
	for _, sym := range syms {
		px := m.price(sym)
		if px <= 0 {
			continue
		}
		b.mu.Lock()
		p := b.Pos[sym]
		b.mu.Unlock()
		if p == nil {
			continue
		}
		var chg float64
		if p.Side == "buy" {
			chg = (px/p.Entry - 1) * 100
		} else {
			chg = (1 - px/p.Entry) * 100
		}
		b.mu.Lock()
		if chg > p.Peak {
			p.Peak = chg
		}
		peak, held := p.Peak, time.Since(p.T).Seconds()
		b.mu.Unlock()
		reason := ""
		switch {
		case chg >= lockPct:
			reason = "lock"
		case peak >= trailArm && chg <= peak-trail:
			reason = "trail"
		case chg <= -stopPct:
			reason = "stop"
		case held >= clockS && chg > 0:
			reason = "clock"
		}
		if reason != "" {
			if pnl, ok := b.close(sym, px, chg, reason); ok {
				fills = append(fills, Fill{Model: b.Name, Symbol: sym, Side: p.Side,
					Reason: reason, NetPct: chg - fee, PnLUSD: pnl, TSMillis: time.Now().UnixMilli()})
			}
		}
	}
	return fills
}

func (b *Book) snapshot() map[string]any {
	b.mu.Lock()
	defer b.mu.Unlock()
	pos := make([]map[string]any, 0, len(b.Pos))
	for _, p := range b.Pos {
		pos = append(pos, map[string]any{"symbol": p.Sym, "side": p.Side, "entry": p.Entry,
			"notional": p.Notional, "held_s": time.Since(p.T).Seconds()})
	}
	return map[string]any{"engine": b.Name, "equity": math.Round(b.Equity*100) / 100,
		"start_equity": b.Start, "trade_count": len(b.Trades), "positions": pos, "trades": b.Trades}
}

func backfill(m *Market) {
	uni, err := http.Get("http://127.0.0.1:8799/api/universe800")
	if err != nil {
		return
	}
	var u struct {
		Tickers []struct {
			S string `json:"s"`
		} `json:"tickers"`
	}
	body, _ := io.ReadAll(uni.Body)
	uni.Body.Close()
	if json.Unmarshal(body, &u) != nil {
		return
	}
	var syms []string
	for _, t := range u.Tickers {
		if len(t.S) >= 5 && t.S[len(t.S)-4:] == "USDT" {
			syms = append(syms, t.S)
			if len(syms) >= 90 {
				break
			}
		}
	}
	for _, sym := range syms {
		resp, err := http.Get(fmt.Sprintf("https://api.binance.com/api/v3/klines?symbol=%s&interval=1m&limit=200", sym))
		if err != nil {
			continue
		}
		raw, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		var ks [][]any
		if json.Unmarshal(raw, &ks) != nil {
			continue
		}
		for _, k := range ks {
			if len(k) < 6 {
				continue
			}
			f := func(i int) float64 {
				if v, ok := k[i].(float64); ok {
					return v
				}
				return 0
			}
			m.candle(sym, Candle{T: int64(k[0].(float64)), O: f(1), H: f(2), L: f(3), C: f(4), V: f(5)})
		}
	}
	log.Printf("backfilled 1m candles for %d symbols", len(syms))
}

// History is the book's intel: recent signals per model+symbol, in a bounded
// rolling window. Models confirm a pattern (several agreeing signals) before the
// strategy is allowed to trade. It never holds too much — entries expire.
type History struct {
	mu     sync.Mutex
	window time.Duration
	seen   map[string][]int64 // key "model|symbol|side" -> epoch-ms stamps
}

func newHistory(window time.Duration) *History {
	return &History{window: window, seen: map[string][]int64{}}
}

// confirm records the signal and reports whether the model has now seen enough
// agreeing signals inside the window to be confident (a pattern, not a lone blip).
func (h *History) confirm(model, symbol, side string, need int) bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	key := model + "|" + symbol + "|" + side
	now := time.Now().UnixMilli()
	cut := now - h.window.Milliseconds()
	kept := h.seen[key][:0]
	for _, ts := range h.seen[key] {
		if ts >= cut {
			kept = append(kept, ts)
		}
	}
	kept = append(kept, now)
	h.seen[key] = kept
	// drop expired keys so the store stays bounded (never too much data)
	if len(h.seen) > 5000 {
		for k, v := range h.seen {
			if len(v) == 0 {
				delete(h.seen, k)
			}
		}
	}
	return len(kept) >= need
}

// ---- main ----

func main() {
	addr := flag.String("stomp", "127.0.0.1:61613", "broker address")
	stateDir := flag.String("state", "/Users/users1/Documents/deepseek-harness/default-workspace/scalp5/state", "state dir")
	flag.Parse()
	log.SetFlags(log.Ltime)

	cl := stomp.New(*addr, "cryptalyst", "cryptalyst")
	for !cl.Healthy() {
		if err := cl.Connect(); err != nil {
			log.Printf("bus connect: %v", err)
			time.Sleep(2 * time.Second)
		}
	}
	log.Printf("SCALP on the bus (models publish model.decision, engine listens)")

	m := newMarket()
	var candles, depths int64

	// models subscribe to the market data published by marketdata
	candleSpec, _ := topics.Get("market.candle")
	depthSpec, _ := topics.Get("market.depth")
	_ = cl.SubscribeTopic(candleSpec.Exchange, candleSpec.Name, func(msg stomp.Message) {
		var d struct {
			Symbol        string  `json:"symbol"`
			T             int64   `json:"t"`
			O, H, L, C, V float64 `json:"o"`
		}
		var raw struct {
			Symbol string  `json:"symbol"`
			T      int64   `json:"t"`
			O      float64 `json:"o"`
			H      float64 `json:"h"`
			L      float64 `json:"l"`
			C      float64 `json:"c"`
			V      float64 `json:"v"`
		}
		_ = d
		if json.Unmarshal(msg.Body, &raw) == nil && raw.Symbol != "" {
			m.candle(raw.Symbol, Candle{T: raw.T, O: raw.O, H: raw.H, L: raw.L, C: raw.C, V: raw.V})
		}
	})
	_ = cl.SubscribeTopic(depthSpec.Exchange, depthSpec.Name, func(msg stomp.Message) {
		var d struct {
			Symbol    string  `json:"symbol"`
			Imbalance float64 `json:"imbalance"`
			Bids      []struct {
				Price float64 `json:"price"`
			} `json:"bids"`
			Asks []struct {
				Price float64 `json:"price"`
			} `json:"asks"`
		}
		if json.Unmarshal(msg.Body, &d) == nil && d.Symbol != "" {
			bb, ba := 0.0, 0.0
			if len(d.Bids) > 0 {
				bb = d.Bids[0].Price
			}
			if len(d.Asks) > 0 {
				ba = d.Asks[0].Price
			}
			m.depth(d.Symbol, d.Imbalance, bb, ba)
			atomic.AddInt64(&depths, 1)
		}
	})

	// backfill 1m candle history (the bus carries only newly-closed candles; no backlog)
	backfill(m)

	// engine books
	books := map[string]*Book{}
	for _, n := range taNames {
		books[n] = newBook(n)
	}
	books["curve_rider"] = newBook("curve_rider")

	decisionSpec, _ := topics.Get("model.decision")
	fillSpec, _ := topics.Get("model.fill")
	history := newHistory(90 * time.Second) // the intel window the models define
	published := 0

	go func() {
		for {
			time.Sleep(15 * time.Second)
			log.Printf("recv candles=%d depths=%d", atomic.LoadInt64(&candles), atomic.LoadInt64(&depths))
		}
	}()

	// model publishers: compute signals -> publish model.decision (async goroutine)
	go func() {
		ticks := 0
		for {
			time.Sleep(2 * time.Second)
			ticks++
			// depth-imbalance signal (curve rider)
			if sym, imb, mid := m.bestImb(); sym != "" && mid > 0 && math.Abs(imb) > 0.3 {
				side := "buy"
				if imb < 0 {
					side = "sell"
				}
				d := Decision{Symbol: sym, Side: side, Model: "curve_rider", Score: imb, TSMillis: time.Now().UnixMilli(),
					Why: fmt.Sprintf("imbalance %+.2f", imb)}
				if body, err := json.Marshal(d); err == nil {
					_ = cl.Publish(decisionSpec.Exchange, decisionSpec.Name, body)
					published++
				}
			}
			// TA signals (each model scans its own basket)
			for i, name := range taNames {
				// basket = every 10th symbol slice; here just scan the cached symbols
				for sym := range m.kl {
					if m.price(sym) <= 0 {
						continue
					}
					bs := m.klines(sym)
					if len(bs) < 60 {
						continue
					}
					side := rules[name](bs)
					if side == "" {
						continue
					}
					d := Decision{Symbol: sym, Side: side, Model: name, TSMillis: time.Now().UnixMilli(), Why: name + " signal"}
					if body, err := json.Marshal(d); err == nil {
						_ = cl.Publish(decisionSpec.Exchange, decisionSpec.Name, body)
						published++
						if published%20 == 1 {
							log.Printf("published %d decisions", published)
						}
					}
					_ = i
					break
				}
			}
		}
	}()

	// engine: subscribe to model.decision and decide
	_ = cl.SubscribeTopic(decisionSpec.Exchange, decisionSpec.Name, func(msg stomp.Message) {
		var d Decision
		if json.Unmarshal(msg.Body, &d) != nil || d.Symbol == "" || d.Model == "" {
			return
		}
		b, ok := books[d.Model]
		if !ok {
			return
		}
		// the model must see a pattern (3 agreeing signals in the window) before
		// the strategy is allowed to trade — a lone signal is not enough.
		if !history.confirm(d.Model, d.Symbol, d.Side, 3) {
			return
		}
		if d.Model == "curve_rider" {
			b.mu.Lock()
			empty := len(b.Pos) == 0
			b.mu.Unlock()
			if empty {
				if px := m.price(d.Symbol); px > 0 {
					b.open(d.Symbol, d.Side, px)
					log.Printf("curve_rider OPEN %s %s", d.Symbol, d.Side)
				}
			}
			return
		}
		b.mu.Lock()
		_, have := b.Pos[d.Symbol]
		n := len(b.Pos)
		b.mu.Unlock()
		if have || n >= 8 {
			return
		}
		if px := m.price(d.Symbol); px > 0 {
			b.open(d.Symbol, d.Side, px)
			log.Printf("%-14s OPEN %s %s", d.Model, d.Symbol, d.Side)
		}
	})

	// manage loop: close positions on the exit ladder + publish fills + dump state
	for {
		for _, b := range books {
			for _, f := range b.manage(m) {
				if body, err := json.Marshal(f); err == nil {
					_ = cl.Publish(fillSpec.Exchange, fillSpec.Name, body)
					log.Printf("%-14s CLOSE %s %s %+.3f%% %s", f.Model, f.Symbol, f.Side, f.NetPct, f.Reason)
				}
			}
		}
		for _, b := range books {
			if body, err := json.Marshal(b.snapshot()); err == nil {
				p := fmt.Sprintf("%s/pack_%s.json", *stateDir, b.Name)
				tmp := p + ".t"
				_ = os.WriteFile(tmp, body, 0o644)
				_ = os.Rename(tmp, p)
			}
		}
		time.Sleep(2 * time.Second)
	}
}
