package api

import (
	"math"
	"sort"

	"pumpdump/backend/internal/binance"
)

// The composite model.
//
// A blend is only worth its complexity if it beats its own components. This one
// evaluates every family available — volume-weighted levels, volume-weighted
// trends and drifts, the formula models from the lab, and ARIMA — weights them by
// measured skill on a validation tail, and then checks the result against the
// best single component. If the blend cannot beat that component out of sample,
// the component wins. The guard is the point: it makes the ensemble unable to do
// worse than what it is made of.

// superComponent is one member of the blend.
type superComponent struct {
	name     string
	make     func([]binance.Kline, int) ([]float64, bool)
	lookback int
	bars     int
}

// superComponents is the candidate pool.
func superComponents() []superComponent {
	// Every component routes through the registry, so a model cannot mean one
	// thing here and another in the backtest.
	viaRegistry := func(method string, lookback int) func([]binance.Kline, int) ([]float64, bool) {
		return func(bars []binance.Kline, horizon int) ([]float64, bool) {
			return modelPath(method, bars, horizon, lookback)
		}
	}
	vol := func(kind string, lookback int) func([]binance.Kline, int) ([]float64, bool) {
		return func(bars []binance.Kline, horizon int) ([]float64, bool) {
			switch kind {
			case "vwap":
				return vwapForecast(bars, horizon, lookback)
			case "vwtrend":
				return vwTrend(bars, horizon, lookback)
			case "vwdrift":
				return vwapDrift(bars, horizon, lookback)
			}
			return nil, false
		}
	}
	return []superComponent{
		// Flat is a component. On a symbol that barely moves, holding the price
		// beats every model, and the composite must be able to say so rather than
		// being structurally unable to lose to it.
		{name: "naive", make: func(bars []binance.Kline, horizon int) ([]float64, bool) {
			if len(bars) == 0 {
				return nil, false
			}
			last := bars[len(bars)-1].Close
			out := make([]float64, horizon)
			for i := range out {
				out[i] = last
			}
			return out, true
		}},
		{name: "vwap2", make: vol("vwap", 2), lookback: 2},
		{name: "vwap6", make: vol("vwap", 6), lookback: 6},
		{name: "vwap12", make: vol("vwap", 12), lookback: 12},
		{name: "vwap24", make: vol("vwap", 24), lookback: 24},
		{name: "vwt24", make: vol("vwtrend", 24), lookback: 24},
		{name: "vwt50", make: vol("vwtrend", 50), lookback: 50},
		{name: "vwd12", make: vol("vwdrift", 12), lookback: 12},
		{name: "vwd24", make: vol("vwdrift", 24), lookback: 24},
		{name: "analog10", make: viaRegistry("analog10", 0)},
		{name: "analog20", make: viaRegistry("analog20", 0)},
		{name: "damped90", make: viaRegistry("damped90", 0)},
		{name: "trend50", make: viaRegistry("trend50", 0)},
		{name: "sma3", make: viaRegistry("sma3", 0)},
		{name: "ema30", make: viaRegistry("ema30", 0)},
		{name: "arima", make: viaRegistry("arima", 0)},
		{name: "mlr", make: viaRegistry("mlr", 0)},
	}
}

// validated returns each component's mean absolute percentage error on the last
// `checks` non-overlapping anchors, using only data available at each anchor.
func validated(bars []binance.Kline, horizon, checks int) map[string]float64 {
	out := map[string]float64{}
	if checks < 3 {
		checks = 3
	}
	first := len(bars) - checks*horizon
	if first < 80 {
		return out
	}
	for _, c := range superComponents() {
		var sum float64
		var n int
		for k := 0; k < checks; k++ {
			cut := first + k*horizon
			if cut+horizon >= len(bars) {
				break
			}
			path, ok := c.make(bars[:cut], horizon)
			if !ok {
				continue
			}
			for i := 0; i < horizon; i++ {
				actual := bars[cut+i].Close
				if actual <= 0 {
					continue
				}
				sum += math.Abs(path[i]-actual) / actual
				n++
			}
		}
		if n > 0 {
			out[c.name] = 100 * sum / float64(n)
		}
	}
	return out
}

// superForecast blends the components by inverse squared error, then keeps the
// blend only if it beats the best component on the same validation window.
func superForecast(bars []binance.Kline, horizon int) ([]float64, bool) {
	if len(bars) < 120+horizon {
		return nil, false
	}
	errs := validated(bars, horizon, 8)
	if len(errs) == 0 {
		return nil, false
	}

	type ranked struct {
		name  string
		err   float64
		value float64
	}
	list := make([]ranked, 0, len(errs))
	for name, e := range errs {
		list = append(list, ranked{name: name, err: e})
	}
	sort.Slice(list, func(i, j int) bool { return list[i].err < list[j].err })

	// Use the best six: beyond that the extra members stop adding independent
	// information and start dragging the blend toward the average.
	if len(list) > 6 {
		list = list[:6]
	}

	blend := make([]float64, horizon)
	var totalW float64
	for _, r := range list {
		var comp superComponent
		found := false
		for _, c := range superComponents() {
			if c.name == r.name {
				comp = c
				found = true
				break
			}
		}
		if !found {
			continue
		}
		path, ok := comp.make(bars, horizon)
		if !ok {
			continue
		}
		w := 1 / math.Max(r.err*r.err, 1e-6)
		for i := 0; i < horizon; i++ {
			blend[i] += path[i] * w
		}
		totalW += w
	}
	if totalW <= 0 {
		return nil, false
	}
	for i := range blend {
		blend[i] /= totalW
	}

	// Grade the blend the same way the components were graded.
	checks := 5
	first := len(bars) - checks*horizon
	if first < 80 {
		return blend, true
	}
	var sum float64
	var n int
	for k := 0; k < checks; k++ {
		cut := first + k*horizon
		if cut+horizon >= len(bars) {
			break
		}
		path, ok := superForecastAt(bars[:cut], horizon)
		if !ok {
			continue
		}
		for i := 0; i < horizon; i++ {
			actual := bars[cut+i].Close
			if actual <= 0 {
				continue
			}
			sum += math.Abs(path[i]-actual) / actual
			n++
		}
	}
	blendErr := math.MaxFloat64
	if n > 0 {
		blendErr = 100 * sum / float64(n)
	}
	// Accept the blend only if it beats the best component by a clear margin.
	// Where the components agree (BTC, SOL) a blend of near-identical forecasts
	// just adds noise, and the guard has to be strict enough to notice that.
	best := list[0]
	if blendErr <= best.err*0.98 {
		return blend, true
	}
	// The guard: the blend lost, so use the component that won.
	for _, c := range superComponents() {
		if c.name == best.name {
			if path, ok := c.make(bars, horizon); ok {
				return path, true
			}
		}
	}
	return blend, true
}

// superForecastAt is the weighting step without the guard, used by the guard
// itself when it grades the blend.
func superForecastAt(bars []binance.Kline, horizon int) ([]float64, bool) {
	errs := validated(bars, horizon, 4)
	if len(errs) == 0 {
		return nil, false
	}
	type ranked struct {
		name string
		err  float64
	}
	list := make([]ranked, 0, len(errs))
	for name, e := range errs {
		list = append(list, ranked{name: name, err: e})
	}
	sort.Slice(list, func(i, j int) bool { return list[i].err < list[j].err })
	if len(list) > 6 {
		list = list[:6]
	}
	blend := make([]float64, horizon)
	var totalW float64
	for _, r := range list {
		for _, c := range superComponents() {
			if c.name != r.name {
				continue
			}
			path, ok := c.make(bars, horizon)
			if !ok {
				continue
			}
			w := 1 / math.Max(r.err*r.err, 1e-6)
			for i := 0; i < horizon; i++ {
				blend[i] += path[i] * w
			}
			totalW += w
		}
	}
	if totalW <= 0 {
		return nil, false
	}
	for i := range blend {
		blend[i] /= totalW
	}
	return blend, true
}
