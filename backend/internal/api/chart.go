package api

import (
	"context"
	"fmt"
	"math"
	"net/http"
	"strings"
	"time"

	"pumpdump/backend/internal/binance"
	"pumpdump/backend/internal/forecast"
	"pumpdump/backend/internal/turtle"
)

// Chart data. The UI deliberately does NOT re-implement indicator maths: this
// endpoint returns OHLCV bars with every indicator series already computed by
// the same code the trading logic uses, so a chart can never disagree with a
// signal.

var allowedIntervals = map[string]bool{
	// 1s exists so the chart can actually SHOW a five-second window. Zoom
	// presets finer than the bar interval do not zoom, they just repeat the
	// same candle, which looks like a rendering bug to the user. Binance spot
	// serves 1s candles with a 1000-bar cap, which is 16.6 minutes of history.
	"1s": true,
	"1m": true, "5m": true, "15m": true, "1h": true, "4h": true, "1d": true,
}

// Bar is one OHLCV candle for the chart.
type Bar struct {
	T  int64   `json:"t"` // bar OPEN time, epoch ms
	O  float64 `json:"o"`
	H  float64 `json:"h"`
	L  float64 `json:"l"`
	C  float64 `json:"c"`
	V  float64 `json:"v"`  // base volume
	Q  float64 `json:"q"`  // quote volume
	N  int64   `json:"n"`  // trade count
	TB float64 `json:"tb"` // taker-buy share, 0-1
}

// Mark is a chart marker for a detected event.
//
// T is snapped to the containing bar's OPEN time (see buildMarks) because chart
// libraries place a marker only on a time that exists in the series data. An
// alert fires at an arbitrary millisecond; passing that through unchanged means
// the marker silently does not render, which looks like the feature is broken.
type Mark struct {
	T             int64   `json:"t"`
	Letter        string  `json:"letter"`
	Color         string  `json:"color"`
	Label         string  `json:"label"`
	Kind          string  `json:"kind"`
	Direction     int     `json:"direction"`
	Severity      string  `json:"severity"`
	Score         float64 `json:"score"`
	FlowQuote     float64 `json:"flow_quote"`
	BurstMultiple float64 `json:"burst_multiple"`
}

// intervalMillis is the bar duration per supported interval, used to snap
// marker times onto bar boundaries.
var intervalMillis = map[string]int64{
	"1s": 1_000,
	"1m": 60_000, "5m": 300_000, "15m": 900_000,
	"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
}

// IntervalBarCap is the most bars the exchange will return for an interval.
// 1s is capped at 1000, which is 16.6 minutes -- the reason the one-hour preset
// must switch to 1m bars rather than asking for 3600 one-second bars.
func IntervalBarCap(interval string) int {
	if interval == "1s" {
		return 1000
	}
	return 1000
}

// PresetPlan maps a zoom preset to the interval and bar count that can actually
// draw it. Returning both makes the constraint explicit instead of leaving the
// UI to request bars that cannot exist.
func PresetPlan(preset string) (interval string, bars int, ok bool) {
	switch preset {
	case "5s":
		return "1s", 5, true
	case "10s":
		return "1s", 10, true
	case "30s":
		return "1s", 30, true
	case "1m":
		return "1s", 60, true
	case "2m":
		return "1s", 120, true
	case "5m":
		return "1s", 300, true
	case "1h":
		return "1m", 60, true
	case "all":
		return "", 0, true
	default:
		return "", 0, false
	}
}

// buildMarks snaps every marker to its containing bar's open time. Markers are
// then deduplicated per (bar, kind), because a developng pump can fire several
// alerts inside one minute and a stack of identical letters is unreadable.
func buildMarks(marks []Mark, interval string) []Mark {
	step := intervalMillis[interval]
	if step <= 0 || len(marks) == 0 {
		return marks
	}
	out := make([]Mark, 0, len(marks))
	seen := make(map[string]struct{}, len(marks))
	for _, mark := range marks {
		if mark.T > 0 {
			mark.T = (mark.T / step) * step
		}
		key := fmt.Sprintf("%d|%s", mark.T, mark.Letter)
		if _, dup := seen[key]; dup {
			continue
		}
		seen[key] = struct{}{}
		out = append(out, mark)
	}
	return out
}

// ChartResponse is the /api/klines payload.
type ChartResponse struct {
	Type       string                `json:"type"`
	Symbol     string                `json:"symbol"`
	Interval   string                `json:"interval"`
	TSMS       int64                 `json:"ts_ms"`
	Bars       []Bar                 `json:"bars"`
	Indicators map[string][]*float64 `json:"indicators"`
	Marks      []Mark                `json:"marks"`
	Latest     map[string]any        `json:"latest"`
	// Forecast is a reversion projection, present only when price is actually
	// displaced from its recent anchor. Absent means "nothing to project",
	// which is the common and correct case.
	Forecast *forecast.Forecast `json:"forecast,omitempty"`
}

func (s *Server) handleKlines(w http.ResponseWriter, r *http.Request) {
	if s.deps.Market == nil {
		http.Error(w, "market client unavailable", http.StatusServiceUnavailable)
		return
	}
	symbol := strings.ToUpper(strings.TrimSpace(r.URL.Query().Get("symbol")))
	interval := strings.TrimSpace(r.URL.Query().Get("interval"))
	if interval == "" {
		interval = "1h"
	}
	if !allowedIntervals[interval] {
		http.Error(w, "unsupported interval: "+interval, http.StatusBadRequest)
		return
	}
	if symbol == "" || len(symbol) > 24 {
		http.Error(w, "symbol required", http.StatusBadRequest)
		return
	}
	limit := queryInt(r, "limit", 300)
	if limit < 2 {
		limit = 2
	}
	if limit > 1000 {
		limit = 1000
	}

	ctx, cancel := context.WithTimeout(r.Context(), 20*time.Second)
	defer cancel()

	var (
		klineRows []binance.Kline
		err       error
	)
	if s.deps.KlinesCache != nil {
		klineRows, err = s.deps.KlinesCache.Get(ctx, s.deps.Market, klinesKey{symbol, interval, limit})
	} else {
		klineRows, err = s.deps.Market.Klines(ctx, symbol, interval, limit)
	}
	if err != nil {
		http.Error(w, "klines unavailable: "+err.Error(), http.StatusBadGateway)
		return
	}
	if len(klineRows) == 0 {
		writeJSON(w, http.StatusOK, ChartResponse{
			Type: "klines", Symbol: symbol, Interval: interval,
			TSMS: time.Now().UnixMilli(), Bars: []Bar{},
			Indicators: map[string][]*float64{}, Marks: []Mark{},
			Latest: map[string]any{},
		})
		return
	}

	writeJSON(w, http.StatusOK, buildChart(symbol, interval, klineRows, s.marksFor(symbol)))
}

// marksFor returns recent onset alerts for one symbol.
func (s *Server) marksFor(symbol string) []Mark {
	if s.deps.Alerts == nil {
		return []Mark{}
	}
	out := make([]Mark, 0, 32)
	for _, alert := range s.deps.Alerts.ForSymbol(symbol, 400) {
		mark := Mark{
			T:        alert.TSMS,
			Kind:     alert.Kind,
			Severity: alert.Severity,
			Score:    alert.Score,
		}
		mark.Letter, mark.Label, mark.Direction = markStyle(alert.Kind)
		mark.Color = markColor(alert.Kind, alert.Severity)
		if v, ok := alert.Metrics["flow_quote"].(float64); ok {
			mark.FlowQuote = v
		}
		if v, ok := alert.Metrics["burst_multiple"].(float64); ok {
			mark.BurstMultiple = v
		}
		out = append(out, mark)
	}
	// Oldest first so a chart library can lay them down in order.
	for i, j := 0, len(out)-1; i < j; i, j = i+1, j-1 {
		out[i], out[j] = out[j], out[i]
	}
	return out
}

// markStyle maps a signal kind to its chart marker: a single letter, a label
// for the tooltip, and a direction the chart uses to place it (pumps below the
// bar, dumps above, which is the usual convention and keeps them from covering
// the candle body).
// Bright pump/dump colours, chosen to stay legible on a dark candle chart
// without being mistaken for the candle bodies themselves.
const (
	markGreen = "#00e676"
	markRed   = "#ff1744"
	markAmber = "#ffab00"
	markBlue  = "#40c4ff"
)

// markColor keys off direction first, so a pump is always green and a dump
// always red regardless of which detector produced it, then falls back to the
// kind for reversals and breakouts.
func markColor(kind, severity string) string {
	switch kind {
	case "PUMP", "BREAKOUT_LONG":
		return markGreen
	case "DUMP", "BREAKOUT_SHORT":
		return markRed
	case "REVERSAL_PUMP":
		return markAmber
	case "REVERSAL_DUMP":
		return markAmber
	case "BREAKOUT":
		return markBlue
	default:
		return markBlue
	}
}

func markStyle(kind string) (letter, label string, direction int) {
	switch kind {
	case "PUMP":
		return "P", "Pump detected", 1
	case "DUMP":
		return "D", "Dump detected", -1
	case "REVERSAL_PUMP":
		return "R", "Reversal up (post-dump squeeze)", 1
	case "REVERSAL_DUMP":
		return "R", "Reversal down (post-pump dump)", -1
	case "BREAKOUT_LONG":
		return "B", "Turtle breakout long", 1
	case "BREAKOUT_SHORT":
		return "B", "Turtle breakout short", -1
	case "BREAKOUT":
		return "B", "Turtle breakout", 1
	default:
		// Never emit an UNKNOWN-style placeholder: fall back to the first
		// letter of the kind, or a neutral dot.
		if kind == "" {
			return "?", "Detection", 1
		}
		return kind[:1], kind, 1
	}
}

func buildChart(symbol, interval string, klines []binance.Kline, marks []Mark) ChartResponse {
	count := len(klines)
	bars := make([]Bar, 0, count)
	closes := make([]float64, 0, count)
	quotes := make([]float64, 0, count)

	for _, k := range klines {
		takerShare := 0.5
		if k.Volume > 0 {
			takerShare = k.TakerBuyBase / k.Volume
		}
		bars = append(bars, Bar{
			T: k.OpenTime, O: k.Open, H: k.High, L: k.Low, C: k.Close,
			V: k.Volume, Q: k.QuoteVolume, N: k.Trades, TB: takerShare,
		})
		closes = append(closes, k.Close)
		quotes = append(quotes, k.QuoteVolume)
	}

	don20 := turtle.DonchianChannel(klines, 20)
	don55 := turtle.DonchianChannel(klines, 55)
	atr := turtle.ATR(klines, 20)
	adxResult := turtle.ADX(klines, 14)
	rsi := turtle.RSI(klines, 14)

	atrPct := make([]float64, count)
	for i := range atr {
		if !math.IsNaN(atr[i]) && closes[i] > 0 {
			atrPct[i] = atr[i] / closes[i]
		} else {
			atrPct[i] = math.NaN()
		}
	}

	indicators := map[string][]*float64{
		"donchian20_upper": optSeries(don20.Upper),
		"donchian20_lower": optSeries(don20.Lower),
		"donchian55_upper": optSeries(don55.Upper),
		"donchian55_lower": optSeries(don55.Lower),
		"ema20":            optSeries(emaSeries(closes, 20)),
		"ema50":            optSeries(emaSeries(closes, 50)),
		"sma200":           optSeries(smaSeries(closes, 200)),
		"atr20":            optSeries(atr),
		"atr_pct":          optSeries(atrPct),
		"volume_median_20": optSeries(rollingMedianPrior(quotes, 20)),
		"adx14":            optSeries(adxResult.ADX),
		"plus_di":          optSeries(adxResult.PlusDI),
		"minus_di":         optSeries(adxResult.MinusDI),
		"rsi14":            optSeries(rsi),
	}

	last := count - 1
	latest := map[string]any{
		"close":            closes[last],
		"atr20":            nullable(atr, last),
		"atr_pct":          nullable(atrPct, last),
		"adx14":            nullable(adxResult.ADX, last),
		"rsi14":            nullable(rsi, last),
		"plus_di":          nullable(adxResult.PlusDI, last),
		"minus_di":         nullable(adxResult.MinusDI, last),
		"donchian20_upper": nullable(don20.Upper, last),
		"donchian20_lower": nullable(don20.Lower, last),
		"donchian55_upper": nullable(don55.Upper, last),
		"donchian55_lower": nullable(don55.Lower, last),
		"trend":            trendOf(closes, last),
	}
	// Volume ratio on the latest bar, using the same prior-20 median the
	// trading gate uses.
	medians := rollingMedianPrior(quotes, 20)
	if last >= 0 && !math.IsNaN(medians[last]) && medians[last] > 0 {
		latest["volume_ratio"] = quotes[last] / medians[last]
	} else {
		latest["volume_ratio"] = nil
	}

	marks = buildMarks(marks, interval)
	if marks == nil {
		marks = []Mark{}
	}
	return ChartResponse{
		Type:       "klines",
		Symbol:     symbol,
		Interval:   interval,
		TSMS:       time.Now().UnixMilli(),
		Bars:       bars,
		Indicators: indicators,
		Marks:      marks,
		Latest:     latest,
		Forecast:   forecast.Compute(klines, intervalMillis[interval], forecast.DefaultParams()),
	}
}

// --------------------------------------------------------------------------
// Series helpers
// --------------------------------------------------------------------------

// optSeries converts a []float64 into the wire form: index-aligned, with null
// wherever the value is undefined (NaN). Alignment matters because the chart
// maps values to bars by index, so the array must never be shortened.
func optSeries(values []float64) []*float64 {
	out := make([]*float64, len(values))
	for i, v := range values {
		if math.IsNaN(v) || math.IsInf(v, 0) {
			continue
		}
		value := v
		out[i] = &value
	}
	return out
}

func nullable(values []float64, index int) any {
	if index < 0 || index >= len(values) || math.IsNaN(values[index]) {
		return nil
	}
	return values[index]
}

// emaSeries is a standard EMA seeded with the SMA of the first `period`
// values, aligned to the input with NaN during warm-up.
func emaSeries(values []float64, period int) []float64 {
	out := nanSlice(len(values))
	if period <= 0 || len(values) < period {
		return out
	}
	var seed float64
	for i := 0; i < period; i++ {
		seed += values[i]
	}
	seed /= float64(period)
	out[period-1] = seed
	multiplier := 2.0 / (float64(period) + 1.0)
	previous := seed
	for i := period; i < len(values); i++ {
		previous = (values[i]-previous)*multiplier + previous
		out[i] = previous
	}
	return out
}

func smaSeries(values []float64, period int) []float64 {
	out := nanSlice(len(values))
	if period <= 0 {
		return out
	}
	var running float64
	for i, v := range values {
		running += v
		if i >= period {
			running -= values[i-period]
		}
		if i >= period-1 {
			out[i] = running / float64(period)
		}
	}
	return out
}

// rollingMedianPrior returns, at each index, the median of the `window` values
// strictly BEFORE it. Prior-only, so a bar never grades its own homework --
// the same convention the volume-confirmation gate uses.
func rollingMedianPrior(values []float64, window int) []float64 {
	out := nanSlice(len(values))
	if window <= 0 {
		return out
	}
	for i := range values {
		if i < window {
			continue
		}
		chunk := make([]float64, window)
		copy(chunk, values[i-window:i])
		out[i] = medianOfCopy(chunk)
	}
	return out
}

func medianOfCopy(values []float64) float64 {
	if len(values) == 0 {
		return math.NaN()
	}
	// Insertion sort: the window is 20 and this runs once per bar.
	for i := 1; i < len(values); i++ {
		for j := i; j > 0 && values[j] < values[j-1]; j-- {
			values[j], values[j-1] = values[j-1], values[j]
		}
	}
	mid := len(values) / 2
	if len(values)%2 == 1 {
		return values[mid]
	}
	return (values[mid-1] + values[mid]) / 2
}

func nanSlice(n int) []float64 {
	out := make([]float64, n)
	for i := range out {
		out[i] = math.NaN()
	}
	return out
}

// trendOf reports the EMA20/EMA50 relationship at `index`.
func trendOf(closes []float64, index int) string {
	fast := emaSeries(closes, 20)
	slow := emaSeries(closes, 50)
	if index < 0 || index >= len(fast) {
		return "unknown"
	}
	f, s := fast[index], slow[index]
	if math.IsNaN(f) || math.IsNaN(s) {
		return "unknown"
	}
	spread := (f - s) / s
	switch {
	case spread > 0.002:
		return "up"
	case spread < -0.002:
		return "down"
	default:
		return "flat"
	}
}
