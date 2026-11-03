package api

import (
	"context"
	"math"
	"net/http"
	"time"

	"pumpdump/backend/internal/binance"
)

// A model that reads price, volume AND time.
//
// Every other price model here sees one column of closes. That throws away two
// things the market publishes: how much was traded (volume carries participation
// and exhaustion) and when the bar happened (crypto has strong intraday and
// weekday structure). This fits a linear model on those columns and forecasts
// the horizon return from them, walk-forward so the fit never sees its own test.

// barFeatures is one training row: predictors for the next `horizon` bars.
type barFeatures struct {
	R1, R2, R5, R10  float64
	RetVol           float64 // 20-bar realised volatility
	VolZ             float64 // volume z-score over 20 bars
	HourSin, HourCos float64
	DowSin, DowCos   float64
}

// featuresAt builds the predictor row for bar i. Returns false when there is not
// enough history behind i.
func featuresAt(bars []binance.Kline, i int) (barFeatures, bool) {
	if i < 21 || i >= len(bars) {
		return barFeatures{}, false
	}
	c := func(k int) float64 { return bars[k].Close }
	if c(i) == 0 || c(i-1) == 0 || c(i-2) == 0 || c(i-5) == 0 || c(i-10) == 0 {
		return barFeatures{}, false
	}
	f := barFeatures{
		R1:  c(i)/c(i-1) - 1,
		R2:  c(i)/c(i-2) - 1,
		R5:  c(i)/c(i-5) - 1,
		R10: c(i)/c(i-10) - 1,
	}
	var sq float64
	for j := i - 19; j <= i; j++ {
		if j > 0 && c(j-1) != 0 {
			r := c(j)/c(j-1) - 1
			sq += r * r
		}
	}
	f.RetVol = math.Sqrt(sq / 20)

	var volSum, volSq float64
	for j := i - 19; j <= i; j++ {
		volSum += bars[j].Volume
		volSq += bars[j].Volume * bars[j].Volume
	}
	mean := volSum / 20
	sd := math.Sqrt(math.Max(0, volSq/20-mean*mean))
	if sd > 0 {
		f.VolZ = (bars[i].Volume - mean) / sd
	}

	// Clock features: sin/cos pairs so 23:00 sits next to 00:00.
	t := time.UnixMilli(bars[i].OpenTime).UTC()
	hour := float64(t.Hour()) + float64(t.Minute())/60
	f.HourSin = math.Sin(2 * math.Pi * hour / 24)
	f.HourCos = math.Cos(2 * math.Pi * hour / 24)
	dow := float64(t.Weekday())
	f.DowSin = math.Sin(2 * math.Pi * dow / 7)
	f.DowCos = math.Cos(2 * math.Pi * dow / 7)
	return f, true
}

func (f barFeatures) row() []float64 {
	return []float64{f.R1, f.R2, f.R5, f.R10, f.RetVol, f.VolZ, f.HourSin, f.HourCos, f.DowSin, f.DowCos}
}

// fitReturnModel regresses the forward horizon return on the feature rows that
// end before `cut`, so the coefficients never see the forecast window.
func fitReturnModel(bars []binance.Kline, cut, horizon int) ([]float64, bool) {
	var X [][]float64
	var y []float64
	for i := 21; i+horizon < cut; i++ {
		f, ok := featuresAt(bars, i)
		if !ok {
			continue
		}
		if bars[i].Close == 0 {
			continue
		}
		X = append(X, f.row())
		y = append(y, bars[i+horizon].Close/bars[i].Close-1)
	}
	if len(X) < 60 {
		return nil, false
	}
	coef, ok := olsRidge(X, y)
	return coef, ok
}

// olsRidge solves the normal equations with a small ridge, so a collinear
// feature block cannot make the fit singular.
func olsRidge(X [][]float64, y []float64) ([]float64, bool) {
	d := len(X[0])
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
	for i := 0; i < d; i++ {
		XtX[i][i] += 1e-6
	}
	return gaussSolve(XtX, Xty)
}

// gaussSolve is Gaussian elimination with partial pivoting.
func gaussSolve(A [][]float64, b []float64) ([]float64, bool) {
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

// mlrForecast produces a path from the fitted horizon-return model.
func mlrForecast(bars []binance.Kline, horizon int) ([]float64, bool) {
	cut := len(bars) - horizon
	if cut < 80 {
		return nil, false
	}
	coef, ok := fitReturnModel(bars, cut, horizon)
	if !ok {
		return nil, false
	}
	f, ok := featuresAt(bars, cut-1)
	if !ok {
		return nil, false
	}
	row := f.row()
	pred := 0.0
	for i, c := range coef {
		pred += c * row[i]
	}
	// A predicted horizon return beyond a few daily ranges is a bad fit, not a
	// forecast; hold the price in that case.
	if math.IsNaN(pred) || math.Abs(pred) > 0.25 {
		return nil, false
	}
	base := bars[cut-1].Close
	out := make([]float64, horizon)
	for i := 0; i < horizon; i++ {
		// Spread the predicted return across the horizon so the path moves
		// gradually instead of jumping on bar one.
		out[i] = base * (1 + pred*float64(i+1)/float64(horizon))
	}
	return out, true
}

// handleMLR exposes the price+volume+time model with the coefficients it learned,
// so the fit is inspectable rather than a black box.
func (s *Server) handleMLR(w http.ResponseWriter, r *http.Request) {
	symbol := paramSymbol(r)
	if symbol == "" {
		http.Error(w, "symbol required", http.StatusBadRequest)
		return
	}
	interval := paramInterval(r, "1h")
	horizon := queryInt(r, "horizon", 24)
	if horizon < 1 || horizon > 720 {
		horizon = 24
	}
	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()
	if s.deps.Market == nil {
		fail(w, "market client unavailable")
		return
	}
	bars, err := s.barsFor(ctx, symbol, interval, 500)
	if err != nil || len(bars) < 120+horizon {
		fail(w, "not enough bars with volume")
		return
	}
	path, ok := mlrForecast(bars, horizon)
	if !ok {
		fail(w, "fit failed")
		return
	}
	cut := len(bars) - horizon
	actual := make([]float64, horizon)
	for i := range actual {
		actual[i] = bars[cut+i].Close
	}
	var sumAbs, sumPct float64
	for i := range actual {
		e := math.Abs(path[i] - actual[i])
		sumAbs += e
		if actual[i] != 0 {
			sumPct += e / actual[i]
		}
	}
	coef, _ := fitReturnModel(bars, cut, horizon)
	writeJSON(w, http.StatusOK, map[string]any{
		"ok": true, "symbol": symbol, "interval": interval, "horizon": horizon,
		"forecast": path, "actual": actual,
		"errors": map[string]any{
			"mae": sumAbs / float64(horizon), "mape_pct": 100 * sumPct / float64(horizon),
		},
		"features": []string{
			"1-bar return", "2-bar return", "5-bar return", "10-bar return",
			"20-bar volatility", "volume z-score", "hour sin", "hour cos", "weekday sin", "weekday cos",
		},
		"coefficients": coef,
	})
}
