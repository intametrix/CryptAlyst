// Package outlook answers "what is happening right now, and what next?" for a
// single symbol: whether an event is in progress, how long it may run before
// reverting, and a short-horizon distribution over the next few seconds.
//
// This package contains the most dangerous output in the system, because a
// probability shown next to a live chart will be read as a prediction. Three
// rules therefore apply without exception:
//
//  1. **Every probability states its basis.** If no fitted model is loaded, the
//     basis says "uncalibrated heuristic" and names the sample shortfall. There
//     is no code path that emits a bare percentage.
//  2. **Nothing is presented as calibrated until it is.** A calibrated model is
//     loaded from an external file produced by the offline learner; absent that
//     file, the numbers are explicitly labelled as a prior.
//  3. **One source for the reversal ETA.** The ETA comes from the forecast
//     package's measured half-life, not from a second competing estimate, so
//     the chart and the ETA can never disagree.
package outlook

import (
	"encoding/json"
	"math"
	"os"
	"strconv"
	"sync"

	"pumpdump/backend/internal/binance"
	"pumpdump/backend/internal/flow"
	"pumpdump/backend/internal/forecast"
	"pumpdump/backend/internal/levels"
)

// State is the event lifecycle stage.
type State string

const (
	StateIdle       State = "idle"
	StateOnset      State = "onset"
	StateActive     State = "active"
	StateExhausting State = "exhausting"
)

// Params controls the thresholds.
type Params struct {
	// BurstOnset is the flow multiple at which an event is considered starting.
	BurstOnset float64
	// BurstActive is the multiple for a fully underway event.
	BurstActive float64
	// ExhaustBurstRatio: flow falling below this fraction of its peak while the
	// move is already extended marks the move as exhausting rather than fresh.
	ExhaustBurstRatio float64
	// ExtendedPct is the 60s move considered already extended.
	ExtendedPct float64
	// ShortHorizonSec is the forecast overlay window.
	ShortHorizonSec int
	// MinResolvedForCalibration mirrors the learner's gate. Until the ledger can
	// support a fitted model, probabilities stay explicitly uncalibrated.
	MinResolvedForCalibration int
}

// DefaultParams returns the starting values.
func DefaultParams() Params {
	return Params{
		BurstOnset:                3.0,
		BurstActive:               8.0,
		ExhaustBurstRatio:         0.45,
		ExtendedPct:               1.2,
		ShortHorizonSec:           10,
		MinResolvedForCalibration: 30,
	}
}

// Event describes whether an event is underway.
type Event struct {
	InProgress bool    `json:"in_progress"`
	State      State   `json:"state"`
	Direction  int     `json:"direction"` // +1 up, -1 down, 0 none
	Confidence float64 `json:"confidence"`
	Basis      string  `json:"confidence_basis"`
	ElapsedSec float64 `json:"elapsed_sec"`
	ETASeconds float64 `json:"eta_reversal_sec"`
	ETABasis   string  `json:"eta_basis"`
	ETALow     float64 `json:"eta_low_sec"`
	ETAHigh    float64 `json:"eta_high_sec"`
}

// ShortHorizon is the next-few-seconds distribution.
type ShortHorizon struct {
	HorizonSec      int     `json:"horizon_sec"`
	PUp             float64 `json:"p_up"`
	PDown           float64 `json:"p_down"`
	ExpectedMovePct float64 `json:"expected_move_pct"`
	BandPct         float64 `json:"band_pct"`
	Basis           string  `json:"basis"`
	Edge            string  `json:"edge"`
}

// Outlook is the full answer for one symbol.
type Outlook struct {
	Type        string         `json:"type"`
	Symbol      string         `json:"symbol"`
	TSMS        int64          `json:"ts_ms"`
	Event       Event          `json:"event"`
	Short       ShortHorizon   `json:"short_horizon"`
	Levels      []levels.Level `json:"levels"`
	Supports    []levels.Level `json:"supports"`
	Resistances []levels.Level `json:"resistances"`
}

// calibration is the fitted model contract, written by the offline learner.
// The loader is tolerant on purpose: an absent or partial file must degrade to
// an explicitly uncalibrated heuristic, never to a fabricated probability.
type calibration struct {
	Version  string   `json:"version"`
	Features []string `json:"features"`
	// The offline learner calls these weights; an earlier draft of this loader
	// expected `coefficients`. Accept both rather than silently ignoring a
	// model over a field name -- a rejected model looks exactly like no model,
	// which is the failure this whole layer exists to prevent.
	Weights      []float64 `json:"weights"`
	Coefficients []float64 `json:"coefficients"`
	// Features are standardised before the weights are applied, so the live
	// vector must be normalised with the same mean and standard deviation the
	// fit used. Without this the coefficients are applied to raw magnitudes and
	// the probability is meaningless.
	Mean      []float64 `json:"mean"`
	Std       []float64 `json:"std"`
	Intercept float64   `json:"intercept"`
	// n in the learner's contract; sample_size in the older draft.
	N          int     `json:"n"`
	SampleSize int     `json:"sample_size"`
	Brier      float64 `json:"brier"`
	LogLoss    float64 `json:"log_loss"`
	BaseRate   float64 `json:"base_rate"`
	LabelRule  string  `json:"label_rule"`
	HorizonSec int     `json:"horizon_sec"`
	Basis      string  `json:"basis"`
}

// weights returns whichever coefficient field the file populated.
func (c *calibration) weights() []float64 {
	if len(c.Weights) > 0 {
		return c.Weights
	}
	return c.Coefficients
}

// samples returns whichever sample-size field the file populated.
func (c *calibration) samples() int {
	if c.N > 0 {
		return c.N
	}
	return c.SampleSize
}

// Service computes outlooks and caches the loaded calibration.
type Service struct {
	params Params

	mu     sync.RWMutex
	model  *calibration
	loaded string
}

// New creates a service and attempts to load a fitted model.
func New(params Params, calibrationPath string) *Service {
	s := &Service{params: params}
	s.LoadCalibration(calibrationPath)
	return s
}

// LoadCalibration reads the fitted model, if present and usable.
func (s *Service) LoadCalibration(path string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.model = nil
	s.loaded = ""
	if path == "" {
		return
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return
	}
	var model calibration
	if err := json.Unmarshal(raw, &model); err != nil {
		return
	}
	// Reject a model that cannot actually be evaluated.
	weights := model.weights()
	if len(model.Features) == 0 || len(weights) != len(model.Features) {
		return
	}
	// Refuse a model the learner itself would have refused to publish.
	if model.samples() < s.params.MinResolvedForCalibration {
		return
	}
	s.model = &model
	s.loaded = path
}

// ModelStatus reports what is loaded, for the status line and the UI.
func (s *Service) ModelStatus() map[string]any {
	s.mu.RLock()
	defer s.mu.RUnlock()
	if s.model == nil {
		return map[string]any{
			"calibrated": false,
			"basis": "uncalibrated heuristic (no fitted model loaded; ledger must reach " +
				itoa(s.params.MinResolvedForCalibration) + " resolved predictions)",
		}
	}
	basis := s.model.Basis
	if basis == "" {
		basis = "calibrated on " + itoa(s.model.samples()) + " resolved predictions, brier " +
			strconv.FormatFloat(s.model.Brier, 'f', 3, 64)
	}
	return map[string]any{
		"calibrated":  true,
		"source":      s.loaded,
		"sample_size": s.model.samples(),
		"brier":       s.model.Brier,
		"log_loss":    s.model.LogLoss,
		"base_rate":   s.model.BaseRate,
		"label_rule":  s.model.LabelRule,
		"basis":       basis,
	}
}

// Compute builds the outlook for one symbol.
func (s *Service) Compute(
	symbol string,
	now int64,
	row flow.Candidate,
	bars []binance.Kline,
	intervalMS int64,
	supports, resistances []levels.Level,
	fc *forecast.Forecast,
) *Outlook {
	p := s.params

	burst := row.BurstMultiple
	move10 := absOf(row.Change10s)
	move60 := absOf(row.Change60s)
	direction := 0
	if row.Change10s != nil && *row.Change10s > 0 {
		direction = 1
	} else if row.Change10s != nil && *row.Change10s < 0 {
		direction = -1
	}

	// --- event lifecycle --------------------------------------------------
	state := StateIdle
	switch {
	case burst < p.BurstOnset:
		state = StateIdle
	case move60 >= p.ExtendedPct && burst < p.BurstActive*p.ExhaustBurstRatio*4:
		// Extended already, and flow no longer expanding: the move is mature.
		state = StateExhausting
	case burst >= p.BurstActive:
		state = StateActive
	default:
		state = StateOnset
	}

	// Confidence is a statement about the EVIDENCE, not a probability. It is
	// deliberately capped: this is a transparent heuristic and has no business
	// claiming near-certainty.
	confidence := 0.0
	if state != StateIdle {
		magnitude := math.Min(1.0, burst/math.Max(p.BurstActive*2, 1))
		coverage := math.Min(1.0, row.Coverage)
		confirmation := math.Min(1.0, move10/0.5)
		confidence = round(0.45*magnitude + 0.30*coverage + 0.25*confirmation)
		if confidence > 0.85 {
			confidence = 0.85
		}
	}
	status := s.ModelStatus()
	calibrated, _ := status["calibrated"].(bool)
	confBasis := "uncalibrated heuristic over burst multiple, tick coverage and move size; " +
		"not a probability"
	if calibrated {
		confBasis = "heuristic evidence score; directional probability is calibrated separately"
	}

	// --- ETA to reversal, from the ONE principled source ------------------
	event := Event{
		InProgress: state == StateOnset || state == StateActive || state == StateExhausting,
		State:      state,
		Direction:  direction,
		Confidence: confidence,
		Basis:      confBasis,
	}
	if state != StateIdle && row.UpdatedMS > 0 {
		event.ElapsedSec = round(math.Max(0, float64(now-row.UpdatedMS)/1000))
	}
	if event.InProgress && fc != nil && fc.HalfLifeBars > 0 && intervalMS > 0 {
		// The forecast package already measured how long THIS symbol takes to
		// give back half a move. Deriving the ETA from it keeps the chart line
		// and the ETA numerically consistent instead of competing.
		halfLifeSec := fc.HalfLifeBars * float64(intervalMS) / 1000
		event.ETASeconds = round(halfLifeSec)
		event.ETALow = round(halfLifeSec * 0.5)
		event.ETAHigh = round(halfLifeSec * 2.0)
		event.ETABasis = "from the forecast half-life: " + fc.Basis
	} else if event.InProgress {
		event.ETABasis = "unavailable: no measured half-life for this symbol yet"
	} else {
		event.ETABasis = "no event in progress"
	}

	// --- short-horizon distribution --------------------------------------
	short := s.shortHorizon(p, row, move10, direction, status)

	// --- levels, nearest first -------------------------------------------
	all := make([]levels.Level, 0, len(supports)+len(resistances))
	all = append(all, resistances...)
	all = append(all, supports...)

	return &Outlook{
		Type: "outlook", Symbol: symbol, TSMS: now,
		Event: event, Short: short,
		Levels: all, Supports: supports, Resistances: resistances,
	}
}

// shortHorizon returns the next-few-seconds distribution.
//
// Directional prediction over ten seconds of crypto spot is close to the
// hardest question in this whole system, so the honest default is a near-coin-
// flip with a WIDE band and an explicit "no measurable edge" statement. The
// value of this block is the band and the honesty, not the point estimate.
func (s *Service) shortHorizon(p Params, row flow.Candidate, move10 float64, direction int, status map[string]any) ShortHorizon {
	short := ShortHorizon{
		HorizonSec: p.ShortHorizonSec,
		PUp:        0.5,
		PDown:      0.5,
		Edge:       "no measurable edge at this horizon",
	}

	// A fitted model, when the ledger has earned one, takes precedence.
	if model := s.modelOrNil(); model != nil {
		if pUp, ok := evaluate(model, row); ok {
			short.PUp = round3(pUp)
			short.PDown = round3(1 - pUp)
			basis, _ := status["basis"].(string)
			short.Basis = "calibrated: " + basis
			short.Edge = edgeWord(pUp)
			short.BandPct = round3(shortBand(row))
			short.ExpectedMovePct = round3((2*short.PUp - 1) * short.BandPct)
			return short
		}
	}

	// Uncalibrated fallback: momentum, shrunk hard toward 0.5 because we have
	// no evidence that ten-second momentum persists.
	tilt := 0.0
	if direction != 0 {
		tilt = math.Min(0.06, move10*0.06)
		if direction < 0 {
			tilt = -tilt
		}
	}
	// A burst is mildly informative about continuation, but only mildly.
	if row.BurstMultiple >= p.BurstActive {
		tilt *= 1.2
	}
	pUp := 0.5 + tilt
	if pUp > 0.62 {
		pUp = 0.62
	}
	if pUp < 0.38 {
		pUp = 0.38
	}
	short.PUp = round3(pUp)
	short.PDown = round3(1 - pUp)
	band := shortBand(row)
	short.BandPct = round3(band)
	short.ExpectedMovePct = round3((2*short.PUp - 1) * band)
	basis, _ := status["basis"].(string)
	short.Basis = basis
	short.Edge = "no measurable edge at this horizon"
	return short
}

// shortBand estimates the plausible 10s move from the symbol's own recent
// volatility, so the overlay is scaled to the instrument rather than a fixed
// percentage that would be enormous for one coin and invisible for another.
func shortBand(row flow.Candidate) float64 {
	// Change60s as a proxy for per-minute volatility; a 10s window is roughly
	// sqrt(1/6) of it under a random walk.
	base := absOf(row.Change60s)
	if base <= 0 {
		base = absOf(row.Change10s)
	}
	if base <= 0 {
		return 0.05
	}
	band := base / math.Sqrt(6)
	if band < 0.01 {
		band = 0.01
	}
	if band > 2.0 {
		band = 2.0
	}
	return band
}

func (s *Service) modelOrNil() *calibration {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.model
}

// evaluate applies the fitted logistic model to a live feature vector.
func evaluate(model *calibration, row flow.Candidate) (float64, bool) {
	features := map[string]float64{
		"burst_multiple":  row.BurstMultiple,
		"burst_z":         row.BurstZ,
		"flow_quote":      row.FlowQuote,
		"coverage":        row.Coverage,
		"change_10s":      absOf(row.Change10s),
		"change_60s":      absOf(row.Change60s),
		"taker_buy_ratio": row.TakerBuyRatio,
	}
	weights := model.weights()
	z := model.Intercept
	used := 0
	for i, name := range model.Features {
		value, ok := features[name]
		if !ok {
			return 0, false
		}
		// Standardise with the fitted statistics: the weights were learned
		// against normalised inputs, so applying them to raw values would be
		// applying a different model.
		if i < len(model.Mean) && i < len(model.Std) && model.Std[i] > 0 {
			value = (value - model.Mean[i]) / model.Std[i]
		}
		z += weights[i] * value
		used++
	}
	if used == 0 {
		return 0, false
	}
	return 1 / (1 + math.Exp(-z)), true
}

func edgeWord(pUp float64) string {
	deviation := math.Abs(pUp - 0.5)
	switch {
	case deviation < 0.02:
		return "no measurable edge at this horizon"
	case deviation < 0.05:
		return "weak tilt"
	default:
		return "moderate tilt"
	}
}

func absOf(v *float64) float64 {
	if v == nil {
		return 0
	}
	return math.Abs(*v)
}

func round(v float64) float64 {
	if math.IsNaN(v) || math.IsInf(v, 0) {
		return 0
	}
	return math.Round(v*100) / 100
}

func round3(v float64) float64 {
	if math.IsNaN(v) || math.IsInf(v, 0) {
		return 0.5
	}
	return math.Round(v*1000) / 1000
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var buf [20]byte
	i := len(buf)
	for n > 0 {
		i--
		buf[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		buf[i] = '-'
	}
	return string(buf[i:])
}
