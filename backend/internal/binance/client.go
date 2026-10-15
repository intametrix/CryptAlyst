// Package binance implements the public market-data access the scanner needs:
// a weight-aware REST client and a combined-stream websocket client.
//
// No API key is required for anything here. The client deliberately uses only
// the standard library plus gorilla/websocket for the one thing stdlib cannot
// do (a websocket client), so the detection loop has no other moving parts.
package binance

import (
	"compress/gzip"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// Hosts and paths are fixed: this build targets Binance spot only.
const (
	SpotBase        = "https://api.binance.com"
	SpotStreamBase  = "wss://stream.binance.com:9443"
	SpotWeightLimit = 6000 // request weight per minute
)

// Client is a weight-aware REST client for one host.
type Client struct {
	Base        string
	HTTP        *http.Client
	UserAgent   string
	MaxRetries  int
	WeightLimit int

	// WeightUsed is the most recent x-mbx-used-weight-1m reported by the
	// exchange, and WeightPeak is the high-water mark we have observed.
	WeightUsed atomic.Int64
	WeightPeak atomic.Int64

	requests atomic.Int64
	errors   atomic.Int64
	bytesIn  atomic.Int64
}

// NewClient builds a REST client with sane timeouts.
func NewClient(base string) *Client {
	return &Client{
		Base:       base,
		UserAgent:  "pumpdump-go/1.0",
		MaxRetries: 4,
		HTTP: &http.Client{
			Timeout: 20 * time.Second,
			Transport: &http.Transport{
				MaxIdleConns:        32,
				MaxIdleConnsPerHost: 16,
				IdleConnTimeout:     60 * time.Second,
			},
		},
		WeightLimit: SpotWeightLimit,
	}
}

// Stats reports request counters for the status endpoint.
type Stats struct {
	Requests   int64 `json:"requests"`
	Errors     int64 `json:"errors"`
	BytesIn    int64 `json:"bytes_in"`
	WeightUsed int64 `json:"weight_used"`
	WeightPeak int64 `json:"weight_peak"`
}

// Stats returns a snapshot of the client counters.
func (c *Client) Stats() Stats {
	return Stats{
		Requests:   c.requests.Load(),
		Errors:     c.errors.Load(),
		BytesIn:    c.bytesIn.Load(),
		WeightUsed: c.WeightUsed.Load(),
		WeightPeak: c.WeightPeak.Load(),
	}
}

// WeightPressure returns the fraction of the per-minute weight budget in use.
func (c *Client) WeightPressure() float64 {
	if c.WeightLimit == 0 {
		return 0
	}
	return float64(c.WeightUsed.Load()) / float64(c.WeightLimit)
}

// APIError is a non-retryable error returned by the exchange.
type APIError struct {
	Status int
	Code   int
	Msg    string
}

func (e *APIError) Error() string {
	if e.Code != 0 {
		return fmt.Sprintf("binance http %d code=%d: %s", e.Status, e.Code, e.Msg)
	}
	return fmt.Sprintf("binance http %d: %s", e.Status, e.Msg)
}

// get performs a GET with gzip, retries and weight tracking.
//
// `params` values may be a string, a number, or a []string for array
// parameters. Array parameters are rendered as a JSON document, which is what
// Binance expects for `symbols` -- and NOT as repeated query keys.
func (c *Client) get(ctx context.Context, path string, params url.Values, out any) error {
	endpoint := c.Base + path
	if len(params) > 0 {
		endpoint += "?" + params.Encode()
	}

	var lastErr error
	for attempt := 1; attempt <= c.MaxRetries; attempt++ {
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
		if err != nil {
			return err
		}
		req.Header.Set("User-Agent", c.UserAgent)
		req.Header.Set("Accept", "application/json")
		req.Header.Set("Accept-Encoding", "gzip")

		resp, err := c.HTTP.Do(req)
		if err != nil {
			lastErr = err
			c.errors.Add(1)
			if attempt == c.MaxRetries {
				break
			}
			sleepBackoff(ctx, attempt)
			continue
		}

		body, readErr := readBody(resp)
		c.requests.Add(1)
		c.bytesIn.Add(int64(len(body)))
		if w, ok := parseWeight(resp.Header.Get("x-mbx-used-weight-1m")); ok {
			c.WeightUsed.Store(int64(w))
			for {
				peak := c.WeightPeak.Load()
				if int64(w) <= peak || c.WeightPeak.CompareAndSwap(peak, int64(w)) {
					break
				}
			}
		}

		if resp.StatusCode >= 200 && resp.StatusCode < 300 {
			resp.Body.Close()
			if readErr != nil {
				return readErr
			}
			if len(body) == 0 {
				return nil
			}
			return json.Unmarshal(body, out)
		}

		code, msg := decodeError(body)
		resp.Body.Close()

		switch {
		case resp.StatusCode == http.StatusTooManyRequests, resp.StatusCode == 418:
			// Rate limited or temporarily banned: honour Retry-After.
			wait := time.Duration(retryAfter(resp.Header.Get("Retry-After"), resp.StatusCode)) * time.Second
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(wait):
			}
			lastErr = &APIError{Status: resp.StatusCode, Code: code, Msg: msg}
			continue
		case resp.StatusCode >= 500:
			lastErr = &APIError{Status: resp.StatusCode, Code: code, Msg: msg}
			c.errors.Add(1)
			if attempt == c.MaxRetries {
				break
			}
			sleepBackoff(ctx, attempt)
			continue
		default:
			c.errors.Add(1)
			return &APIError{Status: resp.StatusCode, Code: code, Msg: msg}
		}
	}
	if lastErr == nil {
		lastErr = errors.New("request failed")
	}
	return lastErr
}

func readBody(resp *http.Response) ([]byte, error) {
	if strings.EqualFold(resp.Header.Get("Content-Encoding"), "gzip") {
		reader, err := gzip.NewReader(resp.Body)
		if err != nil {
			return nil, err
		}
		defer reader.Close()
		return io.ReadAll(reader)
	}
	return io.ReadAll(resp.Body)
}

func parseWeight(header string) (int, bool) {
	if header == "" {
		return 0, false
	}
	w, err := strconv.Atoi(header)
	if err != nil {
		return 0, false
	}
	return w, true
}

func retryAfter(header string, status int) int {
	if header != "" {
		if n, err := strconv.Atoi(header); err == nil && n > 0 {
			return n
		}
	}
	if status == 418 {
		return 120
	}
	return 5
}

func decodeError(body []byte) (int, string) {
	var payload struct {
		Code int    `json:"code"`
		Msg  string `json:"msg"`
	}
	if err := json.Unmarshal(body, &payload); err == nil {
		return payload.Code, payload.Msg
	}
	if len(body) > 200 {
		body = body[:200]
	}
	return 0, string(body)
}

func sleepBackoff(ctx context.Context, attempt int) {
	d := time.Duration(1<<uint(attempt-1)) * 500 * time.Millisecond
	if d > 20*time.Second {
		d = 20 * time.Second
	}
	select {
	case <-ctx.Done():
	case <-time.After(d):
	}
}

// --------------------------------------------------------------------------
// Endpoints
// --------------------------------------------------------------------------

// Ping checks reachability.
func (c *Client) Ping(ctx context.Context) error {
	var out map[string]any
	return c.get(ctx, "/api/v3/ping", nil, &out)
}

// Ticker24h is one row of the rolling 24h statistics.
type Ticker24h struct {
	Symbol             string `json:"symbol"`
	LastPrice          string `json:"lastPrice"`
	PriceChangePercent string `json:"priceChangePercent"`
	OpenPrice          string `json:"openPrice"`
	HighPrice          string `json:"highPrice"`
	LowPrice           string `json:"lowPrice"`
	Volume             string `json:"volume"`
	QuoteVolume        string `json:"quoteVolume"`
	Count              int64  `json:"count"`
}

// Tickers24h fetches every symbol's 24h statistics in one request.
//
// Cost is 80 weight for the full market, which is why the fast path polls a
// symbol subset instead. See Tickers24hForSymbols.
func (c *Client) Tickers24h(ctx context.Context) ([]Ticker24h, error) {
	var out []Ticker24h
	if err := c.get(ctx, "/api/v3/ticker/24hr", nil, &out); err != nil {
		return nil, err
	}
	return out, nil
}

// Tickers24hForSymbols fetches 24h statistics for a symbol subset.
//
// Binance charges 2 weight per symbol for up to 20 symbols, then 40 flat for
// 21-100, then 80 above that -- so batching a whole universe into one call is
// far cheaper than it looks and beats polling the full market.
func (c *Client) Tickers24hForSymbols(ctx context.Context, symbols []string) ([]Ticker24h, error) {
	if len(symbols) == 0 {
		return nil, nil
	}
	encoded, err := json.Marshal(symbols)
	if err != nil {
		return nil, err
	}
	params := url.Values{}
	params.Set("symbols", string(encoded))
	var out []Ticker24h
	if err := c.get(ctx, "/api/v3/ticker/24hr", params, &out); err != nil {
		return nil, err
	}
	return out, nil
}

// Kline is one OHLCV bar.
//
// Binance returns klines as a positional array; ParseKlines maps it.
type Kline struct {
	OpenTime      int64
	Open          float64
	High          float64
	Low           float64
	Close         float64
	Volume        float64
	CloseTime     int64
	QuoteVolume   float64
	Trades        int64
	TakerBuyBase  float64
	TakerBuyQuote float64
}

// Klines fetches OHLCV bars, oldest first.
func (c *Client) Klines(ctx context.Context, symbol, interval string, limit int) ([]Kline, error) {
	params := url.Values{}
	params.Set("symbol", symbol)
	params.Set("interval", interval)
	params.Set("limit", strconv.Itoa(limit))
	var raw [][]json.RawMessage
	if err := c.get(ctx, "/api/v3/klines", params, &raw); err != nil {
		return nil, err
	}
	return ParseKlines(raw), nil
}

// ParseKlines converts positional kline arrays into Kline values, skipping any
// row that is malformed rather than failing the whole batch.
func ParseKlines(raw [][]json.RawMessage) []Kline {
	out := make([]Kline, 0, len(raw))
	for _, row := range raw {
		if len(row) < 11 {
			continue
		}
		k := Kline{
			OpenTime:      jsonInt(row[0]),
			Open:          jsonFloat(row[1]),
			High:          jsonFloat(row[2]),
			Low:           jsonFloat(row[3]),
			Close:         jsonFloat(row[4]),
			Volume:        jsonFloat(row[5]),
			CloseTime:     jsonInt(row[6]),
			QuoteVolume:   jsonFloat(row[7]),
			Trades:        int64(jsonFloat(row[8])),
			TakerBuyBase:  jsonFloat(row[9]),
			TakerBuyQuote: jsonFloat(row[10]),
		}
		if k.Close <= 0 {
			continue
		}
		out = append(out, k)
	}
	return out
}

// SymbolMeta is the subset of exchangeInfo the scanner needs.
type SymbolMeta struct {
	Symbol     string
	BaseAsset  string
	QuoteAsset string
	Status     string
}

// ExchangeInfo fetches trading rules and symbol metadata.
//
// This response is ~17 MB uncompressed, so it is called once at startup and
// never in a loop.
func (c *Client) ExchangeInfo(ctx context.Context) ([]SymbolMeta, error) {
	var payload struct {
		Symbols []struct {
			Symbol     string `json:"symbol"`
			BaseAsset  string `json:"baseAsset"`
			QuoteAsset string `json:"quoteAsset"`
			Status     string `json:"status"`
		} `json:"symbols"`
	}
	if err := c.get(ctx, "/api/v3/exchangeInfo", nil, &payload); err != nil {
		return nil, err
	}
	out := make([]SymbolMeta, 0, len(payload.Symbols))
	for _, s := range payload.Symbols {
		out = append(out, SymbolMeta{
			Symbol:     s.Symbol,
			BaseAsset:  s.BaseAsset,
			QuoteAsset: s.QuoteAsset,
			Status:     s.Status,
		})
	}
	return out, nil
}

func jsonFloat(raw json.RawMessage) float64 {
	if len(raw) == 0 {
		return 0
	}
	if raw[0] == '"' {
		var s string
		if err := json.Unmarshal(raw, &s); err != nil {
			return 0
		}
		f, err := strconv.ParseFloat(s, 64)
		if err != nil {
			return 0
		}
		return f
	}
	var f float64
	if err := json.Unmarshal(raw, &f); err != nil {
		return 0
	}
	return f
}

func jsonInt(raw json.RawMessage) int64 {
	if len(raw) == 0 {
		return 0
	}
	if raw[0] == '"' {
		var s string
		if err := json.Unmarshal(raw, &s); err != nil {
			return 0
		}
		n, err := strconv.ParseInt(s, 10, 64)
		if err != nil {
			return 0
		}
		return n
	}
	var f float64
	if err := json.Unmarshal(raw, &f); err != nil {
		return 0
	}
	return int64(f)
}

// Float parses a Binance decimal string, returning 0 on failure.
func Float(s string) float64 {
	if s == "" {
		return 0
	}
	f, err := strconv.ParseFloat(s, 64)
	if err != nil {
		return 0
	}
	return f
}

// Pool bounds the concurrency of REST fan-out (klines for many symbols).
type Pool struct {
	sem chan struct{}
	wg  sync.WaitGroup
}

// NewPool creates a pool with the given concurrency.
func NewPool(size int) *Pool {
	if size < 1 {
		size = 1
	}
	return &Pool{sem: make(chan struct{}, size)}
}

// Go runs fn under the pool's concurrency limit.
func (p *Pool) Go(fn func()) {
	p.wg.Add(1)
	go func() {
		defer p.wg.Done()
		p.sem <- struct{}{}
		defer func() { <-p.sem }()
		fn()
	}()
}

// Wait blocks until every submitted function has finished.
func (p *Pool) Wait() { p.wg.Wait() }
