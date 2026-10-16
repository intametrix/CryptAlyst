package models

import "pumpdump/backend/internal/flow"

// FlowSource adapts the flow engine to the runner's narrow Row.
//
// The adapter exists so the models never import the flow engine. That is what
// makes them testable without a live market and replaceable without touching the
// measurement layer -- a model that can reach into the engine eventually depends
// on all of it.
type FlowSource struct {
	Engine *flow.Engine
}

// SnapshotForModels reads the current market once per tick.
func (f *FlowSource) SnapshotForModels(atMS int64) []Row {
	if f == nil || f.Engine == nil {
		return nil
	}
	cands := f.Engine.Snapshot(0, atMS)
	rows := make([]Row, 0, len(cands))
	for i := range cands {
		c := &cands[i]
		r := Row{
			Symbol: c.Symbol, Price: c.Price,
			QuoteVolume24h: c.QuoteVolume24h, ChangePct24h: c.PriceChangePct24h,
			High24h: c.High24h, Low24h: c.Low24h,
			Sigma10s: c.Sigma10s, SigmaMultiple: c.SigmaMultiple,
			BurstMultiple: c.BurstMultiple, BurstZ: c.BurstZ,
			TakerBuyRatio: c.TakerBuyRatio, TakerQuote: c.TakerQuote,
			RangePosition: c.RangePosition,
			TrendAgree:    c.TrendAgreement, TrendSign: c.TrendSign,
		}
		if c.Change10s != nil {
			r.Change10s = *c.Change10s
		}
		if c.Change60s != nil {
			r.Change60s = *c.Change60s
		}
		if c.Precursor != nil {
			r.Precursor = c.Precursor.Score
		}
		if r.TakerBuyRatio == 0 {
			// An unmeasured ratio is 0.5 (balanced), NOT 0 (all selling). A zero
			// here would make every symbol with an empty taker buffer look like
			// maximum sell pressure, which is precisely the silent-zero failure
			// that emptied two other features today.
			r.TakerBuyRatio = 0.5
		}
		rows = append(rows, r)
	}
	return rows
}
