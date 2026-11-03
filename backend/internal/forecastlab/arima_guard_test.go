package forecastlab

import (
	"math"
	"testing"
)

// ARIMA must not invent a trend. On a pure random walk the correct forecast is
// the last price, so any meaningful excursion is a modelling failure.
func TestARIMADoesNotDriftOnRandomWalk(t *testing.T) {
	x := make([]float64, 400)
	x[0] = 100
	seed := uint64(12345)
	for i := 1; i < 400; i++ {
		seed = seed*6364136223846793005 + 1442695040888963407
		u := float64(seed>>33) / float64(1<<31)
		x[i] = x[i-1] * (1 + (u-0.5)*0.01)
	}
	out := arimaForecast()(x, 24)
	if len(out) != 24 {
		t.Fatalf("expected 24 steps, got %d", len(out))
	}
	last := x[len(x)-1]
	end := out[len(out)-1]
	if math.Abs(end/last-1) > 0.01 {
		t.Fatalf("ARIMA drifted %.2f%% over 24 bars of a random walk", (end/last-1)*100)
	}
	t.Logf("drift over 24 bars: %+.3f%%", (end/last-1)*100)
}

// And it must still be able to follow real persistence when it exists.
func TestARIMAFollowsRealMomentum(t *testing.T) {
	x := make([]float64, 400)
	x[0] = 100
	r := 0.0
	seed := uint64(99)
	for i := 1; i < 400; i++ {
		seed = seed*6364136223846793005 + 1442695040888963407
		u := float64(seed>>33) / float64(1<<31)
		r = 0.6*r + (u-0.5)*0.004 // persistent returns
		x[i] = x[i-1] * (1 + r)
	}
	out := arimaForecast()(x, 12)
	if len(out) != 12 {
		t.Fatalf("expected 12 steps, got %d", len(out))
	}
	for i, v := range out {
		if math.IsNaN(v) || v <= 0 {
			t.Fatalf("step %d unusable: %v", i, v)
		}
	}
	t.Logf("last %.2f -> %.2f after 12 bars with momentum", x[len(x)-1], out[len(out)-1])
}
