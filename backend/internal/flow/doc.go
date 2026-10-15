// Package flow implements sub-minute volume-flow analysis: the engine that
// answers "has volume suddenly started flowing into this pair, and is price
// beginning to move with it?"
//
// Binance's @ticker stream gives a per-second snapshot whose 24h counters are
// cumulative, so differencing them yields exact per-second traded volume.
// Summing that over a sliding window is the measurement the whole design rests
// on. Three details make it trustworthy:
//
//   - Coverage tracking. If messages were dropped we know how much of the
//     window is really observed, and refuse to score a half-observed burst.
//   - A contamination-resistant baseline whose reference is the LOWER QUARTILE,
//     not the median. Bursts live in the upper tail; anchoring on the median
//     creates a feedback loop where stored bursts ratchet the baseline upward
//     by the cap multiple every round until the symbol goes blind.
//   - A liquidity floor derived from 24h volume, so a near-zero divisor cannot
//     turn a routine print into a "1000x burst".
//
// Locking: ingestion and evaluation both take the engine's write lock. The scan
// runs once per second over a few hundred symbols of purely in-memory work, so
// the critical section is short, and a single lock removes an entire class of
// subtle races over the ring buffer and the baseline.
package flow

import (
	"math"
	"strconv"
)

// Numeric and formatting helpers shared by the engine and its reason strings.

func clamp(v, lo, hi float64) float64 {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

// stdev returns the population standard deviation. 0 for fewer than 2 samples.
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

func parseFloat(s string) float64 {
	if s == "" {
		return 0
	}
	f, err := strconv.ParseFloat(s, 64)
	if err != nil {
		return 0
	}
	return f
}

func f0(v float64) string { return strconv.FormatFloat(v, 'f', 0, 64) }
func f1(v float64) string { return strconv.FormatFloat(v, 'f', 1, 64) }
func f2(v float64) string { return strconv.FormatFloat(v, 'f', 2, 64) }
func f3(v float64) string { return strconv.FormatFloat(v, 'f', 3, 64) }

func signPct(fraction float64) string {
	sign := ""
	if fraction > 0 {
		sign = "+"
	}
	return sign + f2(fraction*100) + "%"
}

// humanMoney renders a USD amount compactly: 1.23B / 18.20M / 412.0K / 1234.
func humanMoney(v float64) string {
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

func describeFlow(window, flow, multiple, z float64) string {
	return "flow " + f0(window) + "s $" + humanMoney(flow) +
		" = " + f2(multiple) + "x baseline (" + f1(z) + "sigma)"
}

func describeMove(window, change, sigma, multiple float64) string {
	return f0(window) + "s move " + signPct(change) +
		" (" + f1(multiple) + "x " + f3(sigma*100) + "% vol)"
}
