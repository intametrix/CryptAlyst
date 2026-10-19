package turtle

import (
	"math"
	"sort"
)

// Correlation, used for two things the book treats as one.
//
// The book caps exposure to a GROUP of closely correlated markets at 6 units
// while allowing 4 in any single one. In futures the groups were given by hand --
// currencies, bonds, metals, grains. Crypto has no such table and the obvious
// substitutes are wrong: "all alts" is too coarse to be a limit, and "same
// sector" is a narrative rather than a measurement.
//
// So the group is MEASURED from returns. Two markets are treated as one for
// position-limit purposes when |rho| is at or above the threshold, because a
// correlation of -0.9 is a hedge in a textbook and a single macro factor in
// practice: the pair moves together in magnitude, and both legs get stopped out
// by the same move.
//
// The second use is selection: the human asked for correlations "to understand
// similarity", and the practical consequence is that the top-ranked ten
// candidates are frequently one trade wearing ten tickers. Selecting the best few
// WITHOUT that check is how a system ends up with 3 positions and 1 bet.

// ReturnSeries is one symbol's returns, oldest first, aligned by index.
type ReturnSeries struct {
	Symbol  string    `json:"symbol"`
	Returns []float64 `json:"-"`
}

// PairCorrelation is one measured pair.
type PairCorrelation struct {
	A     string  `json:"a"`
	B     string  `json:"b"`
	Rho   float64 `json:"rho"`
	Group bool    `json:"counts_as_one_group"`
}

// CorrelationMatrix computes pairwise Pearson correlation on aligned returns.
//
// Pairs with fewer than minSamples overlapping returns are OMITTED rather than
// reported as zero. A correlation of zero means "measured, unrelated"; a missing
// measurement means "not known", and conflating them would let an unmeasured pair
// slip past the group limit -- the exact failure the limit exists to prevent.
func CorrelationMatrix(series []ReturnSeries, threshold float64, minSamples int) []PairCorrelation {
	out := []PairCorrelation{}
	for i := 0; i < len(series); i++ {
		for j := i + 1; j < len(series); j++ {
			rho, ok := pearson(series[i].Returns, series[j].Returns, minSamples)
			if !ok {
				continue
			}
			out = append(out, PairCorrelation{
				A: series[i].Symbol, B: series[j].Symbol, Rho: rho,
				Group: math.Abs(rho) >= threshold,
			})
		}
	}
	sort.SliceStable(out, func(a, b int) bool {
		return math.Abs(out[a].Rho) > math.Abs(out[b].Rho)
	})
	return out
}

func pearson(a, b []float64, minSamples int) (float64, bool) {
	n := len(a)
	if len(b) < n {
		n = len(b)
	}
	if n < minSamples || n < 3 {
		return 0, false
	}
	// Compare the most recent n observations, which is the window that matters.
	a, b = a[len(a)-n:], b[len(b)-n:]

	var sa, sb float64
	for i := 0; i < n; i++ {
		sa += a[i]
		sb += b[i]
	}
	ma, mb := sa/float64(n), sb/float64(n)

	var num, da, db float64
	for i := 0; i < n; i++ {
		da_ := a[i] - ma
		db_ := b[i] - mb
		num += da_ * db_
		da += da_ * da_
		db += db_ * db_
	}
	if da <= 0 || db <= 0 {
		// A constant series has no correlation to measure. Not zero: undefined.
		return 0, false
	}
	return num / math.Sqrt(da*db), true
}

// GroupUnits sums the units already held across everything correlated with symbol,
// which is the quantity the book's 6-unit limit constrains.
func GroupUnits(symbol string, held map[string]int, matrix []PairCorrelation) int {
	total := 0
	for _, c := range matrix {
		if !c.Group {
			continue
		}
		if c.A == symbol {
			total += held[c.B]
		} else if c.B == symbol {
			total += held[c.A]
		}
	}
	return total
}

// DedupeByCorrelation removes candidates that are effectively the same trade as
// one already chosen.
//
// The kept candidate is the FIRST one seen, so callers must pass candidates in
// preference order. This is deliberate: the function's job is to remove
// redundancy, not to re-rank, and a function that silently re-ranks would make
// the caller's ordering meaningless.
func DedupeByCorrelation(ordered []string, matrix []PairCorrelation, threshold float64) (kept, dropped []string) {
	chosen := map[string]bool{}
	for _, sym := range ordered {
		redundantWith := ""
		for _, c := range matrix {
			if math.Abs(c.Rho) < threshold {
				continue
			}
			if c.A == sym && chosen[c.B] {
				redundantWith = c.B
				break
			}
			if c.B == sym && chosen[c.A] {
				redundantWith = c.A
				break
			}
		}
		if redundantWith != "" {
			dropped = append(dropped, sym+"~"+redundantWith)
			continue
		}
		chosen[sym] = true
		kept = append(kept, sym)
	}
	return kept, dropped
}

// ReturnsFromPrices converts a price series into simple returns, oldest first.
func ReturnsFromPrices(prices []float64) []float64 {
	if len(prices) < 2 {
		return nil
	}
	out := make([]float64, 0, len(prices)-1)
	for i := 1; i < len(prices); i++ {
		if prices[i-1] <= 0 {
			continue
		}
		out = append(out, (prices[i]-prices[i-1])/prices[i-1])
	}
	return out
}
