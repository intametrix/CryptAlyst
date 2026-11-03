package api

import (
	"strings"
	"sync"

	"pumpdump/backend/internal/binance"
	"pumpdump/backend/internal/forecastlab"
)

// The model registry.
//
// A model used to be described in four places at once — the backtest switch, the
// verify switch, the composite's component list, and the GPU runner's own Python
// table — and they drifted: vwapDrift refused lookbacks under five in the Go
// implementation while the Python one happily computed them, so a saved
// configuration could be valid on one path and impossible on the other.
//
// One table now defines what each model is, what it needs, and where it can run.
// Every caller reads it.

// modelSpec describes one forecast model.
type modelSpec struct {
	Name string
	// Family groups models for reporting ("volume", "moving average", "regression").
	Family string
	// Volume reports whether the model needs volume as well as price.
	Volume bool
	// AgentOnly marks a model the platform cannot compute; it must go to an agent.
	AgentOnly bool
	// Path produces the forecast. Lookback is ignored by models without one.
	Path func(bars []binance.Kline, horizon, lookback int) ([]float64, bool)
}

// closesOf extracts closes once, for the models that only read price.
func closesOf(bars []binance.Kline) []float64 {
	out := make([]float64, len(bars))
	for i, b := range bars {
		out[i] = b.Close
	}
	return out
}

// labModel adapts a forecastlab model to the registry.
func labModel(name string) func([]binance.Kline, int, int) ([]float64, bool) {
	return func(bars []binance.Kline, horizon, _ int) ([]float64, bool) {
		pts, ok := forecastlab.Forecast(name, closesOf(bars), horizon)
		return pts, ok && len(pts) == horizon
	}
}

// Default lookbacks, kept here so no caller invents its own.
const (
	defaultVWAPLookback    = 24
	defaultVWTrendLookback = 50
	defaultVWDriftLookback = 24
)

// registry is built lazily: the composite model refers back to the registry to
// compute its components, so building it at package initialisation would be a
// cycle.
var (
	registryOnce sync.Once
	registry     map[string]modelSpec
)

func getRegistry() map[string]modelSpec {
	registryOnce.Do(func() { registry = buildRegistry() })
	return registry
}

// buildRegistry is the single definition of every platform-side model.
func buildRegistry() map[string]modelSpec {
	reg := map[string]modelSpec{}
	for _, name := range forecastlab.Names() {
		n := name
		reg[n] = modelSpec{Name: n, Family: forecastlab.Family(n), Path: labModel(n)}
	}
	reg["vwap"] = modelSpec{Name: "vwap", Family: "volume", Volume: true,
		Path: func(b []binance.Kline, h, lb int) ([]float64, bool) {
			if lb <= 0 {
				lb = defaultVWAPLookback
			}
			return vwapForecast(b, h, lb)
		}}
	reg["vwtrend"] = modelSpec{Name: "vwtrend", Family: "volume", Volume: true,
		Path: func(b []binance.Kline, h, lb int) ([]float64, bool) {
			if lb <= 0 {
				lb = defaultVWTrendLookback
			}
			return vwTrend(b, h, lb)
		}}
	reg["vwdrift"] = modelSpec{Name: "vwdrift", Family: "volume", Volume: true,
		Path: func(b []binance.Kline, h, lb int) ([]float64, bool) {
			if lb <= 0 {
				lb = defaultVWDriftLookback
			}
			return vwapDrift(b, h, lb)
		}}
	reg["hybrid"] = modelSpec{Name: "hybrid", Family: "volume", Volume: true,
		Path: func(b []binance.Kline, h, _ int) ([]float64, bool) { return hybridForecast(b, h) }}
	reg["super"] = modelSpec{Name: "super", Family: "composite", Volume: true,
		Path: func(b []binance.Kline, h, _ int) ([]float64, bool) { return superForecast(b, h) }}
	reg["mlr"] = modelSpec{Name: "mlr", Family: "regression", Volume: true,
		Path: func(b []binance.Kline, h, _ int) ([]float64, bool) { return mlrForecast(b, h) }}
	reg["timestral"] = modelSpec{Name: "timestral", Family: "foundation model", Volume: true, AgentOnly: true}
	return reg
}

// modelPath is the one way to compute a forecast from bars.
func modelPath(method string, bars []binance.Kline, horizon, lookback int) ([]float64, bool) {
	m := strings.ToLower(strings.TrimSpace(method))
	spec, ok := getRegistry()[m]
	if !ok || spec.Path == nil {
		return nil, false
	}
	pts, ok := spec.Path(bars, horizon, lookback)
	if !ok || len(pts) != horizon {
		return nil, false
	}
	return pts, true
}

// platformComputable reports whether the platform can compute a model locally.
func platformComputable(method string) bool {
	spec, ok := getRegistry()[strings.ToLower(strings.TrimSpace(method))]
	return ok && !spec.AgentOnly && spec.Path != nil
}

// modelFamily names the group a model belongs to, for reporting.
func modelFamily(method string) string {
	if spec, ok := getRegistry()[strings.ToLower(strings.TrimSpace(method))]; ok {
		return spec.Family
	}
	return "unknown"
}
