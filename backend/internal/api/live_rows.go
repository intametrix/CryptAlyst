package api

import "encoding/json"

// Typed rows at the boundary.
//
// Sweep results arrive as untyped JSON from the agent: thirty call sites then read
// them as map[string]any, so a key that is missing or spelled differently produces
// a zero instead of an error. The live board rendered `"trace": null` from exactly
// that class of mistake and blanked the panel.
//
// The wire stays untyped — it is the agent's JSON — but it is decoded once, here,
// and everything downstream is a struct the compiler checks.

// sweepRow is one scored candidate: a model at a window and horizon.
type sweepRow struct {
	Method   string  `json:"method"`
	Lookback int     `json:"lookback"`
	Bars     int     `json:"bars"`
	Horizon  int     `json:"horizon"`
	R2       float64 `json:"r2"`
	MAPEPct  float64 `json:"mape_pct"`
	MAE      float64 `json:"mae"`
	Skill    float64 `json:"skill_vs_flat"`
	Tests    int     `json:"tests,omitempty"`
	Family   string  `json:"family,omitempty"`
	Source   string  `json:"source,omitempty"`
}

// sweepSeries is the path the chart draws for one configuration.
type sweepSeries struct {
	Method   string    `json:"method"`
	Lookback int       `json:"lookback"`
	MAPEPct  float64   `json:"mape_pct"`
	Points   []float64 `json:"points"`
	Times    []int64   `json:"times,omitempty"`
}

// decodeRows converts the agent's result array into typed rows, dropping anything
// that does not fit the contract instead of letting a zero flow onward.
func decodeRows(raw any) []sweepRow {
	if raw == nil {
		return nil
	}
	b, err := json.Marshal(raw)
	if err != nil {
		return nil
	}
	var rows []sweepRow
	if err := json.Unmarshal(b, &rows); err != nil {
		return nil
	}
	return rows
}

// decodeSeries does the same for the chart series.
func decodeSeries(raw any) []sweepSeries {
	if raw == nil {
		return nil
	}
	b, err := json.Marshal(raw)
	if err != nil {
		return nil
	}
	var out []sweepSeries
	if err := json.Unmarshal(b, &out); err != nil {
		return nil
	}
	return out
}
