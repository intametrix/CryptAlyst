// Package market builds and maintains the tradable universe: which symbols
// exist, which are liquid enough to care about, and their rolling 24h context.
package market

import (
	"context"
	"fmt"
	"sort"
	"strings"
	"sync"

	"pumpdump/backend/internal/binance"
)

// Config controls universe selection.
type Config struct {
	QuoteAssets       []string
	MinQuoteVolume24h float64
	MaxSymbols        int
	ExcludeSymbols    []string
	ExcludePatterns   []string
	ExtraSymbols      []string
}

// DefaultConfig returns sensible spot defaults.
func DefaultConfig() Config {
	return Config{
		QuoteAssets:       []string{"USDT"},
		MinQuoteVolume24h: 750_000,
		MaxSymbols:        400,
		ExcludePatterns:   []string{"UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT"},
	}
}

// Context is a symbol's rolling 24h statistics.
type Context struct {
	Symbol         string  `json:"symbol"`
	LastPrice      float64 `json:"last_price"`
	OpenPrice      float64 `json:"open_price"`
	HighPrice      float64 `json:"high_price"`
	LowPrice       float64 `json:"low_price"`
	PriceChangePct float64 `json:"change_24h_pct"`
	QuoteVolume24h float64 `json:"quote_volume_24h"`
	Volume24h      float64 `json:"volume_24h"`
	Trades         int64   `json:"trades_24h"`
	UpdatedMS      int64   `json:"updated_ms"`
}

// Universe holds symbol metadata plus 24h context.
type Universe struct {
	cfg Config

	mu      sync.RWMutex
	meta    map[string]binance.SymbolMeta
	bySym   map[string]Context
	tracked []string
	volume  float64
}

// New creates an empty universe.
func New(cfg Config) *Universe {
	if len(cfg.QuoteAssets) == 0 {
		cfg.QuoteAssets = []string{"USDT"}
	}
	return &Universe{
		cfg:   cfg,
		meta:  make(map[string]binance.SymbolMeta),
		bySym: make(map[string]Context),
	}
}

func (u *Universe) excluded(symbol string) bool {
	for _, s := range u.cfg.ExcludeSymbols {
		if s == symbol {
			return true
		}
	}
	upper := strings.ToUpper(symbol)
	for _, p := range u.cfg.ExcludePatterns {
		if p != "" && strings.Contains(upper, strings.ToUpper(p)) {
			return true
		}
	}
	return false
}

// LoadMetadata fetches exchangeInfo and indexes eligible symbols.
func (u *Universe) LoadMetadata(ctx context.Context, client *binance.Client) (int, error) {
	symbols, err := client.ExchangeInfo(ctx)
	if err != nil {
		return 0, err
	}
	allowed := make(map[string]struct{}, len(u.cfg.QuoteAssets))
	for _, q := range u.cfg.QuoteAssets {
		allowed[strings.ToUpper(q)] = struct{}{}
	}
	keep := make(map[string]binance.SymbolMeta)
	for _, s := range symbols {
		if _, ok := allowed[strings.ToUpper(s.QuoteAsset)]; !ok {
			continue
		}
		if !strings.EqualFold(s.Status, "TRADING") {
			continue
		}
		if u.excluded(s.Symbol) {
			continue
		}
		keep[s.Symbol] = s
	}
	u.mu.Lock()
	u.meta = keep
	u.mu.Unlock()
	return len(keep), nil
}

// Refresh pulls the full-market 24h ticker and rebuilds the tracked list.
//
// The full-market call costs 80 weight; at a 60-second cadence that is trivial
// against the 6000/minute budget, and it is far simpler than stitching together
// per-symbol responses.
func (u *Universe) Refresh(ctx context.Context, client *binance.Client) (int, error) {
	rows, err := client.Tickers24h(ctx)
	if err != nil {
		return 0, err
	}
	u.mu.RLock()
	hasMeta := len(u.meta) > 0
	u.mu.RUnlock()

	now := nowMS()
	updated := make(map[string]Context, len(rows))
	var totalVolume float64
	for _, row := range rows {
		if u.excluded(row.Symbol) {
			continue
		}
		if hasMeta {
			u.mu.RLock()
			_, ok := u.meta[row.Symbol]
			u.mu.RUnlock()
			if !ok {
				continue
			}
		}
		price := binance.Float(row.LastPrice)
		if price <= 0 {
			continue
		}
		c := Context{
			Symbol:         row.Symbol,
			LastPrice:      price,
			OpenPrice:      binance.Float(row.OpenPrice),
			HighPrice:      binance.Float(row.HighPrice),
			LowPrice:       binance.Float(row.LowPrice),
			PriceChangePct: binance.Float(row.PriceChangePercent),
			QuoteVolume24h: binance.Float(row.QuoteVolume),
			Volume24h:      binance.Float(row.Volume),
			Trades:         row.Count,
			UpdatedMS:      now,
		}
		updated[row.Symbol] = c
		totalVolume += c.QuoteVolume24h
	}

	// Rank by liquidity, keep the top N above the floor.
	minLiq, maxSym := u.cfgSnapshot()
	candidates := make([]Context, 0, len(updated))
	for _, c := range updated {
		if c.QuoteVolume24h >= minLiq {
			candidates = append(candidates, c)
		}
	}
	sort.Slice(candidates, func(i, j int) bool {
		return candidates[i].QuoteVolume24h > candidates[j].QuoteVolume24h
	})

	limit := maxSym
	if limit > 0 && len(candidates) > limit {
		candidates = candidates[:limit]
	}
	tracked := make([]string, 0, len(candidates))
	for _, c := range candidates {
		tracked = append(tracked, c.Symbol)
	}
	// Force-track extra symbols that exist, regardless of their 24h volume.
	for _, s := range u.cfg.ExtraSymbols {
		s = strings.ToUpper(strings.TrimSpace(s))
		if s == "" {
			continue
		}
		if _, ok := updated[s]; !ok {
			continue
		}
		dup := false
		for _, x := range tracked {
			if x == s {
				dup = true
				break
			}
		}
		if !dup {
			tracked = append(tracked, s)
		}
	}

	u.mu.Lock()
	u.bySym = updated
	u.tracked = tracked
	u.volume = totalVolume
	u.mu.Unlock()
	return len(updated), nil
}

// Tracked returns the tracked symbol list, in descending liquidity order.
func (u *Universe) Tracked() []string {
	u.mu.RLock()
	defer u.mu.RUnlock()
	out := make([]string, len(u.tracked))
	copy(out, u.tracked)
	return out
}

// SetMinQuoteVolume updates the liquidity floor live; the next Refresh applies it.
func (u *Universe) SetMinQuoteVolume(v float64) {
	u.mu.Lock()
	u.cfg.MinQuoteVolume24h = v
	u.mu.Unlock()
}

// SetMaxSymbols updates the tracked-symbol cap live; the next Refresh applies it.
func (u *Universe) SetMaxSymbols(n int) {
	u.mu.Lock()
	u.cfg.MaxSymbols = n
	u.mu.Unlock()
}

func (u *Universe) cfgSnapshot() (float64, int) {
	u.mu.RLock()
	defer u.mu.RUnlock()
	return u.cfg.MinQuoteVolume24h, u.cfg.MaxSymbols
}

// Get returns a symbol's 24h context.
func (u *Universe) Get(symbol string) (Context, bool) {
	u.mu.RLock()
	defer u.mu.RUnlock()
	c, ok := u.bySym[symbol]
	return c, ok
}

// TopByVolume returns the N most liquid symbols.
func (u *Universe) TopByVolume(n int) []Context {
	u.mu.RLock()
	defer u.mu.RUnlock()
	all := make([]Context, 0, len(u.bySym))
	for _, c := range u.bySym {
		all = append(all, c)
	}
	sort.Slice(all, func(i, j int) bool {
		return all[i].QuoteVolume24h > all[j].QuoteVolume24h
	})
	if n > 0 && len(all) > n {
		all = all[:n]
	}
	return all
}

// TotalVolume24h returns the summed 24h quote volume across the market.
func (u *Universe) TotalVolume24h() float64 {
	u.mu.RLock()
	defer u.mu.RUnlock()
	return u.volume
}

// Size returns how many symbols currently have context.
func (u *Universe) Size() int {
	u.mu.RLock()
	defer u.mu.RUnlock()
	return len(u.bySym)
}

// Summary renders a one-line description for logs.
func (u *Universe) Summary() string {
	u.mu.RLock()
	defer u.mu.RUnlock()
	return fmt.Sprintf("%d symbols with 24h data, %d tracked", len(u.bySym), len(u.tracked))
}
