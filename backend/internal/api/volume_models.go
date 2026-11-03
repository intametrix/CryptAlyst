package api

import (
	"math"

	"pumpdump/backend/internal/binance"
)

// Volume-weighted forecasts.
//
// Every formula model in the lab sees only closes. Volume says how much
// conviction is behind a price: a level reached on heavy volume is a different
// claim from the same level on thin trade, and a slope carried by rising volume
// is not the same slope as one drifting on fading volume. These models put that
// into the forecast.

// vwapLevel is the volume-weighted mean price over the last n bars: the average
// price actually paid, rather than the average price printed.
func vwapLevel(bars []binance.Kline, n int) (float64, bool) {
	if len(bars) < 2 {
		return 0, false
	}
	if n <= 0 || n > len(bars) {
		n = len(bars)
	}
	var pv, v float64
	for _, b := range bars[len(bars)-n:] {
		if b.Volume <= 0 {
			continue
		}
		pv += b.Close * b.Volume
		v += b.Volume
	}
	if v <= 0 {
		return 0, false
	}
	return pv / v, true
}

// vwapForecast converges from the last close toward the traded average, the same
// shape a level model uses, so the path stays continuous with the chart.
func vwapForecast(bars []binance.Kline, horizon int, lookback int) ([]float64, bool) {
	if len(bars) < horizon+lookback {
		return nil, false
	}
	level, ok := vwapLevel(bars, lookback)
	if !ok {
		return nil, false
	}
	anchor := bars[len(bars)-1].Close
	out := make([]float64, horizon)
	half := math.Max(1, float64(horizon)/2)
	for i := range out {
		w := 1 - math.Pow(0.5, float64(i+1)/half)
		out[i] = anchor + (level-anchor)*w
	}
	return out, true
}

// vwTrend fits a line whose every bar is weighted by its own volume, so heavy
// bars steer the slope and thin ones barely register.
func vwTrend(bars []binance.Kline, horizon int, lookback int) ([]float64, bool) {
	if lookback < 2 {
		lookback = 2
	}
	if len(bars) < lookback+horizon {
		return nil, false
	}
	seg := bars[len(bars)-lookback:]
	var sw, swx, swy, swxy, swxx float64
	for i, b := range seg {
		w := b.Volume
		if w <= 0 {
			w = 1e-9
		}
		x := float64(i)
		y := b.Close
		sw += w
		swx += w * x
		swy += w * y
		swxy += w * x * y
		swxx += w * x * x
	}
	if sw <= 0 {
		return nil, false
	}
	den := sw*swxx - swx*swx
	if math.Abs(den) < 1e-12 {
		return nil, false
	}
	slope := (sw*swxy - swx*swy) / den
	anchor := seg[len(seg)-1].Close
	out := make([]float64, horizon)
	for i := range out {
		out[i] = anchor + slope*float64(i+1)
	}
	return out, true
}

// vwapDrift scales the recent move by how unusual the volume is: the same
// return means more when it arrived on exceptional participation.
func vwapDrift(bars []binance.Kline, horizon int, lookback int) ([]float64, bool) {
	// The volume baseline needs 21 bars; the lookback itself can be as short as
	// the move it measures. Refusing small lookbacks here made vwdrift unavailable
	// for the configurations the optimiser actually picks (lb2, lb3).
	if lookback < 1 {
		lookback = 1
	}
	if len(bars) < lookback+22 {
		return nil, false
	}
	seg := bars[len(bars)-lookback:]
	base := seg[0].Close
	last := seg[len(seg)-1].Close
	if base == 0 {
		return nil, false
	}
	move := last/base - 1

	// Volume z-score of the final bar against the preceding window.
	var sum, sq float64
	for _, b := range bars[len(bars)-21 : len(bars)-1] {
		sum += b.Volume
		sq += b.Volume * b.Volume
	}
	mean := sum / 20
	sd := math.Sqrt(math.Max(0, sq/20-mean*mean))
	z := 0.0
	if sd > 1e-12 {
		z = (bars[len(bars)-1].Volume - mean) / sd
	}
	// Damp the move, then scale by participation, capped so a single print
	// cannot throw the forecast.
	scale := 1 + 0.1*math.Max(-2, math.Min(2, z))
	drift := move * 0.35 * scale
	if math.Abs(drift) > 0.25 {
		drift = math.Copysign(0.25, drift)
	}
	out := make([]float64, horizon)
	for i := range out {
		out[i] = last * (1 + drift*float64(i+1)/float64(horizon))
	}
	return out, true
}
