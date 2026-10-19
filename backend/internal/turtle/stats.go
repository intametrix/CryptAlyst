package turtle

import (
	"math"
	"sort"
	"time"

	"pumpdump/backend/internal/binance"
)

// medianOf returns the median of a copy, leaving the caller's slice untouched.
// Zero for an empty input, which callers must treat as "no usable baseline"
// rather than as a real median.
func medianOf(values []float64) float64 {
	if len(values) == 0 {
		return 0
	}
	cp := make([]float64, len(values))
	copy(cp, values)
	sort.Float64s(cp)
	mid := len(cp) / 2
	if len(cp)%2 == 1 {
		return cp[mid]
	}
	return (cp[mid-1] + cp[mid]) / 2
}

// dropFormingBar removes the final bar when its interval has not closed yet.
//
// Binance's REST klines include the CURRENT, still-forming candle as the last
// element. Evaluating it lets a bar confirm a breakout intrabar and then close
// back inside the channel -- and it corrupts every gate built on top (Donchian
// levels, ATR, the volume confirmation, the extension calculation), because
// they would all be computed from a price that does not exist yet. The plan
// requires completed bars only.
func dropFormingBar(bars []binance.Kline) []binance.Kline {
	if len(bars) == 0 {
		return bars
	}
	last := bars[len(bars)-1]
	if last.CloseTime > 0 && time.Now().UnixMilli() < last.CloseTime {
		return bars[:len(bars)-1]
	}
	return bars
}

// priorHighestHigh returns the highest high of the `period` bars BEFORE the
// final bar. This is the upside level the BTC regime gate needs to unlatch:
// entering risk-off is a downside break, leaving it takes an upside break of
// the same period, and the two distinct levels are what stop the gate flapping.
func priorHighestHigh(bars []binance.Kline, period int) float64 {
	if period <= 0 || len(bars) < period+1 {
		return math.NaN()
	}
	window := bars[len(bars)-period-1 : len(bars)-1]
	high := window[0].High
	for _, b := range window {
		if b.High > high {
			high = b.High
		}
	}
	return high
}

// priorLowestLow returns the lowest low of the `period` bars BEFORE the final
// bar, which is the Donchian level a break below would have to clear. NaN when
// there is not enough history.
func priorLowestLow(bars []binance.Kline, period int) float64 {
	if period <= 0 || len(bars) < period+1 {
		return math.NaN()
	}
	window := bars[len(bars)-1-period : len(bars)-1]
	low := window[0].Low
	for _, b := range window {
		if b.Low < low {
			low = b.Low
		}
	}
	return low
}
