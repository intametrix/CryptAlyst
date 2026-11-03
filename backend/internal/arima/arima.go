// Package arima fits and forecasts ARIMA(p,d,q) models.
//
// Granular price forecasting needs a model that carries its own dynamics rather
// than a fixed rule, and ARIMA is the classical answer: autoregressive terms for
// the persistence that is actually in the series, moving-average terms for the
// shock decay, and differencing so the level is not what gets fitted.
//
// Estimation uses Hannan-Rissanen: a long autoregression produces residuals, and
// a second regression of the differenced series on its own lags plus those
// residuals estimates the ARMA parameters. It is consistent, needs only ordinary
// least squares, and has no convergence failure mode.
package arima

import (
	"math"
	"sort"
)

// Model is a fitted ARIMA(p,d,q) on a log-price series.
type Model struct {
	P, D, Q int
	Phi     []float64 // AR coefficients, length P
	Theta   []float64 // MA coefficients, length Q
	Const   float64   // mean of the differenced series
	// Sigma2 is the residual variance of the fitted model, for intervals.
	Sigma2 float64
	// BIC is the selection criterion used to choose the order.
	BIC float64
	n   int
}

// lagged builds a design row of lagged values and lagged residuals.
func ols(X [][]float64, y []float64) ([]float64, bool) {
	if len(X) == 0 || len(X) != len(y) {
		return nil, false
	}
	d := len(X[0])
	// Normal equations.
	XtX := make([][]float64, d)
	for i := range XtX {
		XtX[i] = make([]float64, d)
	}
	Xty := make([]float64, d)
	for r, row := range X {
		for i := 0; i < d; i++ {
			Xty[i] += row[i] * y[r]
			for j := 0; j < d; j++ {
				XtX[i][j] += row[i] * row[j]
			}
		}
	}
	// Ridders' guard: a tiny ridge keeps a collinear design solvable.
	for i := 0; i < d; i++ {
		XtX[i][i] += 1e-9
	}
	return solve(XtX, Xty)
}

// solve is Gaussian elimination with partial pivoting.
func solve(A [][]float64, b []float64) ([]float64, bool) {
	n := len(b)
	m := make([][]float64, n)
	for i := range m {
		m[i] = append(append([]float64{}, A[i]...), b[i])
	}
	for col := 0; col < n; col++ {
		piv := col
		for r := col + 1; r < n; r++ {
			if math.Abs(m[r][col]) > math.Abs(m[piv][col]) {
				piv = r
			}
		}
		if math.Abs(m[piv][col]) < 1e-12 {
			return nil, false
		}
		m[col], m[piv] = m[piv], m[col]
		for r := col + 1; r < n; r++ {
			f := m[r][col] / m[col][col]
			if f == 0 {
				continue
			}
			for c := col; c <= n; c++ {
				m[r][c] -= f * m[col][c]
			}
		}
	}
	x := make([]float64, n)
	for i := n - 1; i >= 0; i-- {
		s := m[i][n]
		for j := i + 1; j < n; j++ {
			s -= m[i][j] * x[j]
		}
		x[i] = s / m[i][i]
	}
	return x, true
}

// difference applies d-th order differencing.
func difference(x []float64, d int) []float64 {
	out := append([]float64{}, x...)
	for k := 0; k < d; k++ {
		next := make([]float64, 0, len(out)-1)
		for i := 1; i < len(out); i++ {
			next = append(next, out[i]-out[i-1])
		}
		out = next
	}
	return out
}

// Fit estimates ARIMA(p,d,q) on the series using Hannan-Rissanen.
func Fit(x []float64, p, d, q int) (*Model, bool) {
	if len(x) < 40 || p < 0 || q < 0 || d < 0 || d > 2 {
		return nil, false
	}
	y := difference(logs(x), d)
	if len(y) < p+q+20 {
		return nil, false
	}

	mean := 0.0
	for _, v := range y {
		mean += v
	}
	mean /= float64(len(y))
	yc := make([]float64, len(y))
	for i, v := range y {
		yc[i] = v - mean
	}

	// Step 1: a long autoregression to recover the shocks.
	longAR := p + q + 4
	if longAR > len(y)/3 {
		longAR = len(y) / 3
	}
	if longAR < 2 {
		return nil, false
	}
	var Xa [][]float64
	var ya []float64
	for t := longAR; t < len(yc); t++ {
		row := make([]float64, longAR)
		for j := 0; j < longAR; j++ {
			row[j] = yc[t-1-j]
		}
		Xa = append(Xa, row)
		ya = append(ya, yc[t])
	}
	arCoef, ok := ols(Xa, ya)
	if !ok {
		return nil, false
	}
	resid := make([]float64, len(yc))
	for t := longAR; t < len(yc); t++ {
		pred := 0.0
		for j := 0; j < longAR; j++ {
			pred += arCoef[j] * yc[t-1-j]
		}
		resid[t] = yc[t] - pred
	}

	// Step 2: regress on own lags and lagged residuals.
	start := longAR
	if p > start {
		start = p
	}
	if q > start {
		start = q
	}
	start++
	var X [][]float64
	var Y []float64
	for t := start; t < len(yc); t++ {
		row := make([]float64, p+q)
		for j := 0; j < p; j++ {
			row[j] = yc[t-1-j]
		}
		for j := 0; j < q; j++ {
			row[p+j] = resid[t-1-j]
		}
		X = append(X, row)
		Y = append(Y, yc[t])
	}
	if len(X) < p+q+10 {
		return nil, false
	}
	coef, ok := ols(X, Y)
	if !ok {
		return nil, false
	}

	// Residual variance for the fitted order.
	var ss float64
	for i := range X {
		pred := 0.0
		for j, c := range coef {
			pred += c * X[i][j]
		}
		e := Y[i] - pred
		ss += e * e
	}
	n := float64(len(X))
	sigma2 := ss / n
	if sigma2 <= 0 || math.IsNaN(sigma2) {
		sigma2 = 1e-12
	}
	k := float64(p + q + 1)
	bic := n*math.Log(sigma2) + k*math.Log(n)

	m := &Model{P: p, D: d, Q: q, Const: mean, Sigma2: sigma2, BIC: bic, n: len(x)}
	m.Phi = append([]float64{}, coef[:p]...)
	m.Theta = append([]float64{}, coef[p:]...)
	return m, true
}

// Select fits a small grid of orders and returns the lowest BIC.
func Select(x []float64, maxP, maxQ int) (*Model, bool) {
	var best *Model
	for p := 0; p <= maxP; p++ {
		for q := 0; q <= maxQ; q++ {
			m, ok := Fit(x, p, 1, q)
			if !ok {
				continue
			}
			if best == nil || m.BIC < best.BIC {
				best = m
			}
		}
	}
	return best, best != nil
}

// Forecast returns h steps ahead on the original (price) scale.
func (m *Model) Forecast(x []float64, h int) []float64 {
	if m == nil || h < 1 || len(x) == 0 {
		return nil
	}
	lx := logs(x)
	y := difference(lx, m.D)
	if len(y) < 2 {
		return nil
	}
	yc := make([]float64, len(y))
	for i, v := range y {
		yc[i] = v - m.Const
	}
	// Recompute residuals from the fitted recursion so the MA terms are usable.
	resid := make([]float64, len(yc))
	for t := 0; t < len(yc); t++ {
		pred := 0.0
		for j := 0; j < m.P && t-1-j >= 0; j++ {
			pred += m.Phi[j] * yc[t-1-j]
		}
		for j := 0; j < m.Q && t-1-j >= 0; j++ {
			pred += m.Theta[j] * resid[t-1-j]
		}
		resid[t] = yc[t] - pred
	}

	// Recursive forecast; future shocks are zero in expectation.
	ext := append([]float64{}, yc...)
	extRes := append([]float64{}, resid...)
	diffs := make([]float64, h)
	for s := 0; s < h; s++ {
		t := len(ext)
		v := 0.0
		for j := 0; j < m.P && t-1-j >= 0; j++ {
			v += m.Phi[j] * ext[t-1-j]
		}
		for j := 0; j < m.Q && t-1-j >= 0; j++ {
			v += m.Theta[j] * extRes[t-1-j]
		}
		ext = append(ext, v)
		extRes = append(extRes, 0)
		diffs[s] = v
	}

	// Integrate back up to the price level.
	//
	// Each step adds one predicted difference to the running log level. Seeding
	// from the last observed difference as well double-counted the drift and
	// compounded it: on a pure random walk that made the forecast climb 6.7%
	// over 24 bars when the correct answer was "hold the price".
	level := lx[len(lx)-1]
	out := make([]float64, h)
	for s := 0; s < h; s++ {
		level += m.Const + diffs[s]
		out[s] = math.Exp(level)
	}
	return out
}

func logs(x []float64) []float64 {
	out := make([]float64, len(x))
	for i, v := range x {
		if v <= 0 {
			out[i] = 0
			continue
		}
		out[i] = math.Log(v)
	}
	return out
}

// SortedOrders is a helper for reporting which orders were considered.
func SortedOrders(maxP, maxQ int) [][2]int {
	out := make([][2]int, 0, (maxP+1)*(maxQ+1))
	for p := 0; p <= maxP; p++ {
		for q := 0; q <= maxQ; q++ {
			out = append(out, [2]int{p, q})
		}
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i][0] != out[j][0] {
			return out[i][0] < out[j][0]
		}
		return out[i][1] < out[j][1]
	})
	return out
}
