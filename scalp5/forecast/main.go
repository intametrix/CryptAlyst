// forecast - research, price and volume agents with a supervising blender.
//
//	GET /forecast?symbol=BTCUSDT&side=buy&horizon=300
//	GET /views?symbol=BTCUSDT
//	GET /health
//
// Each agent looks at the same 1m candle series from a different angle and
// returns a forward estimate plus a probability. The supervisor weights them by
// their own recent hit rate and hands the models one consensus view.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"math"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strconv"
	"sync"
	"time"
)

type bar struct {
	T             int64
	O, H, L, C, V float64
}

type view struct {
	Agent      string  `json:"agent"`
	Side       string  `json:"side"`
	Prob       float64 `json:"prob"`       // probability the trade direction is right
	MovePct    float64 `json:"move_pct"`   // expected move over the horizon
	Confidence float64 `json:"confidence"` // 0..1
	Note       string  `json:"note"`
}

type forecast struct {
	Symbol     string  `json:"symbol"`
	Side       string  `json:"side"`
	HorizonSec int     `json:"horizon_s"`
	Prob       float64 `json:"prob"`
	MovePct    float64 `json:"move_pct"`
	Confidence float64 `json:"confidence"`
	Action     string  `json:"action"` // enter | hold | take_profit | liquidate
	Resolution string  `json:"resolution"`
	Views      []view  `json:"views"`
	Price      float64 `json:"price"`
	BarAgeSec  float64 `json:"bar_age_s"`
	TS         int64   `json:"ts"`
}

// MIN_BARS is the warm-up floor. The agents read rolling windows (10/20/30/50),
// and below this a window slice would index past the front of the series and
// panic (e.g. cl[n-50:] with n=40). One guard, everywhere.
const MIN_BARS = 50

// neutral is the guarded answer while the series is still warming up.
func neutral(agent, side string) view {
	return view{Agent: agent, Side: side, Prob: 0.5, MovePct: 0, Confidence: 0,
		Note: "warm-up: not enough bars"}
}

var (
	mu     sync.Mutex
	hits   = map[string][2]int{} // agent -> {correct, total}
	cache  = map[string]cached{}
	client = &http.Client{Timeout: 6 * time.Second}
)

type cached struct {
	at   time.Time
	bars []bar
}

// fetchAt reads bars at one resolution.
func fetchAt(symbol, interval string, limit int) ([]bar, error) {
	mu.Lock()
	if c, ok := cache[interval+":"+symbol]; ok && time.Since(c.at) < 8*time.Second {
		mu.Unlock()
		return c.bars, nil
	}
	mu.Unlock()

	u := fmt.Sprintf("http://127.0.0.1:8796/klines?symbol=%s&interval=%s&limit=%d",
		url.QueryEscape(symbol), interval, limit)
	resp, err := client.Get(u)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("upstream %d", resp.StatusCode)
	}
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	var env struct {
		Klines []struct {
			T int64   `json:"t"`
			O float64 `json:"o"`
			H float64 `json:"h"`
			L float64 `json:"l"`
			C float64 `json:"c"`
			V float64 `json:"v"`
		} `json:"klines"`
	}
	if err := json.Unmarshal(body, &env); err != nil {
		return nil, err
	}
	out := make([]bar, 0, len(env.Klines))
	for _, r := range env.Klines {
		out = append(out, bar{T: r.T, O: r.O, H: r.H, L: r.L, C: r.C, V: r.V})
	}
	_ = strconv.Itoa
	mu.Lock()
	cache[interval+":"+symbol] = cached{at: time.Now(), bars: out}
	mu.Unlock()
	return out, nil
}

func ema(v []float64, n int) float64 {
	k := 2.0 / float64(n+1)
	e := v[0]
	for _, x := range v[1:] {
		e = x*k + e*(1-k)
	}
	return e
}

func atr(bs []bar, n int) float64 {
	if len(bs) < 2 {
		return 0
	}
	sum, cnt := 0.0, 0
	for i := len(bs) - n; i < len(bs); i++ {
		if i < 1 {
			continue
		}
		tr := math.Max(bs[i].H-bs[i].L,
			math.Max(math.Abs(bs[i].H-bs[i-1].C), math.Abs(bs[i].L-bs[i-1].C)))
		sum += tr
		cnt++
	}
	if cnt == 0 {
		return 0
	}
	return sum / float64(cnt)
}

func stdev(v []float64) float64 {
	if len(v) < 2 {
		return 0
	}
	m := 0.0
	for _, x := range v {
		m += x
	}
	m /= float64(len(v))
	s := 0.0
	for _, x := range v {
		s += (x - m) * (x - m)
	}
	return math.Sqrt(s / float64(len(v)))
}

// ---- agents -------------------------------------------------------------

// momentum: EMA slope plus recent return, direction-aware probability.
func agentMomentum(bs []bar, side string) view {
	if len(bs) < MIN_BARS {
		return neutral("momentum", side)
	}

	cl := make([]float64, len(bs))
	for i, b := range bs {
		cl[i] = b.C
	}
	e9, e21 := ema(cl[len(cl)-40:], 9), ema(cl[len(cl)-40:], 21)
	slope := (e9 - e21) / e21 * 100
	ret := (cl[len(cl)-1]/cl[len(cl)-8] - 1) * 100
	raw := slope*0.6 + ret*0.4
	prob := 1 / (1 + math.Exp(-raw*8))
	if side == "sell" {
		prob = 1 - prob
	}
	return view{Agent: "momentum", Side: side, Prob: prob,
		MovePct: math.Abs(raw), Confidence: math.Min(1, math.Abs(raw)/0.5),
		Note: fmt.Sprintf("ema9-21 %.3f%%, 15m ret %.3f%%", slope, ret)}
}

// volume: is participation confirming the move or fading it.
func agentVolume(bs []bar, side string) view {
	if len(bs) < MIN_BARS {
		return neutral("volume", side)
	}

	n := len(bs)
	recent, prior := 0.0, 0.0
	for i := n - 10; i < n; i++ {
		recent += bs[i].V
	}
	for i := n - 40; i < n-10; i++ {
		prior += bs[i].V
	}
	recent /= 10
	prior /= 30
	ratio := 1.0
	if prior > 0 {
		ratio = recent / prior
	}
	up := bs[n-1].C >= bs[n-10].C
	confirms := (ratio > 1.1 && ((up && side == "buy") || (!up && side == "sell")))
	prob := 0.5
	if confirms {
		prob = 0.5 + math.Min(0.35, (ratio-1)*0.5)
	} else if ratio < 0.8 {
		prob = 0.45
	}
	return view{Agent: "volume", Side: side, Prob: prob,
		MovePct: 0, Confidence: math.Min(1, math.Abs(ratio-1)),
		Note: fmt.Sprintf("volume %.2fx prior 30m", ratio)}
}

// volatility: expected range vs the stop/target distances we actually use.
func agentVolatility(bs []bar, side string, horizon int) view {
	if len(bs) < MIN_BARS {
		return neutral("volatility", side)
	}

	a := atr(bs, 14)
	px := bs[len(bs)-1].C
	atrPct := 0.0
	if px > 0 {
		atrPct = a / px * 100
	}
	// expected move scales with sqrt(time)
	exp := atrPct * math.Sqrt(float64(horizon)/900.0)
	prob := 0.5
	conf := 0.3
	if exp > 0.35 {
		prob = 0.6
		conf = math.Min(1, exp/1.0)
	} else if exp < 0.15 {
		prob = 0.42
		conf = 0.5
	}
	return view{Agent: "volatility", Side: side, Prob: prob, MovePct: exp,
		Confidence: conf,
		Note:       fmt.Sprintf("atr %.3f%%, expected %.3f%% over %ds", atrPct, exp, horizon)}
}

// reversion: stretched from the mean -> the move is more likely to stall.
func agentReversion(bs []bar, side string) view {
	if len(bs) < MIN_BARS {
		return neutral("reversion", side)
	}

	n := len(bs)
	cl := make([]float64, n)
	for i, b := range bs {
		cl[i] = b.C
	}
	window := bs[n-30:]
	pv, vol := 0.0, 0.0
	for _, b := range window {
		pv += b.C * b.V
		vol += b.V
	}
	vw := 0.0
	if vol > 0 {
		vw = pv / vol
	}
	dev := 0.0
	if vw > 0 {
		dev = (cl[n-1] - vw) / vw * 100
	}
	prob := 0.5
	switch {
	case dev > 0.6:
		prob = 0.4 // stretched above the mean
	case dev < -0.6:
		prob = 0.6
	}
	if side == "sell" {
		prob = 1 - prob
	}
	return view{Agent: "reversion", Side: side, Prob: prob, MovePct: math.Abs(dev),
		Confidence: math.Min(1, math.Abs(dev)/1.2),
		Note:       fmt.Sprintf("%.3f%% from 30m vwap", dev)}
}

// ma: classic moving-average structure (20/50 plus price vs them).
func agentMA(bs []bar, side string) view {
	if len(bs) < MIN_BARS {
		return neutral("ma", side)
	}

	n := len(bs)
	cl := make([]float64, n)
	for i, b := range bs {
		cl[i] = b.C
	}
	sma := func(k int) float64 {
		sum := 0.0
		for _, x := range cl[n-k:] {
			sum += x
		}
		return sum / float64(k)
	}
	m20, m50 := sma(20), sma(50)
	gap := (m20 - m50) / m50 * 100
	above := cl[n-1] > m20
	prob := 0.5
	if (gap > 0 && above) || (gap < 0 && !above) {
		prob = 0.5 + math.Min(0.3, math.Abs(gap)*0.4)
	}
	if side == "sell" {
		prob = 1 - prob
	}
	return view{Agent: "ma", Side: side, Prob: prob, MovePct: math.Abs(gap),
		Confidence: math.Min(1, math.Abs(gap)/0.8),
		Note:       fmt.Sprintf("sma20-sma50 %.3f%%, price %s sma20", gap, map[bool]string{true: "above", false: "below"}[above])}
}

// breakout: distance to the 20-bar range edge, the level a move must clear.
func agentBreakout(bs []bar, side string) view {
	if len(bs) < MIN_BARS {
		return neutral("breakout", side)
	}

	n := len(bs)
	hi, lo := bs[n-20].H, bs[n-20].L
	for _, b := range bs[n-20:] {
		if b.H > hi {
			hi = b.H
		}
		if b.L < lo {
			lo = b.L
		}
	}
	px := bs[n-1].C
	rng := hi - lo
	pos := 0.5
	if rng > 0 {
		pos = (px - lo) / rng
	}
	prob := 0.5
	move := 0.0
	if side == "buy" {
		move = (hi - px) / px * 100
		if pos > 0.8 {
			prob = 0.62 // pressing the top of the range
		} else if pos < 0.25 {
			prob = 0.45
		}
	} else {
		move = (px - lo) / px * 100
		if pos < 0.2 {
			prob = 0.62
		} else if pos > 0.75 {
			prob = 0.45
		}
	}
	return view{Agent: "breakout", Side: side, Prob: prob, MovePct: math.Abs(move),
		Confidence: math.Abs(pos-0.5) * 2,
		Note:       fmt.Sprintf("%.0f%% of 20-bar range, %.3f%% to the edge", pos*100, math.Abs(move))}
}

// pressure: where each bar closes inside its own range, averaged.
func agentPressure(bs []bar, side string) view {
	if len(bs) < MIN_BARS {
		return neutral("pressure", side)
	}

	n := len(bs)
	sum := 0.0
	cnt := 0
	for _, b := range bs[n-10:] {
		if b.H > b.L {
			sum += (b.C - b.L) / (b.H - b.L)
			cnt++
		}
	}
	p := 0.5
	if cnt > 0 {
		p = sum / float64(cnt)
	}
	prob := 0.5 + (p-0.5)*0.6
	if side == "sell" {
		prob = 1 - prob
	}
	return view{Agent: "pressure", Side: side, Prob: prob, MovePct: 0,
		Confidence: math.Abs(p-0.5) * 2,
		Note:       fmt.Sprintf("avg close in range %.2f over 10 bars", p)}
}

// ---- supervisor ---------------------------------------------------------

func blend(symbol, side string, horizon int) (forecast, error) {
	bs, err := fetchAt(symbol, "15m", 200)
	resolution := "15m"
	if err != nil || len(bs) < 40 {
		// a young process has few 15m bars; 1m bars carry the same information at
		// a finer step, and the horizon is scaled to match
		alt, aerr := fetchAt(symbol, "1m", 200)
		if aerr != nil {
			if err != nil {
				return forecast{}, err
			}
			return forecast{}, aerr
		}
		if len(alt) < 40 {
			return forecast{}, fmt.Errorf("not enough bars")
		}
		bs, resolution = alt, "1m"
		horizon = horizon / 15
		if horizon < 1 {
			horizon = 1
		}
	}
	vs := []view{
		agentMomentum(bs, side),
		agentVolume(bs, side),
		agentVolatility(bs, side, horizon),
		agentReversion(bs, side),
		agentMA(bs, side),
		agentBreakout(bs, side),
		agentPressure(bs, side),
	}

	// weight each agent by its historical hit rate, with a neutral prior
	mu.Lock()
	wsum, psum, msum, csum, mw := 0.0, 0.0, 0.0, 0.0, 0.0
	for i := range vs {
		h := hits[vs[i].Agent]
		acc := 0.5
		if h[1] >= 5 {
			acc = float64(h[0]) / float64(h[1])
		}
		w := 0.5 + acc // 0.5..1.5
		wsum += w
		psum += vs[i].Prob * w
		if vs[i].MovePct > 0 { // agents that estimate magnitude only
			msum += vs[i].MovePct * w
			mw += w
		}
		csum += vs[i].Confidence * w
	}
	mu.Unlock()
	if wsum == 0 {
		wsum = 1
	}
	if mw == 0 {
		mw = 1
	}
	f := forecast{
		Symbol: symbol, Side: side, HorizonSec: horizon,
		Prob: psum / wsum, MovePct: msum / mw, Confidence: csum / wsum,
		Views: vs, Price: bs[len(bs)-1].C, Resolution: resolution,
		BarAgeSec: time.Since(time.UnixMilli(bs[len(bs)-1].T)).Seconds(),
		TS:        time.Now().UnixMilli(),
	}
	// the supervisor's rule: only recommend acting when the blend is decisive
	switch {
	case f.Prob >= 0.58 && f.MovePct >= 0.25:
		f.Action = "enter"
	case f.Prob <= 0.42:
		f.Action = "liquidate"
	case f.Prob >= 0.52:
		f.Action = "hold"
	default:
		f.Action = "hold"
	}
	return f, nil
}

// publishLoop keeps intel.forecast moving without anyone asking for it: walk the
// streamed symbols, blend, publish. One symbol per tick so the loop never blocks
// the HTTP handlers.
func publishLoop(every time.Duration) {
	last := 0
	for {
		syms := streamedSymbols()
		if len(syms) == 0 {
			time.Sleep(5 * time.Second)
			continue
		}
		sym := syms[last%len(syms)]
		last++
		for _, side := range []string{"buy", "sell"} {
			f, err := blend(sym, side, 3600)
			if err != nil {
				continue
			}
			if body, err := json.Marshal(f); err == nil {
				publish("intel", "intel.forecast", body)
			}
		}
		time.Sleep(every)
	}
}

// streamedSymbols asks our own feed which symbols have live data.
func streamedSymbols() []string {
	r, err := http.Get("http://127.0.0.1:8796/streamprices")
	if err != nil {
		return nil
	}
	defer r.Body.Close()
	var payload struct {
		P map[string]float64 `json:"p"`
	}
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		return nil
	}
	out := make([]string, 0, len(payload.P))
	for sym := range payload.P {
		out = append(out, sym)
	}
	sort.Strings(out)
	return out
}

func main() {
	addr := flag.String("addr", "127.0.0.1:8790", "listen address")
	flag.Parse()
	log.SetFlags(log.Ltime)
	go publishLoop(1500 * time.Millisecond)
	mux := http.NewServeMux()

	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{"ok": true, "service": "forecast",
			"agents": []string{"momentum", "volume", "volatility", "reversion",
				"ma", "breakout", "pressure"},
			"ts": time.Now().UnixMilli()})
	})

	mux.HandleFunc("/forecast", func(w http.ResponseWriter, r *http.Request) {
		sym := r.URL.Query().Get("symbol")
		side := r.URL.Query().Get("side")
		if side == "" {
			side = "buy"
		}
		horizon := 300
		if h := r.URL.Query().Get("horizon"); h != "" {
			fmt.Sscanf(h, "%d", &horizon)
		}
		log.Printf("forecast %s %s h=%d", sym, side, horizon)
		f, err := blend(sym, side, horizon)
		w.Header().Set("Content-Type", "application/json")
		if err != nil {
			w.WriteHeader(502)
			json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}
		json.NewEncoder(w).Encode(f)
		go func(fc forecast) {
			if body, err := json.Marshal(fc); err == nil {
				publish("intel", "intel.forecast", body)
			}
		}(f)
	})

	mux.HandleFunc("/views", func(w http.ResponseWriter, r *http.Request) {
		sym := r.URL.Query().Get("symbol")
		log.Printf("views %s", sym)
		if sym == "" {
			w.WriteHeader(400)
			json.NewEncoder(w).Encode(map[string]string{"error": "symbol required"})
			return
		}
		bs, err := fetchAt(sym, "15m", 200)
		if err != nil {
			log.Printf("views fetch error: %v", err)
			w.WriteHeader(502)
			json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}
		log.Printf("views %s bars=%d", sym, len(bs))
		defer func() {
			if r2 := recover(); r2 != nil {
				log.Printf("PANIC in views: %v", r2)
			}
		}()
		if len(bs) < 40 {
			w.WriteHeader(502)
			json.NewEncoder(w).Encode(map[string]string{"error": "not enough bars"})
			return
		}
		vs := []view{agentMomentum(bs, "buy"), agentVolume(bs, "buy"),
			agentVolatility(bs, "buy", 300), agentReversion(bs, "buy"),
			agentMA(bs, "buy"), agentBreakout(bs, "buy"), agentPressure(bs, "buy")}
		for _, v := range vs {
			log.Printf("  agent=%s prob=%v move=%v conf=%v note=%s", v.Agent, v.Prob, v.MovePct, v.Confidence, v.Note)
		}
		payload := map[string]any{"symbol": sym, "views": vs}
		buf, merr := json.Marshal(payload)
		if merr != nil {
			log.Printf("marshal error: %v", merr)
			w.WriteHeader(500)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write(buf)
	})

	// post-hoc grading: a model tells us whether a forecast was right
	mux.HandleFunc("/grade", func(w http.ResponseWriter, r *http.Request) {
		var in struct {
			Agent string `json:"agent"`
			Hit   bool   `json:"hit"`
		}
		if err := json.NewDecoder(r.Body).Decode(&in); err != nil {
			w.WriteHeader(400)
			return
		}
		mu.Lock()
		h := hits[in.Agent]
		h[1]++
		if in.Hit {
			h[0]++
		}
		hits[in.Agent] = h
		mu.Unlock()
		json.NewEncoder(w).Encode(map[string]any{"ok": true})
	})

	mux.HandleFunc("/state", func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		defer mu.Unlock()
		out := map[string]any{}
		keys := make([]string, 0, len(hits))
		for k := range hits {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			h := hits[k]
			acc := 0.0
			if h[1] > 0 {
				acc = float64(h[0]) / float64(h[1])
			}
			out[k] = map[string]any{"hits": h[0], "total": h[1], "accuracy": acc}
		}
		json.NewEncoder(w).Encode(out)
	})

	log.Printf("forecast service on http://%s", *addr)
	log.Fatal(http.ListenAndServe(*addr, mux))
	os.Exit(0)
}
