// Command scalp runs the trading models (the 9 TA rules + the depth-imbalance
// curve rider) in pure Go, streaming market data over Binance WebSocket.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"math"
	"os"
	"sort"
	"sync"
	"time"

	"pumpdump/backend/internal/binance"
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
	rng := sess[:n]
	hi, lo := rng[0].H, rng[0].L
	for _, b := range rng {
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
	width := 2 * sd / m
	if bs[len(bs)-1].C > m+2*sd && width < 0.01 {
		return "buy"
	}
	if bs[len(bs)-1].C < m-2*sd && width < 0.01 {
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
	"donchian55":   ruleDonchian55,
	"orb":          ruleOrb,
	"boll_squeeze": ruleBollSqueeze,
	"rsi2":         ruleRsi2,
	"vwap_revert":  ruleVwapRevert,
	"macd_trend":   ruleMacd,
	"vol_break":    ruleVolBreak,
	"ema_pullback": ruleEmaPullback,
	"atr_channel":  ruleAtrChannel,
}

type Trade struct {
	Symbol   string  `json:"symbol"`
	Side     string  `json:"side"`
	Entry    float64 `json:"entry"`
	Exit     float64 `json:"exit"`
	GrossPct float64 `json:"gross_pct"`
	NetPct   float64 `json:"net_pct"`
	PeakPct  float64 `json:"peak_pct"`
	Reason   string  `json:"reason"`
	HeldS    float64 `json:"held_s"`
	Notional float64 `json:"notional"`
}

type Position struct {
	Sym      string
	Side     string
	Entry    float64
	T        time.Time
	Notional float64
	Peak     float64
	Why      string
}

type Book struct {
	Name   string
	Start  float64
	Equity float64

	mu     sync.Mutex
	Pos    map[string]*Position
	Trades []Trade
}

func newBook(name string, start float64) *Book {
	return &Book{Name: name, Start: start, Equity: start, Pos: map[string]*Position{}}
}

func (b *Book) open(sym, side string, px float64, notional float64, why string) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if _, ok := b.Pos[sym]; ok {
		return
	}
	b.Pos[sym] = &Position{Sym: sym, Side: side, Entry: px, T: time.Now(), Notional: notional, Why: why}
}

func (b *Book) close(sym string, px, chg float64, reason string) {
	b.mu.Lock()
	defer b.mu.Unlock()
	p, ok := b.Pos[sym]
	if !ok {
		return
	}
	delete(b.Pos, sym)
	net := chg - fee
	b.Equity += p.Notional * net / 100
	b.Trades = append(b.Trades, Trade{Symbol: sym, Side: p.Side, Entry: p.Entry, Exit: px,
		GrossPct: chg, NetPct: net, PeakPct: p.Peak, Reason: reason,
		HeldS: time.Since(p.T).Seconds(), Notional: p.Notional})
}

func (b *Book) manage(px map[string]float64) {
	b.mu.Lock()
	syms := make([]string, 0, len(b.Pos))
	for s := range b.Pos {
		syms = append(syms, s)
	}
	b.mu.Unlock()
	for _, sym := range syms {
		p, ok := px[sym]
		if !ok {
			continue
		}
		b.mu.Lock()
		pos := b.Pos[sym]
		b.mu.Unlock()
		if pos == nil {
			continue
		}
		var chg float64
		if pos.Side == "buy" {
			chg = (p/pos.Entry - 1) * 100
		} else {
			chg = (1 - p/pos.Entry) * 100
		}
		b.mu.Lock()
		if chg > pos.Peak {
			pos.Peak = chg
		}
		peak := pos.Peak
		held := time.Since(pos.T).Seconds()
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
			b.close(sym, p, chg, reason)
			log.Printf("%-14s CLOSE %-11s %-4s %+6.3f%% %s", b.Name, sym, pos.Side, chg, reason)
		}
	}
}

func (b *Book) snapshot() map[string]any {
	b.mu.Lock()
	defer b.mu.Unlock()
	pos := make([]map[string]any, 0, len(b.Pos))
	for _, p := range b.Pos {
		pos = append(pos, map[string]any{"symbol": p.Sym, "side": p.Side, "entry": p.Entry,
			"notional": p.Notional, "entry_ms": p.T.UnixMilli(), "held_s": time.Since(p.T).Seconds(), "why": p.Why})
	}
	return map[string]any{"engine": b.Name, "mode": "paper", "state": "running",
		"equity": math.Round(b.Equity*100) / 100, "start_equity": b.Start,
		"realised": math.Round((b.Equity-b.Start)*100) / 100, "trade_count": len(b.Trades),
		"positions": pos, "trades": b.Trades}
}

type Feed struct {
	mu  sync.Mutex
	imb map[string]float64
	mid map[string]float64
	kl  map[string][]Candle
}

func newFeed(symbols []string, client *binance.Client) *Feed {
	f := &Feed{imb: map[string]float64{}, mid: map[string]float64{}, kl: map[string][]Candle{}}
	for _, s := range symbols {
		if ks, err := client.Klines(context.Background(), s, "1s", 200); err == nil {
			for _, k := range ks {
				f.kl[s] = append(f.kl[s], Candle{T: k.OpenTime, O: k.Open, H: k.High, L: k.Low, C: k.Close, V: k.Volume})
			}
		}
	}
	return f
}

func (f *Feed) handle(stream string, data json.RawMessage) {
	sym := ""
	for i := 0; i < len(stream); i++ {
		if stream[i] == '@' {
			sym = stream[:i]
			break
		}
	}
	if sym == "" {
		return
	}
	sym = upper(sym)
	if contains(stream, "@depth") {
		var d struct {
			Bids [][]string `json:"bids"`
			Asks [][]string `json:"asks"`
		}
		if json.Unmarshal(data, &d) != nil {
			return
		}
		bb, bv, ba, av := 0.0, 0.0, 0.0, 0.0
		for i, x := range d.Bids {
			if len(x) < 2 {
				continue
			}
			if i == 0 {
				bb = parseFloat(x[0])
			}
			bv += parseFloat(x[1])
		}
		for i, x := range d.Asks {
			if len(x) < 2 {
				continue
			}
			if i == 0 {
				ba = parseFloat(x[0])
			}
			av += parseFloat(x[1])
		}
		imb := 0.0
		if bv+av > 0 {
			imb = (bv - av) / (bv + av)
		}
		mid := 0.0
		if bb > 0 && ba > 0 {
			mid = (bb + ba) / 2
		}
		f.mu.Lock()
		f.imb[sym] = imb
		f.mid[sym] = mid
		f.mu.Unlock()
		return
	}
	if contains(stream, "@kline") {
		var d struct {
			K struct {
				T int64  `json:"t"`
				O string `json:"o"`
				H string `json:"h"`
				L string `json:"l"`
				C string `json:"c"`
				V string `json:"v"`
				X bool   `json:"x"`
			} `json:"k"`
		}
		if json.Unmarshal(data, &d) != nil || !d.K.X {
			return
		}
		f.mu.Lock()
		f.kl[sym] = append(f.kl[sym], Candle{T: d.K.T, O: parseFloat(d.K.O), H: parseFloat(d.K.H),
			L: parseFloat(d.K.L), C: parseFloat(d.K.C), V: parseFloat(d.K.V)})
		if len(f.kl[sym]) > 200 {
			f.kl[sym] = f.kl[sym][len(f.kl[sym])-200:]
		}
		f.mu.Unlock()
	}
}

func (f *Feed) snapshot(sym string) (float64, float64) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.imb[sym], f.mid[sym]
}

func (f *Feed) klines(sym string) []Candle {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := make([]Candle, len(f.kl[sym]))
	copy(out, f.kl[sym])
	return out
}

func (f *Feed) best() (string, float64, float64) {
	f.mu.Lock()
	defer f.mu.Unlock()
	best, bestv := "", 0.0
	for s, imb := range f.imb {
		if f.mid[s] > 0 && math.Abs(imb) > math.Abs(bestv) {
			best, bestv = s, imb
		}
	}
	return best, bestv, f.mid[best]
}

func parseFloat(s string) float64 {
	var f float64
	fmt.Sscanf(s, "%f", &f)
	return f
}

func upper(s string) string {
	b := []byte(s)
	for i := range b {
		if b[i] >= 'a' && b[i] <= 'z' {
			b[i] -= 'a' - 'A'
		}
	}
	return string(b)
}

func lower(s string) string {
	b := []byte(s)
	for i := range b {
		if b[i] >= 'A' && b[i] <= 'Z' {
			b[i] += 'a' - 'A'
		}
	}
	return string(b)
}

func contains(s, sub string) bool {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return true
		}
	}
	return false
}

func dumpState(books []*Book, stateDir string) {
	for _, b := range books {
		buf, err := json.Marshal(b.snapshot())
		if err != nil {
			continue
		}
		p := fmt.Sprintf("%s/pack_%s.json", stateDir, b.Name)
		tmp := p + ".t"
		_ = os.WriteFile(tmp, buf, 0o644)
		_ = os.Rename(tmp, p)
	}
}

func main() {
	stateDir := os.Getenv("STATE_DIR")
	if stateDir == "" {
		stateDir = "/Users/users1/Documents/deepseek-harness/default-workspace/scalp5/state"
	}
	client := binance.NewClient("https://api.binance.com")
	ctx := context.Background()

	tickers, err := client.Tickers24h(ctx)
	if err != nil {
		log.Fatalf("universe: %v", err)
	}
	sort.SliceStable(tickers, func(i, j int) bool { return parseFloat(tickers[i].QuoteVolume) > parseFloat(tickers[j].QuoteVolume) })
	var syms []string
	for _, t := range tickers {
		if len(t.Symbol) >= 5 && t.Symbol[len(t.Symbol)-4:] == "USDT" && parseFloat(t.QuoteVolume) > 0 {
			syms = append(syms, t.Symbol)
			if len(syms) >= 90 {
				break
			}
		}
	}
	log.Printf("SCALP — %d symbols, 9 TA books + curve rider", len(syms))

	feed := newFeed(syms, client)
	var streamNames []string
	for _, s := range syms {
		streamNames = append(streamNames, lower(s)+"@depth5@100ms", lower(s)+"@kline_1s")
	}
	stream := binance.NewStream(binance.SpotStreamBase, feed.handle, func(ok bool, n int) {
		log.Printf("stream connected=%v streams=%d", ok, n)
	})
	stream.Subscribe(streamNames...)
	go stream.Run(ctx)

	books := make([]*Book, 0, 10)
	for _, n := range taNames {
		books = append(books, newBook(n, 5000))
	}
	rider := newBook("curve_rider", 5000)
	books = append(books, rider)

	chg := map[string]float64{}
	for _, t := range tickers {
		chg[t.Symbol] = parseFloat(t.PriceChangePercent)
	}

	time.Sleep(3 * time.Second)
	log.Printf("SCALP running")

	for {
		for _, b := range books[:9] {
			px := map[string]float64{}
			b.mu.Lock()
			for sym := range b.Pos {
				if _, mid := feed.snapshot(sym); mid > 0 {
					px[sym] = mid
				}
			}
			b.mu.Unlock()
			b.manage(px)
		}
		{
			rider.mu.Lock()
			empty := len(rider.Pos) == 0
			rider.mu.Unlock()
			if empty {
				sym, imb, mid := feed.best()
				if sym != "" && mid > 0 {
					side := "buy"
					if imb < 0 {
						side = "sell"
					}
					notional := math.Min(rider.Equity*0.2, rider.Equity*0.25)
					rider.open(sym, side, mid, notional, fmt.Sprintf("imb %+.2f 24h %+.1f%%", imb, chg[sym]))
					log.Printf("curve_rider OPEN %-11s %-4s imb %+.2f", sym, side, imb)
				}
			} else {
				px := map[string]float64{}
				rider.mu.Lock()
				for sym := range rider.Pos {
					if _, mid := feed.snapshot(sym); mid > 0 {
						px[sym] = mid
					}
				}
				rider.mu.Unlock()
				rider.manage(px)
			}
		}
		for i, b := range books[:9] {
			b.mu.Lock()
			n := len(b.Pos)
			b.mu.Unlock()
			if n >= 8 {
				continue
			}
			start := i * 10
			if start >= len(syms) {
				continue
			}
			end := start + 10
			if end > len(syms) {
				end = len(syms)
			}
			for _, sym := range syms[start:end] {
				b.mu.Lock()
				_, have := b.Pos[sym]
				n := len(b.Pos)
				b.mu.Unlock()
				if have || n >= 8 {
					continue
				}
				bs := feed.klines(sym)
				if len(bs) < 60 {
					continue
				}
				side := rules[b.Name](bs)
				if side == "" {
					continue
				}
				_, mid := feed.snapshot(sym)
				if mid <= 0 {
					continue
				}
				notional := math.Min(b.Equity*0.1, b.Equity*0.08)
				if side == "sell" {
					notional *= 0.5
				}
				b.open(sym, side, mid, notional, fmt.Sprintf("%s signal", b.Name))
				log.Printf("%-14s OPEN %-11s %-4s", b.Name, sym, side)
				break
			}
		}
		dumpState(books, stateDir)
		time.Sleep(2 * time.Second)
	}
}
