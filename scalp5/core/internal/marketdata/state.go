// Package marketdata holds the live market state: prices, depth, candles, 24h
// statistics, plus the bus publications derived from them.
package marketdata

import (
	"encoding/json"
	"sort"
	"sync"
	"time"
)

type Level struct {
	Price float64 `json:"p"`
	Qty   float64 `json:"q"`
}

type Book struct {
	Bid      float64 `json:"b"`
	Ask      float64 `json:"a"`
	BidQty   float64 `json:"B"`
	AskQty   float64 `json:"A"`
	SpreadBP float64 `json:"sp"`
	TS       int64   `json:"ts"`
}

type Kline struct {
	T  int64   `json:"t"`
	O  float64 `json:"o"`
	H  float64 `json:"h"`
	L  float64 `json:"l"`
	C  float64 `json:"c"`
	V  float64 `json:"v"`
	Q  float64 `json:"q"`
	Qb float64 `json:"Q"`
}

type Mini struct {
	Pct float64 `json:"pct"`
	Q   float64 `json:"q"`
	V   float64 `json:"v"`
	P   float64 `json:"p"`
}

type State struct {
	mu          sync.RWMutex
	Prices      map[string]float64
	Books       map[string]*Book
	Depth       map[string][]Level
	DepthS      map[string][]Level
	K1          map[string][]Kline
	K15         map[string][]Kline
	Mini        map[string]Mini
	Msgs        int64
	LastMS      int64
	Series      int
	KlineEvents int64
}

func New() *State {
	return &State{
		Prices: map[string]float64{}, Books: map[string]*Book{},
		Depth: map[string][]Level{}, DepthS: map[string][]Level{},
		K1: map[string][]Kline{}, K15: map[string][]Kline{},
		Mini: map[string]Mini{},
	}
}

func (s *State) OnBook(sym string, b Book) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if b.Bid > 0 && b.Ask > 0 {
		b.SpreadBP = 10000 * (b.Ask - b.Bid) / b.Bid
		s.Prices[sym] = (b.Bid + b.Ask) / 2
	} else if b.Bid > 0 {
		s.Prices[sym] = b.Bid
	}
	b.TS = time.Now().UnixMilli()
	s.Books[sym] = &b
	s.Msgs++
	s.LastMS = b.TS
}

func (s *State) OnDepth(sym string, bids, asks []Level) {
	if len(bids) == 0 || len(asks) == 0 {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.Depth[sym] = bids
	s.DepthS[sym] = asks
	s.Msgs++
	s.LastMS = time.Now().UnixMilli()
}

func (s *State) OnKline(sym, interval string, k Kline, closed bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.KlineEvents++
	var series *[]Kline
	if interval == "1m" {
		series = &[]Kline{} // placeholder, replaced below
		_ = series
	}
	apply := func(list []Kline) []Kline {
		if len(list) == 0 || list[len(list)-1].T != k.T {
			list = append(list, k)
		} else if closed {
			list[len(list)-1] = k
		} else {
			list[len(list)-1] = k
		}
		if len(list) > 200 {
			list = list[len(list)-200:]
		}
		return list
	}
	if interval == "1m" {
		s.K1[sym] = apply(s.K1[sym])
	} else {
		s.K15[sym] = apply(s.K15[sym])
	}
	s.Series = len(s.K1) + len(s.K15)
	s.Msgs++
	s.LastMS = time.Now().UnixMilli()
}

func (s *State) OnMini(sym string, m Mini) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.Mini[sym] = m
	s.Msgs++
	s.LastMS = time.Now().UnixMilli()
}

func (s *State) Snapshot() map[string]float64 {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make(map[string]float64, len(s.Prices))
	for k, v := range s.Prices {
		out[k] = v
	}
	return out
}

func (s *State) Price(sym string) (float64, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	v, ok := s.Prices[sym]
	return v, ok
}

func (s *State) KLines(sym, interval string, limit int) []Kline {
	s.mu.RLock()
	defer s.mu.RUnlock()
	var src []Kline
	if interval == "1m" {
		src = s.K1[sym]
	} else {
		src = s.K15[sym]
	}
	if limit <= 0 || limit > len(src) {
		limit = len(src)
	}
	out := make([]Kline, limit)
	copy(out, src[len(src)-limit:])
	return out
}

type Mover struct {
	S   string  `json:"s"`
	P   float64 `json:"p"`
	Pct float64 `json:"pct"`
	QV  float64 `json:"qv"`
	N   int     `json:"n"`
}

func (s *State) Movers(limit int, minQ float64) map[string]any {
	s.mu.RLock()
	defer s.mu.RUnlock()
	rows := make([]Mover, 0, len(s.Mini))
	for sym, m := range s.Mini {
		if m.Q < minQ {
			continue
		}
		px, _ := s.Prices[sym]
		rows = append(rows, Mover{S: sym, P: px, Pct: m.Pct, QV: m.Q})
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].Pct > rows[j].Pct })
	gainers := rows
	losers := make([]Mover, len(rows))
	copy(losers, rows)
	for i, j := 0, len(losers)-1; i < j; i, j = i+1, j-1 {
		losers[i], losers[j] = losers[j], losers[i]
	}
	byVolume := make([]Mover, len(rows))
	copy(byVolume, rows)
	sort.Slice(byVolume, func(i, j int) bool { return byVolume[i].QV > byVolume[j].QV })
	if limit > len(gainers) {
		limit = len(gainers)
	}
	return map[string]any{
		"gainers":     gainers[:limit],
		"losers":      losers[:limit],
		"most_traded": byVolume[:limit],
		"universe":    len(rows),
		"ts":          time.Now().UnixMilli(),
	}
}

func (s *State) Stats() (msgs int64, last int64, series int, symbols int, prices int) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.Msgs, s.LastMS, s.Series, len(s.K1), len(s.Prices)
}

// Payloads for the bus.
func (s *State) TickPayload() ([]byte, []byte) {
	s.mu.RLock()
	prices := make(map[string]float64, len(s.Prices))
	for k, v := range s.Prices {
		prices[k] = v
	}
	mini := make(map[string]Mini, len(prices))
	for sym, m := range s.Mini {
		if _, ok := prices[sym]; ok {
			mini[sym] = m
		}
	}
	s.mu.RUnlock()
	ticks, _ := json.Marshal(map[string]any{
		"p": prices, "m": mini, "ts": time.Now().UnixMilli(),
	})
	price, _ := json.Marshal(map[string]any{
		"p": prices, "ts": time.Now().UnixMilli(),
	})
	return ticks, price
}

// DepthChanged reports whether the book moved enough to be worth publishing.
// Five levels per symbol per second was 18 GB/day; this publishes on change.
func (s *State) DepthChanged(sym string, lastPrice, lastQty float64) bool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	bids := s.Depth[sym]
	asks := s.DepthS[sym]
	if len(bids) == 0 || len(asks) == 0 {
		return false
	}
	if lastPrice == 0 {
		return true
	}
	if bids[0].Price != lastPrice {
		return true
	}
	// same best price but the size behind it moved by more than 25%
	if lastQty > 0 {
		delta := bids[0].Qty - lastQty
		if delta < 0 {
			delta = -delta
		}
		return delta/lastQty > 0.25
	}
	return true
}

func (s *State) DepthPayload(sym string) []byte {
	s.mu.RLock()
	bids := s.Depth[sym]
	asks := s.DepthS[sym]
	s.mu.RUnlock()
	if len(bids) == 0 || len(asks) == 0 {
		return nil
	}
	var bidQ, askQ float64
	for _, l := range bids {
		bidQ += l.Qty
	}
	for _, l := range asks {
		askQ += l.Qty
	}
	imbalance := 0.0
	if bidQ+askQ > 0 {
		imbalance = (bidQ - askQ) / (bidQ + askQ)
	}
	body, _ := json.Marshal(map[string]any{
		"symbol": sym, "bids": bids, "asks": asks,
		"bid_qty": bidQ, "ask_qty": askQ, "imbalance": imbalance,
		"ts": time.Now().UnixMilli(),
	})
	return body
}

func (s *State) KlineCount() int64 {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.KlineEvents
}

// TopOfBook returns the best bid price and size, used to decide whether depth
// has changed enough to publish again.
func (s *State) TopOfBook(sym string) (price, qty float64) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	if bids := s.Depth[sym]; len(bids) > 0 {
		return bids[0].Price, bids[0].Qty
	}
	return 0, 0
}
