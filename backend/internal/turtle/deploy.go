package turtle

import "math"

// Capital deployment.
//
// The human's instruction: "All capital should be deployed." That is a deliberate
// change of OBJECTIVE, and it is worth stating what it changes, because the two
// modes size positions from opposite directions:
//
//	RISK-BASED (the book)  position size = (risk budget) / (stop distance)
//	                       A volatile symbol gets a SMALL position, because the same
//	                       percentage risk over a wider stop needs fewer units. Cash
//	                       is what is left over, and being partly in cash is normal.
//
//	DEPLOYED (this mode)   position size = (equity x weight) / price
//	                       The notional is the target and the stop distance is
//	                       whatever it is. A volatile symbol gets the same money as
//	                       a calm one and therefore a LARGER percentage risk.
//
// THE CONSEQUENCE, stated plainly rather than buried: in deployed mode a 2N stop on
// a volatile symbol can lose several percent of the account, not the 1% the book
// intends. The stops still protect each position, but the PORTFOLIO risk is no
// longer bounded by the risk budget -- it is bounded by how far the positions can
// fall together, which in crypto is a correlated event. The exposure figure is
// reported on the session so the number is visible rather than inferred.
//
// This is the human's call to make. The mode is a flag, both paths are kept, and
// the arithmetic of each is in one place so the difference is auditable.

// DeploymentMode selects how position size is derived.
type DeploymentMode string

const (
	// DeployRiskBased is the book's sizing: 1% of equity risked per unit.
	DeployRiskBased DeploymentMode = "risk_based"
	// DeployFull keeps the account invested: notional is the target.
	DeployFull DeploymentMode = "full"
)

// TargetExposurePct is how much of equity should be in positions at once when
// fully deployed.
//
// Not 100. A fully invested account with no reserve cannot take a new signal
// without selling something, and it cannot absorb the slippage between a stop being
// touched and being filled. 95% keeps the capital working while leaving a working
// margin -- the difference between "all capital is deployed" and "the account is
// exactly at its limit and any adverse tick forces a sale".
const TargetExposurePct = 95.0

// Exposure reports how much of the account is currently at work.
type Exposure struct {
	Equity        float64 `json:"equity"`
	Cash          float64 `json:"cash"`
	Notional      float64 `json:"notional"`
	ExposurePct   float64 `json:"exposure_pct"`
	TargetPct     float64 `json:"target_pct"`
	OpenPositions int     `json:"open_positions"`
	// HeadroomUSD is what can still be committed before the target is reached.
	HeadroomUSD float64 `json:"headroom_usd"`
	Mode        string  `json:"mode"`
	Note        string  `json:"note"`
}

// Exposure reports the account's current deployment.
func (e *Engine) Exposure() Exposure {
	e.mu.Lock()
	defer e.mu.Unlock()
	eq := e.equityLocked()
	notional := sumNotional(e.positions)
	pct := 0.0
	if eq > 0 {
		pct = notional / eq * 100
	}
	target := TargetExposurePct
	headroom := eq*target/100 - notional
	if headroom < 0 {
		headroom = 0
	}
	return Exposure{
		Equity: round2(eq), Cash: round2(e.cash), Notional: round2(notional),
		ExposurePct: round4(pct), TargetPct: target, OpenPositions: len(e.positions),
		HeadroomUSD: round2(headroom), Mode: string(e.deploy),
		Note: "in full deployment the notional is the target and the stop distance is whatever it is, so a 2N stop on a volatile symbol can cost several percent rather than the 1% the book intends",
	}
}

// SetDeploymentMode switches how positions are sized.
func (e *Engine) SetDeploymentMode(m DeploymentMode) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if m != DeployFull {
		m = DeployRiskBased
	}
	e.deploy = m
}

// DeploymentMode returns the current sizing mode.
func (e *Engine) DeploymentMode() DeploymentMode {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.deploy == "" {
		return DeployRiskBased
	}
	return e.deploy
}

// deployedUnits sizes a position by NOTIONAL rather than by risk.
//
// The weight is the symbol's share of the account. In deployed mode that share is
// interpreted as a share of CAPITAL rather than of risk, which is the whole
// difference between the two modes.
func (e *Engine) deployedUnits(symbol string, price float64) (units, notional float64, ok bool) {
	if price <= 0 || e.cfg.StartingEquity <= 0 {
		return 0, 0, false
	}
	eq := e.equityLocked()
	target := eq * TargetExposurePct / 100

	// A symbol's share of the deployed capital, normalised across the positions the
	// account can ACTUALLY HOLD -- not across every symbol that was allocated.
	//
	// THE BUG THIS FIXES, found by measuring rather than by reading: shares were
	// divided by the whole allocation's weight, so with 27 allocated symbols and a
	// concurrency limit of 3 each position took 1/27 of the capital. The account
	// deployed 5.17% of a 95% target and would never have reached it, because the
	// denominator counted symbols that could never be open at the same time.
	//
	// The denominator is now the sum of the weights of the top MaxConcurrent
	// symbols -- the book the account can actually hold -- so the capital is fully
	// committed by the positions that exist.
	share := 1.0 / float64(maxInt(e.cfg.MaxConcurrent, 1))
	if w := e.allocation.Weight(symbol); w > 0 {
		if denom := e.topWeightSum(); denom > 0 {
			share = w / denom
		}
	}
	notional = target * share
	if notional > e.cash {
		// Cash is still a hard constraint: an unfilled order is not a position.
		notional = e.cash
	}
	if notional <= 0 {
		return 0, 0, false
	}
	units = notional / price
	if units <= 0 || math.IsInf(units, 0) || math.IsNaN(units) {
		return 0, 0, false
	}
	return units, notional, true
}

// topWeightSum is the combined weight of the symbols the account can hold at once.
//
// It is the correct denominator for splitting capital, because it is the sum of the
// shares that will actually be open. Dividing by the whole allocation instead meant
// each position was sized as though every allocated symbol were held
// simultaneously, which is only true if the concurrency limit equals the watchlist
// size -- and it never does.
func (e *Engine) topWeightSum() float64 {
	n := maxInt(e.cfg.MaxConcurrent, 1)
	weights := make([]float64, 0, len(e.allocation.Weights))
	for _, w := range e.allocation.Weights {
		if w.Weight > 0 {
			weights = append(weights, w.Weight)
		}
	}
	if len(weights) == 0 {
		return 0
	}
	sortFloatsDesc(weights)
	if len(weights) > n {
		weights = weights[:n]
	}
	sum := 0.0
	for _, w := range weights {
		sum += w
	}
	return sum
}

func sortFloatsDesc(v []float64) {
	for i := 1; i < len(v); i++ {
		for j := i; j > 0 && v[j] > v[j-1]; j-- {
			v[j], v[j-1] = v[j-1], v[j]
		}
	}
}
