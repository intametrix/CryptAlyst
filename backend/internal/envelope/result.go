package envelope

// result.go defines the sweep response.

// ComboResult is one parameter combination's outcome.
type ComboResult struct {
	Params  map[string]float64 `json:"params"`
	HitRate float64            `json:"hit_rate"`
	Equity  float64            `json:"equity"`
	Signals int                `json:"signals"`
	Right   int                `json:"right"`
}

// SweepResult is the payload of a KindResult envelope.
type SweepResult struct {
	JobID      string        `json:"job_id"`
	Combos     []ComboResult `json:"combos"`
	Device     string        `json:"device"` // "cpu" | "gpu" | "cuda" | "metal"
	DurationMS int64         `json:"duration_ms"`
}
