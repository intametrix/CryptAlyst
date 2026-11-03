// Package forecastlab grades forecasting models against what actually
// happened, so the chart can show a forecast next to the truth instead of a
// confident line nobody checked.
//
// The rule this package exists to enforce: a forecast is only worth drawing if
// it beats the alternatives on data it was not fitted to. Every model here is
// therefore scored by walk-forward evaluation — fit on a prefix of the series,
// forecast the next N bars, compare with the bars that really followed, then
// slide the cut forward and repeat. The leaderboard is the output; the single
// "best" model is whatever wins on out-of-sample error, not on preference.
package forecastlab

import (
	"fmt"
	"math"
	"pumpdump/backend/internal/arima"
	"sort"
)

// Point is one close at a timestamp.
type Point struct {
	T int64   `json:"t"`
	V float64 `json:"v"`
}

// Model is a named forecasting function: given closes and a horizon, return
// exactly horizon predicted closes.
type Model struct {
	Name string `json:"name"`
	Note string `json:"note"`
	Fn   func(closes []float64, horizon int) []float64
}

// Models returns the hyperparameter grid the lab searches.
//
// This is a search space, not a shortlist: several lookbacks for the mean
// models, a range of alphas for the exponential ones, several trend windows and
// damping factors, plus an adaptive ensemble. Ranking the whole grid is how a
// "best" claim is earned — a single hand-picked model would be an opinion.
func Models() []Model {
	models := []Model{
		{Name: "naive", Note: "last close held flat", Fn: flat(lastValue)},
		{Name: "mean", Note: "mean of the whole window", Fn: flat(meanValue)},
	}
	for _, n := range []int{3, 5, 8, 10, 15, 20, 30, 50, 80, 100, 150} {
		n := n
		models = append(models, Model{
			Name: fmt.Sprintf("sma%d", n),
			Note: fmt.Sprintf("%d-bar simple mean held flat", n),
			Fn:   flat(smaValue(n)),
		})
	}
	for _, a := range []float64{0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.6} {
		a := a
		models = append(models, Model{
			Name: fmt.Sprintf("ema%d", int(a*100)),
			Note: fmt.Sprintf("exponential mean (alpha %.2f) held flat", a),
			Fn:   flat(emaValue(a)),
		})
	}
	models = append(models, Model{
		Name: "trend", Note: "least-squares line over the whole window",
		Fn: trendLine(0, 1.0),
	})
	for _, lb := range []int{20, 30, 50, 75, 100, 150} {
		lb := lb
		models = append(models, Model{
			Name: fmt.Sprintf("trend%d", lb),
			Note: fmt.Sprintf("least-squares line over the last %d bars", lb),
			Fn:   trendLine(lb, 1.0),
		})
	}
	for _, d := range []float64{0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 0.98} {
		d := d
		models = append(models, Model{
			Name: fmt.Sprintf("damped%d", int(d*100)),
			Note: fmt.Sprintf("least-squares line damped %.0f%% per step", d*100),
			Fn:   trendLine(0, d),
		})
	}
	// ARIMA: the order is chosen by BIC on the training window, so the model
	// carries its own dynamics instead of a fixed rule.
	models = append(models, Model{
		Name: "arima",
		Note: "ARIMA(p,1,q) with the order selected by BIC",
		Fn:   arimaForecast(),
	})
	// Retrieval: forecast from what actually followed similar past windows.
	for _, k := range []int{5, 10, 20} {
		k := k
		models = append(models, Model{
			Name: fmt.Sprintf("analog%d", k),
			Note: fmt.Sprintf("mean outcome of the %d most similar past windows", k),
			Fn:   analog(k, analogWindow),
		})
	}
	// Combinations: averaging models that fail differently is the cheapest way
	// to cut variance, and blending three horizons of the same idea is a real
	// hyperparameter choice rather than a fixed guess.
	for _, w := range []struct {
		name  string
		smaN  int
		alpha float64
		damp  float64
	}{{"ens_a", 20, 0.30, 0.90}, {"ens_b", 10, 0.20, 0.95}, {"ens_c", 30, 0.40, 0.80}} {
		w := w
		models = append(models, Model{
			Name: w.name,
			Note: fmt.Sprintf("mean of sma%d, ema%.2f and damped %.2f", w.smaN, w.alpha, w.damp),
			Fn:   blend(w.smaN, w.alpha, w.damp),
		})
	}
	return models
}

// blend averages a flat mean, an exponential mean and a damped line.
func blend(smaN int, alpha, damp float64) func([]float64, int) []float64 {
	return func(closes []float64, horizon int) []float64 {
		a := smaValue(smaN)(closes)
		b := emaValue(alpha)(closes)
		c := trendLine(0, damp)(closes, horizon)
		out := make([]float64, horizon)
		for i := range out {
			v := (a + b) / 2
			if i < len(c) {
				v = (a + b + c[i]) / 3
			}
			out[i] = v
		}
		return out
	}
}

// flat builds a model that returns one value repeated.
// flat is a level model: it estimates where price *belongs*, not where it is.
//
// Plotting that level as the next bar teleported the forecast away from the
// market by up to a percent on the first step. The path now starts at the last
// observed close and converges toward the level over the horizon, which is both
// continuous with the chart and what a mean-reversion claim actually means.
// naive picks the last close itself, so its level equals its anchor and it stays
// the flat line it should be.
func flat(pick func([]float64) float64) func([]float64, int) []float64 {
	return func(closes []float64, horizon int) []float64 {
		out := make([]float64, horizon)
		if len(closes) == 0 {
			return out
		}
		anchor := closes[len(closes)-1]
		level := pick(closes)
		half := math.Max(1, float64(horizon)/2)
		for i := range out {
			w := 1 - math.Pow(0.5, float64(i+1)/half)
			out[i] = anchor + (level-anchor)*w
		}
		return out
	}
}

func lastValue(c []float64) float64 {
	if len(c) == 0 {
		return 0
	}
	return c[len(c)-1]
}

func meanValue(c []float64) float64 {
	if len(c) == 0 {
		return 0
	}
	s := 0.0
	for _, v := range c {
		s += v
	}
	return s / float64(len(c))
}

func smaValue(n int) func([]float64) float64 {
	return func(c []float64) float64 {
		if len(c) == 0 {
			return 0
		}
		if n > len(c) {
			n = len(c)
		}
		s := 0.0
		for _, v := range c[len(c)-n:] {
			s += v
		}
		return s / float64(n)
	}
}

func emaValue(alpha float64) func([]float64) float64 {
	return func(c []float64) float64 {
		if len(c) == 0 {
			return 0
		}
		e := c[0]
		for _, v := range c[1:] {
			e = alpha*v + (1-alpha)*e
		}
		return e
	}
}

// trendLine fits a least-squares line and extrapolates it, optionally over only
// the most recent `lookback` bars and with a per-step damping factor.
func trendLine(lookback int, damp float64) func([]float64, int) []float64 {
	return func(closes []float64, horizon int) []float64 {
		n := len(closes)
		if n == 0 {
			return make([]float64, horizon)
		}
		start := 0
		if lookback > 0 && lookback < n {
			start = n - lookback
		}
		xs := closes[start:]
		m := float64(len(xs))
		if m < 2 {
			v := xs[len(xs)-1]
			out := make([]float64, horizon)
			for i := range out {
				out[i] = v
			}
			return out
		}
		var sumX, sumY, sumXY, sumXX float64
		for i, y := range xs {
			x := float64(i)
			sumX += x
			sumY += y
			sumXY += x * y
			sumXX += x * x
		}
		den := m*sumXX - sumX*sumX
		slope := 0.0
		if den != 0 {
			slope = (m*sumXY - sumX*sumY) / den
		}
		// Anchor on the price that actually printed, not on the regression's
		// fitted value at that bar. A line fitted to a window almost never passes
		// through the last close, so the old code opened the forecast a fraction
		// of a percent away from the market and then drifted from there.
		anchor := closes[n-1]

		out := make([]float64, horizon)
		step := 0.0
		scale := 1.0
		for i := 0; i < horizon; i++ {
			scale *= damp
			step += slope * scale
			out[i] = anchor + step
		}
		return out
	}
}

// Window is one walk-forward cut: the models' forecasts and the real closes.
type Window struct {
	TrainEnd  int                  `json:"train_end"`
	TrainFrom int64                `json:"train_from"`
	TrainTo   int64                `json:"train_to"`
	Times     []int64              `json:"times"`
	LastTrain float64              `json:"last_train"`
	Real      []float64            `json:"real"`
	Forecasts map[string][]float64 `json:"forecasts"`
}

// Score is a model's out-of-sample record across every window.
type Score struct {
	Model     string  `json:"model"`
	Note      string  `json:"note"`
	Windows   int     `json:"windows"`
	MAE       float64 `json:"mae"`
	RMSE      float64 `json:"rmse"`
	MAPE      float64 `json:"mape"`
	Bias      float64 `json:"bias"`
	Direction float64 `json:"direction"`
	MaxError  float64 `json:"max_error"`
	// Conformal bands. HalfWidth is calibrated on one half of the windows and
	// Coverage is measured on the other half, so the number is a real
	// out-of-sample hit rate and not a restatement of the quantile.
	Band80     float64 `json:"band80"`
	Coverage80 float64 `json:"coverage80"`
	Band95     float64 `json:"band95"`
	Coverage95 float64 `json:"coverage95"`
	// Adaptive conformal band (ACI): the miscoverage target is updated online,
	// so coverage keeps tracking the nominal level even when the error
	// distribution drifts. This is the band to trust when the static one
	// under-covers.
	Band80ACI     float64 `json:"band80_aci"`
	Coverage80ACI float64 `json:"coverage80_aci"`
}

// Study is the full result of a walk-forward run.
type Study struct {
	Symbol   string   `json:"symbol"`
	Interval string   `json:"interval"`
	Horizon  int      `json:"horizon"`
	Points   int      `json:"points"`
	Best     string   `json:"best"`
	Windows  []Window `json:"windows"`
	Scores   []Score  `json:"scores"`
}

// Evaluate runs a walk-forward study over series.
//
// horizon is how many bars are forecast and graded per cut; windows is how many
// cuts to grade (spread evenly over the tail of the series); minTrain is the
// minimum history a cut needs before it is worth grading.
func Evaluate(symbol, interval string, series []Point, horizon, windows, minTrain int) Study {
	return EvaluateWindow(symbol, interval, series, horizon, windows, minTrain, 0)
}

// EvaluateWindow is Evaluate with an explicit training window: each cut trains
// on at most trainBars bars immediately before it (0 = use everything).
//
// Training span and forecast span are different questions. A model trained on
// three months and asked for one week is a different experiment from one
// trained on three days, and the walk-forward must respect that choice.
func EvaluateWindow(symbol, interval string, series []Point, horizon, windows, minTrain, trainBars int) Study {
	st := Study{Symbol: symbol, Interval: interval, Horizon: horizon, Points: len(series)}
	if horizon < 1 {
		horizon = 1
	}
	if windows < 1 {
		windows = 1
	}
	if minTrain < 10 {
		minTrain = 10
	}
	closes := make([]float64, len(series))
	for i, p := range series {
		closes[i] = p.V
	}

	// Candidate cut points: the last `windows` positions that still leave a
	// full horizon of real data to grade against, spaced evenly.
	latest := len(closes) - horizon
	if latest <= minTrain {
		return st // not enough history to grade anything honestly
	}
	stride := horizon
	if stride < 1 {
		stride = 1
	}
	// Spread the cuts back from the latest one so they do not overlap.
	need := windows*stride + minTrain
	if need > latest {
		stride = (latest - minTrain) / windows
		if stride < 1 {
			stride = 1
		}
	}
	var cuts []int
	for c := latest; c > minTrain && len(cuts) < windows; c -= stride {
		cuts = append(cuts, c)
	}
	sort.Ints(cuts)

	models := Models()
	sums := map[string]*Score{}
	// residuals[model] = every out-of-sample error, in window order, so the
	// conformal split stays chronological.
	residuals := map[string][]float64{}
	for _, m := range models {
		sums[m.Name] = &Score{Model: m.Name, Note: m.Note}
	}

	for _, cut := range cuts {
		w := Window{
			TrainEnd:  cut,
			TrainFrom: series[0].T,
			TrainTo:   series[cut-1].T,
			LastTrain: closes[cut-1],
			Forecasts: map[string][]float64{},
		}
		for i := 0; i < horizon; i++ {
			w.Real = append(w.Real, closes[cut+i])
			w.Times = append(w.Times, series[cut+i].T)
		}
		start := 0
		if trainBars > 0 && cut-trainBars > 0 {
			start = cut - trainBars
		}
		train := closes[start:cut]
		for _, m := range models {
			fc := m.Fn(train, horizon)
			padded := make([]float64, horizon)
			copy(padded, fc)
			w.Forecasts[m.Name] = padded

			s := sums[m.Name]
			s.Windows++
			for i := 0; i < horizon && i < len(padded); i++ {
				e := padded[i] - w.Real[i]
				ae := math.Abs(e)
				s.MAE += ae
				s.RMSE += e * e
				if w.Real[i] != 0 {
					s.MAPE += ae / math.Abs(w.Real[i])
				}
				s.Bias += e
				if ae > s.MaxError {
					s.MaxError = ae
				}
				residuals[m.Name] = append(residuals[m.Name], ae)
				prev := w.LastTrain
				if i > 0 {
					prev = w.Real[i-1]
				}
				if (padded[i]-prev)*(w.Real[i]-prev) > 0 {
					s.Direction++
				}
			}
		}
		st.Windows = append(st.Windows, w)
	}

	// Normalise the accumulators into per-point averages.
	total := len(st.Windows) * horizon
	for _, s := range sums {
		if total == 0 {
			continue
		}
		n := float64(total)
		s.MAE /= n
		s.RMSE = math.Sqrt(s.RMSE / n)
		s.MAPE = 100 * s.MAPE / n
		s.Bias /= n
		if s.Windows > 0 {
			s.Direction = 100 * s.Direction / float64(s.Windows*horizon)
		}
		st.Scores = append(st.Scores, *s)
	}
	// Conformal bands: calibrate on the earlier half of the out-of-sample
	// errors, then measure coverage on the later half. Chronological split, so
	// the reported coverage is genuinely forward-looking.
	for i := range st.Scores {
		sc := &st.Scores[i]
		res := residuals[sc.Model]
		half := len(res) / 2
		if half < 5 {
			continue
		}
		calib, test := res[:half], res[half:]
		sc.Band80, sc.Coverage80 = conformal(calib, test, 0.20)
		sc.Band95, sc.Coverage95 = conformal(calib, test, 0.05)
		sc.Band80ACI, sc.Coverage80ACI = aci(res, 0.20, 0.02)
	}

	sort.Slice(st.Scores, func(i, j int) bool {
		if st.Scores[i].MAPE != st.Scores[j].MAPE {
			return st.Scores[i].MAPE < st.Scores[j].MAPE
		}
		return st.Scores[i].RMSE < st.Scores[j].RMSE
	})
	if len(st.Scores) > 0 {
		st.Best = st.Scores[0].Model
	}
	return st
}

// conformal returns the interval half-width for a 1-alpha coverage target,
// calibrated on `calib`, and the coverage that width actually achieves on
// `test`. Splitting avoids the self-fulfilling answer you get by measuring a
// quantile against the same data it was taken from.
func conformal(calib, test []float64, alpha float64) (halfWidth, coverage float64) {
	if len(calib) == 0 || len(test) == 0 {
		return 0, 0
	}
	sorted := append([]float64(nil), calib...)
	sort.Float64s(sorted)
	idx := int(math.Ceil(float64(len(sorted))*(1-alpha))) - 1
	if idx < 0 {
		idx = 0
	}
	if idx >= len(sorted) {
		idx = len(sorted) - 1
	}
	halfWidth = sorted[idx]

	hits := 0
	for _, e := range test {
		if e <= halfWidth {
			hits++
		}
	}
	return halfWidth, float64(hits) / float64(len(test))
}

// analogWindow is how many trailing returns define "this looks like that".
const analogWindow = 12

// analog is a retrieval forecaster: embed the most recent window as a vector of
// returns, find the k most similar windows in the past, and forecast the average
// of what actually followed them.
//
// This is the RAG idea applied to market state rather than text. Two constraints
// make it honest: an analogue's outcome must be fully observed before the cut
// (no lookahead), and the analogue must not overlap the query window (otherwise
// the nearest neighbour is the query itself).
func analog(k, w int) func([]float64, int) []float64 {
	return func(closes []float64, horizon int) []float64 {
		n := len(closes)
		flat := func() []float64 {
			out := make([]float64, horizon)
			if n == 0 {
				return out
			}
			for i := range out {
				out[i] = closes[n-1]
			}
			return out
		}
		if n < w+horizon+2 || k < 1 {
			return flat()
		}

		// Query vector: returns over the last w bars, z-scored so a level shift
		// does not dominate the distance.
		query := returnsOf(closes, n-w, w)
		norm := zscore(query)

		type cand struct {
			dist float64
			rets []float64
		}
		var cands []cand
		// Latest usable analogue end: its outcome must be fully in the past.
		lastEnd := n - horizon - 1
		for end := w; end <= lastEnd; end++ {
			start := end - w + 1
			// Skip windows overlapping the query.
			if end >= n-w {
				continue
			}
			v := zscore(returnsOf(closes, start, w))
			d := 0.0
			for i := range norm {
				diff := norm[i] - v[i]
				d += diff * diff
			}
			cands = append(cands, cand{dist: math.Sqrt(d), rets: forwardReturns(closes, end, horizon)})
			if len(cands) > 4000 {
				break
			}
		}
		if len(cands) == 0 {
			return flat()
		}
		sort.Slice(cands, func(i, j int) bool { return cands[i].dist < cands[j].dist })
		if len(cands) > k {
			cands = cands[:k]
		}

		base := closes[n-1]
		// Per-step mean cumulative return across the analogues.
		meanRet := make([]float64, horizon)
		for step := 0; step < horizon; step++ {
			acc := 0.0
			count := 0
			for _, c := range cands {
				if step < len(c.rets) {
					acc += c.rets[step]
					count++
				}
			}
			if count > 0 {
				meanRet[step] = acc / float64(count)
			}
		}

		// The analogues supply the shape; the current price supplies the level.
		// The path must start at the last close and must not carry unearned
		// drift, so relative moves are measured from step 0 and the residual
		// drift is removed progressively, leaving step 0 untouched.
		first := meanRet[0]
		raw := make([]float64, horizon)
		centre := 0.0
		for i, r := range meanRet {
			raw[i] = r - first
			centre += raw[i]
		}
		centre /= float64(horizon)
		last := float64(horizon - 1)
		if last < 1 {
			last = 1
		}

		out := make([]float64, horizon)
		for i := range out {
			out[i] = base * (1 + raw[i] - centre*(float64(i)/last))
		}
		return out
	}
}

// returnsOf collects w simple returns starting at index start.
func returnsOf(closes []float64, start, w int) []float64 {
	out := make([]float64, 0, w)
	for i := start; i < start+w && i < len(closes); i++ {
		if i == 0 || closes[i-1] == 0 {
			out = append(out, 0)
			continue
		}
		out = append(out, closes[i]/closes[i-1]-1)
	}
	for len(out) < w {
		out = append(out, 0)
	}
	return out
}

// forwardReturns is the realised return path of `horizon` steps after end.
func forwardReturns(closes []float64, end, horizon int) []float64 {
	out := make([]float64, 0, horizon)
	base := closes[end]
	if base == 0 {
		return out
	}
	for i := end + 1; i <= end+horizon && i < len(closes); i++ {
		out = append(out, closes[i]/base-1)
	}
	return out
}

// zscore centres and scales a vector so distance measures shape, not level.
func zscore(v []float64) []float64 {
	out := make([]float64, len(v))
	if len(v) == 0 {
		return out
	}
	mean := 0.0
	for _, x := range v {
		mean += x
	}
	mean /= float64(len(v))
	sd := 0.0
	for _, x := range v {
		sd += (x - mean) * (x - mean)
	}
	sd = math.Sqrt(sd / float64(len(v)))
	if sd == 0 {
		sd = 1
	}
	for i, x := range v {
		out[i] = (x - mean) / sd
	}
	return out
}

// aci is Adaptive Conformal Inference: instead of freezing one quantile, it
// walks the out-of-sample errors in order and moves the miscoverage target
// whenever the band missed.
//
//	alpha_{t+1} = alpha_t + gamma * (alpha - err_t)
//
// If the band misses (err_t = 1) the target shrinks, which widens the band; if
// it over-covers, the target grows and the band tightens. The result tracks the
// nominal coverage under drift, which a static split-conformal band cannot do —
// that is exactly the failure this was added to fix.
func aci(residuals []float64, alpha, gamma float64) (halfWidth, coverage float64) {
	if len(residuals) < 20 {
		return 0, 0
	}
	const warmup = 20
	const window = 250
	target := alpha
	predicted, covered := 0, 0
	for i, e := range residuals {
		if i >= warmup {
			lo := i - window
			if lo < 0 {
				lo = 0
			}
			q := quantile(residuals[lo:i], 1-target)
			predicted++
			missed := 0.0
			if e > q {
				missed = 1
			} else {
				covered++
			}
			target += gamma * (alpha - missed)
			if target < 0.001 {
				target = 0.001
			}
			if target > 0.999 {
				target = 0.999
			}
		}
	}
	if predicted == 0 {
		return 0, 0
	}
	lo := len(residuals) - window
	if lo < 0 {
		lo = 0
	}
	return quantile(residuals[lo:], 1-target), float64(covered) / float64(predicted)
}

// quantile returns the q-quantile of v (linear interpolation, v need not be
// sorted; it is copied).
func quantile(v []float64, q float64) float64 {
	if len(v) == 0 {
		return 0
	}
	if q <= 0 {
		q = 0
	}
	if q >= 1 {
		q = 1
	}
	c := append([]float64(nil), v...)
	sort.Float64s(c)
	pos := q * float64(len(c)-1)
	lower := int(math.Floor(pos))
	upper := lower + 1
	if upper >= len(c) {
		return c[len(c)-1]
	}
	frac := pos - float64(lower)
	return c[lower]*(1-frac) + c[upper]*frac
}

// Forecast runs one named model over closes and reports whether the name was
// known. It lets a caller outside this package ask for a specific model — for
// example the winner of an Evaluate run — without re-deriving the grid.
func Forecast(name string, closes []float64, horizon int) ([]float64, bool) {
	if horizon < 1 {
		horizon = 1
	}
	for _, m := range Models() {
		if m.Name == name {
			out := m.Fn(closes, horizon)
			if len(out) != horizon {
				padded := make([]float64, horizon)
				copy(padded, out)
				out = padded
			}
			return out, true
		}
	}
	return nil, false
}

// AgentMethods are the model names the agent's python runner understands.
// Anything else must be computed here.
var AgentMethods = map[string]bool{"ema": true, "sma": true, "trend": true}

// arimaForecast fits ARIMA on the training window and projects it forward.
// A fit failure degrades to holding the last close rather than returning a
// short or empty path.
func arimaForecast() func([]float64, int) []float64 {
	return func(closes []float64, horizon int) []float64 {
		m, ok := arima.Select(closes, 2, 2)
		if !ok {
			return flat(lastValue)(closes, horizon)
		}
		out := m.Forecast(closes, horizon)
		if len(out) != horizon {
			return flat(lastValue)(closes, horizon)
		}
		// An ARIMA can diverge when the recursion is unstable or the series is
		// degenerate. A forecast outside half to double the last price is not a
		// forecast, it is a numerical failure; hold the price instead.
		last := closes[len(closes)-1]
		for _, v := range out {
			if math.IsNaN(v) || math.IsInf(v, 0) || v <= 0 || v < last*0.5 || v > last*2 {
				return flat(lastValue)(closes, horizon)
			}
		}
		return out
	}
}
