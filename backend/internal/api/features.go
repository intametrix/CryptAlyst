package api

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"
)

// Market features that price alone cannot express.
//
// A chart of closes is one projection of the order book. Funding tells you what
// leveraged traders are paying to stay positioned, open interest tells you
// whether a move is new money or closing trades, and the taker buy/sell ratio is
// the aggression imbalance. Each is published by the exchange, so these are
// measurements rather than derived opinions.
const futuresBase = "https://fapi.binance.com"

// FeaturePoint is one observation aligned to a bar timestamp.
type FeaturePoint struct {
	T          int64   `json:"t"`
	Funding    float64 `json:"funding"` // funding rate, as published
	OpenInt    float64 `json:"open_interest"`
	OpenIntChg float64 `json:"open_interest_chg"` // fractional change since the previous point
	TakerRatio float64 `json:"taker_ratio"`       // buy volume / (buy+sell); 0.5 is balanced
}

type featureCacheEntry struct {
	points  []FeaturePoint
	fetched time.Time
}

var (
	featureMu    sync.Mutex
	featureCache = map[string]featureCacheEntry{}
	featureTTL   = 5 * time.Minute
)

// supportedFeaturePeriods is what /futures/data accepts.
var supportedFeaturePeriods = map[string]bool{
	"5m": true, "15m": true, "30m": true, "1h": true,
	"2h": true, "4h": true, "6h": true, "12h": true, "1d": true,
}

func featurePeriod(interval string) string {
	if supportedFeaturePeriods[interval] {
		return interval
	}
	return "1h"
}

func getJSON(ctx context.Context, url string, out any) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return err
	}
	req.Header.Set("accept", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("%s: %s", url, resp.Status)
	}
	return json.NewDecoder(resp.Body).Decode(out)
}

// fetchFeatures pulls the three published series and joins them by timestamp.
// A missing series is not fatal: whatever arrived is used, and the caller can
// see which features are absent from the zero values.
func (s *Server) fetchFeatures(ctx context.Context, symbol, interval string) ([]FeaturePoint, error) {
	key := symbol + "|" + featurePeriod(interval)
	featureMu.Lock()
	if e, ok := featureCache[key]; ok && time.Since(e.fetched) < featureTTL {
		featureMu.Unlock()
		return e.points, nil
	}
	featureMu.Unlock()

	period := featurePeriod(interval)
	var funding []struct {
		FundingTime int64  `json:"fundingTime"`
		FundingRate string `json:"fundingRate"`
	}
	var oi []struct {
		SumOpenInterest string `json:"sumOpenInterest"`
		Timestamp       int64  `json:"timestamp"`
	}
	var taker []struct {
		BuySellRatio string `json:"buySellRatio"`
		Timestamp    int64  `json:"timestamp"`
	}

	var wg sync.WaitGroup
	wg.Add(3)
	go func() {
		defer wg.Done()
		_ = getJSON(ctx, futuresBase+"/fapi/v1/fundingRate?symbol="+symbol+"&limit=1000", &funding)
	}()
	go func() {
		defer wg.Done()
		_ = getJSON(ctx, futuresBase+"/futures/data/openInterestHist?symbol="+symbol+"&period="+period+"&limit=500", &oi)
	}()
	go func() {
		defer wg.Done()
		_ = getJSON(ctx, futuresBase+"/futures/data/takerlongshortRatio?symbol="+symbol+"&period="+period+"&limit=500", &taker)
	}()
	wg.Wait()

	byTime := map[int64]*FeaturePoint{}
	touch := func(t int64) *FeaturePoint {
		if p, ok := byTime[t]; ok {
			return p
		}
		p := &FeaturePoint{T: t}
		byTime[t] = p
		return p
	}
	for _, f := range funding {
		if f.FundingTime == 0 {
			continue
		}
		p := touch(f.FundingTime)
		p.Funding = parseFloat(f.FundingRate)
	}
	for _, o := range oi {
		if o.Timestamp == 0 {
			continue
		}
		touch(o.Timestamp).OpenInt = parseFloat(o.SumOpenInterest)
	}
	for _, tk := range taker {
		if tk.Timestamp == 0 {
			continue
		}
		// buySellRatio is buy/sell; convert to a share in [0,1] so 0.5 is neutral.
		r := parseFloat(tk.BuySellRatio)
		if r > 0 {
			touch(tk.Timestamp).TakerRatio = r / (1 + r)
		}
	}

	out := make([]FeaturePoint, 0, len(byTime))
	for _, p := range byTime {
		out = append(out, *p)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].T < out[j].T })

	// Open-interest change needs the previous point, so it is computed after the
	// sort rather than during the join.
	for i := 1; i < len(out); i++ {
		// A missing observation must read as "no change", not as a -100% collapse.
		if out[i-1].OpenInt > 0 && out[i].OpenInt > 0 {
			out[i].OpenIntChg = out[i].OpenInt/out[i-1].OpenInt - 1
		}
	}
	// Funding is published every 8h; carry the last value forward so every bar
	// carries the rate in force rather than a zero that looks like no funding.
	lastFunding := 0.0
	for i := range out {
		if out[i].Funding != 0 {
			lastFunding = out[i].Funding
		} else {
			out[i].Funding = lastFunding
		}
	}

	featureMu.Lock()
	featureCache[key] = featureCacheEntry{points: out, fetched: time.Now()}
	featureMu.Unlock()

	if len(out) == 0 {
		return nil, fmt.Errorf("no futures features published for %s", symbol)
	}
	return out, nil
}

func parseFloat(s string) float64 {
	var v float64
	_, _ = fmt.Sscanf(strings.TrimSpace(s), "%g", &v)
	return v
}

// handleFeatures exposes the raw series, so the UI can show what the model sees.
func (s *Server) handleFeatures(w http.ResponseWriter, r *http.Request) {
	symbol := strings.ToUpper(strings.TrimSpace(r.URL.Query().Get("symbol")))
	if symbol == "" {
		http.Error(w, "symbol required", http.StatusBadRequest)
		return
	}
	interval := r.URL.Query().Get("interval")
	ctx, cancel := context.WithTimeout(r.Context(), 12*time.Second)
	defer cancel()

	pts, err := s.fetchFeatures(ctx, symbol, interval)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]any{"ok": false, "error": err.Error(), "points": []FeaturePoint{}})
		return
	}
	limit := queryInt(r, "limit", 200)
	if limit < 10 {
		limit = 10
	}
	if limit < len(pts) {
		pts = pts[len(pts)-limit:]
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok": true, "symbol": symbol, "interval": featurePeriod(interval),
		"count": len(pts), "points": pts,
	})
}
