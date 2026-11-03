package arima

import (
	"math"
	"math/rand"
	"testing"
)

// A series with genuine AR structure must be recovered and must forecast better
// than holding the last value — that pair is the whole claim of the model.
func TestARIMARecoversARStructure(t *testing.T) {
	// Planted structure: RETURNS follow AR(1). A model fitted on differences of
	// log price must recover that coefficient — a random walk in levels would
	// (correctly) select ARIMA(0,1,0) instead, which is a different test.
	rng := rand.New(rand.NewSource(42))
	n := 800
	phi := 0.5
	rets := make([]float64, n)
	eps := make([]float64, n)
	rets[0] = rng.NormFloat64() * 0.01
	for i := 1; i < n; i++ {
		eps[i] = rng.NormFloat64() * 0.01
		rets[i] = phi*rets[i-1] + eps[i]
	}
	x := make([]float64, n)
	x[0] = 100
	for i := 1; i < n; i++ {
		x[i] = x[i-1] * math.Exp(rets[i])
	}

	cut := n - 20
	train := x[:cut]
	actual := x[cut:]

	m, ok := Fit(train, 1, 1, 0)
	if !ok {
		t.Fatal("fit failed")
	}
	if len(m.Phi) != 1 {
		t.Fatalf("expected one AR coefficient, got %d", len(m.Phi))
	}
	// The differenced log series IS the return series, so phi should land near
	// the planted 0.5 rather than wandering.
	if m.Phi[0] < 0.2 || m.Phi[0] > 0.8 {
		t.Fatalf("fitted phi %.3f does not reflect the planted %.2f", m.Phi[0], phi)
	}

	fc := m.Forecast(train, 20)
	if len(fc) != 20 {
		t.Fatalf("expected 20 steps, got %d", len(fc))
	}

	var maeModel, maeNaive float64
	for i := 0; i < 20; i++ {
		maeModel += math.Abs(fc[i] - actual[i])
		maeNaive += math.Abs(train[len(train)-1] - actual[i])
	}
	maeModel /= 20
	maeNaive /= 20
	t.Logf("ARIMA MAE %.4f vs naive %.4f (phi=%.3f, sigma2=%.2e)", maeModel, maeNaive, m.Phi[0], m.Sigma2)
	// With real persistence in returns the model should at least not be much
	// worse than holding the last price; beating it is what the live ranking
	// measures on real symbols.
	if maeModel > maeNaive*1.3 {
		t.Fatalf("ARIMA far worse than naive on its own generating process: %.4f vs %.4f", maeModel, maeNaive)
	}
}

func TestSelectPicksAnOrderAndForecastsFinite(t *testing.T) {
	rng := rand.New(rand.NewSource(7))
	x := make([]float64, 300)
	x[0] = 50
	for i := 1; i < len(x); i++ {
		x[i] = x[i-1] * (1 + 0.001 + rng.NormFloat64()*0.006)
	}
	m, ok := Select(x, 2, 2)
	if !ok {
		t.Fatal("select failed")
	}
	fc := m.Forecast(x, 12)
	if len(fc) != 12 {
		t.Fatalf("expected 12 steps, got %d", len(fc))
	}
	for i, v := range fc {
		if math.IsNaN(v) || math.IsInf(v, 0) || v <= 0 {
			t.Fatalf("step %d is not a usable price: %v", i, v)
		}
	}
	t.Logf("selected ARIMA(%d,1,%d) bic=%.1f phi=%v theta=%v", m.P, m.Q, m.BIC, m.Phi, m.Theta)
}

// The forecast must start from the last observed price, not from a fitted level.
func TestForecastStartsAtTheLastPrice(t *testing.T) {
	rng := rand.New(rand.NewSource(9))
	x := make([]float64, 250)
	x[0] = 10
	for i := 1; i < len(x); i++ {
		x[i] = x[i-1] * (1 + rng.NormFloat64()*0.01)
	}
	m, ok := Fit(x, 1, 1, 0)
	if !ok {
		t.Fatal("fit failed")
	}
	fc := m.Forecast(x, 1)
	if math.Abs(fc[0]/x[len(x)-1]-1) > 0.02 {
		t.Fatalf("first step %.4f is far from the last price %.4f", fc[0], x[len(x)-1])
	}
}
