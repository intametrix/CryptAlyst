package outlook

import (
	"math"

	"pumpdump/backend/internal/binance"
)

// Funnel is a projection cone drawn forward from the current price.
//
// It answers the question a single forecast line cannot: not "where will price
// be" but "how wide is the range of plausible outcomes, and how does that width
// grow with time". A cone that widens honestly is more useful than a line that
// pretends to precision, because the width is itself information -- a narrow
// cone on a quiet symbol and a wide one on a volatile symbol look different at
// a glance, which is the point.
//
// Three rules:
//
//  1. **Width comes from the symbol's own realised volatility**, not a fixed
//     percentage. Ten percent is enormous for one instrument and invisible for
//     another, and a cone that ignores that is decoration.
//  2. **Width grows with the square root of the horizon**, which is what a
//     random walk does. Growing linearly would overstate long-horizon
//     uncertainty and make the far end meaningless.
//  3. **The centre may be tilted, the bands never are.** An event in progress is
//     evidence about direction, so the centre drifts; the uncertainty around it
//     stays symmetric and grows regardless, because an event tells you nothing
//     about how wrong the estimate might be.
type Funnel struct {
	Kind       string  `json:"kind"`
	FromMS     int64   `json:"from_ms"`
	Current    float64 `json:"current"`
	HorizonSec int     `json:"horizon_sec"`
	Bars       int     `json:"bars"`
	BandNote   string  `json:"band_note"`
	SigmaPct   float64 `json:"sigma_pct_per_horizon"`
	DriftPct   float64 `json:"drift_pct_total"`
	Basis      string  `json:"basis"`
	Confidence float64 `json:"confidence"`
	// Levels carries one series per confidence band so the UI can shade them
	// without recomputing anything.
	Levels []FunnelLevel `json:"levels"`
	Points []FunnelPoint `json:"points"`
}

// FunnelLevel describes one band's coverage.
type FunnelLevel struct {
	Name     string  `json:"name"`
	Coverage float64 `json:"coverage"`
	Z        float64 `json:"z"`
}

// FunnelPoint is one bar forward.
type FunnelPoint struct {
	T      int64   `json:"t"`
	Centre float64 `json:"centre"`
	// Upper/lower for the widest band, plus each narrower band, so a caller can
	// draw nested cones without recomputation.
	Bands map[string][2]float64 `json:"bands"`
}

// funnelLevels are the nested bands. Coverage is two-sided, so z is the
// two-sided quantile: 50% -> 0.674, 80% -> 1.282, 95% -> 1.960.
var funnelLevels = []FunnelLevel{
	{Name: "50", Coverage: 0.50, Z: 0.674},
	{Name: "80", Coverage: 0.80, Z: 1.282},
	{Name: "95", Coverage: 0.95, Z: 1.960},
}

// FunnelParams controls the projection.
type FunnelParams struct {
	Bars        int
	MinSigmaPct float64
	MaxSigmaPct float64
}

// DefaultFunnelParams returns starting values.
func DefaultFunnelParams() FunnelParams {
	return FunnelParams{Bars: 40, MinSigmaPct: 0.02, MaxSigmaPct: 15}
}

// ComputeFunnel projects the cone.
//
// eventDriftPct is the directional evidence from an event in progress; pass 0
// when nothing is happening, in which case the cone is centred on the current
// price and the projection is symmetric.
func ComputeFunnel(
	bars []binance.Kline,
	intervalMS int64,
	now int64,
	eventDriftPct float64,
	confidence float64,
	p FunnelParams,
) *Funnel {
	if len(bars) < 20 || intervalMS <= 0 {
		return nil
	}
	last := bars[len(bars)-1]
	current := last.Close
	if current <= 0 {
		return nil
	}

	// Realised per-bar volatility from the last 60 returns, in percent.
	window := 60
	if len(bars) < window+1 {
		window = len(bars) - 1
	}
	returns := make([]float64, 0, window)
	for i := len(bars) - window; i < len(bars); i++ {
		prev := bars[i-1].Close
		if prev <= 0 {
			continue
		}
		returns = append(returns, (bars[i].Close-prev)/prev*100)
	}
	sigma := stdev(returns)
	// Floor and cap: a zero-volatility sample would draw a cone with no width,
	// which reads as certainty the model does not have.
	if sigma < p.MinSigmaPct {
		sigma = p.MinSigmaPct
	}
	if sigma > p.MaxSigmaPct {
		sigma = p.MaxSigmaPct
	}

	points := make([]FunnelPoint, 0, p.Bars)
	for i := 1; i <= p.Bars; i++ {
		// Square-root-of-time widening, the random-walk result.
		width := sigma * math.Sqrt(float64(i))
		drift := eventDriftPct * (float64(i) / float64(p.Bars))
		centre := current * (1 + drift/100)
		bands := make(map[string][2]float64, len(funnelLevels))
		for _, level := range funnelLevels {
			spread := centre * width * level.Z / 100
			bands[level.Name] = [2]float64{round6(centre - spread), round6(centre + spread)}
		}
		points = append(points, FunnelPoint{
			T:      last.OpenTime + intervalMS*int64(i),
			Centre: round6(centre),
			Bands:  bands,
		})
	}

	basis := "width from this symbol's realised volatility over the last " +
		itoa(len(returns)) + " bars"
	if eventDriftPct == 0 {
		basis += "; no event in progress, so the cone is symmetric"
	} else {
		basis += "; centre tilted by the event in progress, bands remain symmetric"
	}

	return &Funnel{
		Kind:       "funnel",
		FromMS:     last.OpenTime,
		Current:    round6(current),
		HorizonSec: int(int64(p.Bars) * intervalMS / 1000),
		Bars:       p.Bars,
		BandNote:   "nested bands at 50%, 80% and 95% two-sided coverage; width grows with the square root of the horizon",
		SigmaPct:   round6(sigma),
		DriftPct:   round6(eventDriftPct),
		Basis:      basis,
		Confidence: confidence,
		Levels:     funnelLevels,
		Points:     points,
	}
}

func stdev(values []float64) float64 {
	if len(values) < 2 {
		return 0
	}
	mean := 0.0
	for _, v := range values {
		mean += v
	}
	mean /= float64(len(values))
	variance := 0.0
	for _, v := range values {
		d := v - mean
		variance += d * d
	}
	variance /= float64(len(values) - 1)
	return math.Sqrt(variance)
}

func round6(v float64) float64 {
	if math.IsNaN(v) || math.IsInf(v, 0) {
		return 0
	}
	scale := math.Pow(10, math.Max(0, 6-math.Floor(math.Log10(math.Abs(v)+1e-12))))
	return math.Round(v*scale) / scale
}
