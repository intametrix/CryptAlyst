package screen

import (
	"math"
	"sort"
	"strconv"
	"time"

	"pumpdump/backend/internal/binance"
)

func nowMS() int64 { return time.Now().UnixMilli() }

func pctChange(new, old float64) float64 {
	if old == 0 {
		return 0
	}
	return (new - old) / old
}

func sumQuote(bars []binance.Kline) float64 {
	var sum float64
	for _, b := range bars {
		sum += b.QuoteVolume
	}
	return sum
}

// median returns the middle value of a copy, so callers keep their slice order.
func median(values []float64) float64 {
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

// stdev returns the population standard deviation of the sample.
func stdev(values []float64) float64 {
	n := float64(len(values))
	if n < 2 {
		return 0
	}
	var sum float64
	for _, v := range values {
		sum += v
	}
	mean := sum / n
	var ss float64
	for _, v := range values {
		ss += (v - mean) * (v - mean)
	}
	return math.Sqrt(ss / n)
}

func clampUnit(v float64) float64 { return math.Max(0, math.Min(1, v)) }

func deref(p *float64) float64 {
	if p == nil {
		return 0
	}
	return *p
}

func f0(v float64) string { return strconv.FormatFloat(v, 'f', 0, 64) }
func f1(v float64) string { return strconv.FormatFloat(v, 'f', 1, 64) }
func f2(v float64) string { return strconv.FormatFloat(v, 'f', 2, 64) }

func itoa(n int) string { return strconv.Itoa(n) }

func signedPct(fraction float64) string {
	sign := ""
	if fraction > 0 {
		sign = "+"
	}
	return sign + f2(fraction*100) + "%"
}

// human renders a USD amount compactly: 1.23M / 412.0K / 1234.
func human(v float64) string {
	abs := math.Abs(v)
	switch {
	case abs >= 1e9:
		return f2(v/1e9) + "B"
	case abs >= 1e6:
		return f2(v/1e6) + "M"
	case abs >= 1e3:
		return f1(v/1e3) + "K"
	default:
		return f0(v)
	}
}
