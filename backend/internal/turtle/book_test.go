package turtle

import (
	"math"
	"testing"
)

func approx(a, b, tol float64) bool { return math.Abs(a-b) <= tol }

// N must be Wilder-smoothed, not a simple mean. The book is explicit and every
// sizing and stop decision inherits the difference.
func TestNWilderSmoothing(t *testing.T) {
	s := &BookState{Config: BookDefaults()}
	s.Config.NLookback = 3 // N = (2*prevN + TR)/3

	s.UpdateN(10, 8, 0)   // TR=2, seed N=2
	s.UpdateN(11, 9, 10)  // TR=max(2, 1, 1)=2 -> N=(2*2+2)/3=2
	s.UpdateN(14, 12, 11) // TR=max(2,3,1)=3 -> N=(2*2+3)/3=2.3333
	want := (2*2.0 + 3.0) / 3.0
	if !approx(s.N, want, 1e-9) {
		t.Errorf("N = %.6f, want %.6f (Wilder smoothing)", s.N, want)
	}
}

// Unit size is 1% of equity per 1N of movement.
func TestUnitSize(t *testing.T) {
	s := &BookState{Config: BookDefaults(), N: 2.0}
	units, ok := s.UnitSize(300, 100)
	if !ok {
		t.Fatal("expected a valid unit size")
	}
	// 1% of 300 = 3 USDT of risk over a 2.0 move = 1.5 units.
	if !approx(units, 1.5, 1e-9) {
		t.Errorf("units = %.6f, want 1.5", units)
	}
}

// Zero N must refuse rather than divide by zero or round up.
func TestUnitSizeRefusesWithoutN(t *testing.T) {
	s := &BookState{Config: BookDefaults()}
	if _, ok := s.UnitSize(300, 100); ok {
		t.Error("must refuse sizing when N is unknown")
	}
}

// The 2N stop sits 2N adverse from entry.
func TestStopFor(t *testing.T) {
	s := &BookState{Config: BookDefaults(), N: 2.0}
	if got := s.StopFor(100, 1); !approx(got, 96, 1e-9) {
		t.Errorf("long stop = %.4f, want 96", got)
	}
	if got := s.StopFor(100, -1); !approx(got, 104, 1e-9) {
		t.Errorf("short stop = %.4f, want 104", got)
	}
}

// Pyramiding: add at 0.5N in favour, and the WHOLE position's stop moves to 2N
// from the newest unit. This is the rule most implementations get wrong.
func TestPyramidAndStopMoves(t *testing.T) {
	s := &BookState{Config: BookDefaults(), N: 2.0}
	step := 0.5 * s.N // 1.0

	if s.ShouldAddUnit(100, 100, 100.5, 1, 1) {
		t.Error("must not add before a full 0.5N move")
	}
	if !s.ShouldAddUnit(100, 100, 101.0, 1, 1) {
		t.Error("must add at exactly 0.5N in favour")
	}
	// The cap is 4 units per market.
	if s.ShouldAddUnit(100, 101, 102, 1, 4) {
		t.Error("must not exceed MaxUnitsPerMarket")
	}
	// After adding at 101, the stop for the whole position is 101 - 2N = 97,
	// NOT the original 100 - 2N = 96.
	if got := s.AddUnitStop(101, 1); !approx(got, 97, 1e-9) {
		t.Errorf("stop after adding = %.4f, want 97 (2N from the NEWEST unit)", got)
	}
	_ = step
}

// System 1 is skipped when the last breakout was a winner, and System 2 is a
// strictly stronger statement that takes precedence.
func TestBreakoutPrecedenceAndSkip(t *testing.T) {
	s := &BookState{Config: BookDefaults(), N: 1, High20: 100, Low20: 90, High55: 110, Low55: 80}

	if sys, dir, ok := s.Breakout(105); !ok || sys != System1 || dir != 1 {
		t.Errorf("105 in a 100/90 20-channel must be a System 1 long, got %v %d %v", sys, dir, ok)
	}
	if sys, _, ok := s.Breakout(115); !ok || sys != System2 {
		t.Errorf("115 through a 110 55-high must be System 2, got %v %v", sys, ok)
	}
	// A 55-break must win even when the 20-channel is also broken.
	if sys, _, ok := s.Breakout(111); !ok || sys != System2 {
		t.Errorf("111 through both channels must report System 2, got %v %v", sys, ok)
	}

	s.LastBreakoutWasWinner = true
	if _, _, ok := s.Breakout(105); ok {
		t.Error("System 1 must be SKIPPED when the last breakout was a winner")
	}
	if _, _, ok := s.Breakout(115); !ok {
		t.Error("System 2 must still fire when System 1 is skipped")
	}
}

// Correlation: identical series are 1.0, opposite are -1.0, and a pair with too
// few samples is OMITTED rather than reported as zero.
func TestCorrelation(t *testing.T) {
	// Explicit returns with REAL variation. Deriving them from monotone price
	// series was the first attempt and it was wrong: a steadily rising series has
	// near-constant returns, so the correlation is ill-conditioned and two
	// opposite trends scored +0.9997. The fixture was broken, not the maths.
	up := []float64{0.02, -0.01, 0.03, -0.02, 0.01, -0.015, 0.025}
	down := make([]float64, len(up))
	for i, v := range up {
		down[i] = -v
	}
	m := CorrelationMatrix([]ReturnSeries{
		{Symbol: "A", Returns: up}, {Symbol: "B", Returns: up}, {Symbol: "C", Returns: down},
	}, 0.7, 5)
	if len(m) != 3 {
		t.Fatalf("want 3 pairs, got %d", len(m))
	}
	for _, c := range m {
		if c.A == "A" && c.B == "B" && !approx(c.Rho, 1, 1e-9) {
			t.Errorf("identical series rho = %.6f, want 1", c.Rho)
		}
		if c.A == "A" && c.B == "C" && !approx(c.Rho, -1, 1e-9) {
			t.Errorf("opposite series rho = %.6f, want -1", c.Rho)
		}
		// |rho| = 1 clears any sane threshold, so all three count as one group.
		if !c.Group {
			t.Errorf("%s/%s should be grouped at |rho|=1", c.A, c.B)
		}
	}

	// Too few samples -> omitted, not zero.
	short := CorrelationMatrix([]ReturnSeries{
		{Symbol: "X", Returns: []float64{0.1}}, {Symbol: "Y", Returns: []float64{0.2}},
	}, 0.7, 5)
	if len(short) != 0 {
		t.Errorf("an unmeasurable pair must be OMITTED, got %d", len(short))
	}
}

// Dedupe keeps the preference order and names what it dropped and why.
func TestDedupeByCorrelation(t *testing.T) {
	m := []PairCorrelation{
		{A: "ETHUSDT", B: "SOLUSDT", Rho: 0.95, Group: true},
		{A: "ETHUSDT", B: "XRPUSDT", Rho: 0.2, Group: false},
	}
	kept, dropped := DedupeByCorrelation([]string{"ETHUSDT", "SOLUSDT", "XRPUSDT"}, m, 0.7)
	if len(kept) != 2 || kept[0] != "ETHUSDT" || kept[1] != "XRPUSDT" {
		t.Errorf("kept = %v, want [ETHUSDT XRPUSDT] in preference order", kept)
	}
	if len(dropped) != 1 {
		t.Errorf("dropped = %v, want one entry naming the redundant pair", dropped)
	}
}

// GroupUnits sums the correlated exposure the book's 6-unit cap constrains.
func TestGroupUnits(t *testing.T) {
	m := []PairCorrelation{{A: "ETHUSDT", B: "SOLUSDT", Rho: 0.9, Group: true}}
	held := map[string]int{"ETHUSDT": 4, "SOLUSDT": 2, "XRPUSDT": 3}
	if got := GroupUnits("ETHUSDT", held, m); got != 2 {
		t.Errorf("group units for ETH = %d, want 2 (SOL only)", got)
	}
}

// ReturnsFromPrices is tested on its own now that it is no longer used to build
// the correlation fixture.
func TestReturnsFromPrices(t *testing.T) {
	r := ReturnsFromPrices([]float64{100, 110, 99})
	if len(r) != 2 {
		t.Fatalf("want 2 returns, got %d", len(r))
	}
	if !approx(r[0], 0.1, 1e-9) {
		t.Errorf("r[0] = %.6f, want 0.1", r[0])
	}
	if !approx(r[1], -0.1, 1e-9) {
		t.Errorf("r[1] = %.6f, want -0.1", r[1])
	}
	if len(ReturnsFromPrices([]float64{5})) != 0 {
		t.Error("a single price has no returns")
	}
}
