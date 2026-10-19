// Package topics is the single registry of bus topics: which exchange carries
// them, which producers may write them, and how long the broker keeps them.
package topics

type Topic struct {
	Name      string
	Exchange  string
	Producers []string
	Retention string
}

var All = []Topic{
	{"market.price", "market", []string{"market_data"}, "drop"},
	{"market.candle", "market", []string{"market_data"}, "drop"},
	{"market.depth", "market", []string{"market_data"}, "drop"},
	{"market.mini24h", "market", []string{"market_data"}, "drop"},
	{"ticks", "market", []string{"market_data"}, "drop"},
	{"intel.signal", "intel", []string{"signals"}, "60s"},
	{"intel.flow", "intel", []string{"signals"}, "60s"},
	{"intel.movers", "intel", []string{"market_data", "signals"}, "60s"},
	{"intel.ta", "intel", []string{"tad"}, "60s"},
	{"intel.forecast", "intel", []string{"forecast"}, "60s"},
	{"alert.pumpdump", "alert", []string{"backend", "signal_bus"}, "300s"},
	{"model.decision", "model", []string{"alpha_rank", "models10", "crypto_pack", "executor",
		"alpha_signals", "skim_pump", "curve_rider", "hft", "movers", "grid"}, "5m"},
	{"model.fill", "model", []string{"alpha_rank", "models10", "crypto_pack", "executor",
		"alpha_signals", "skim_pump", "curve_rider", "hft", "movers", "grid"}, "5m"},
	{"model.pnl", "model", []string{"alpha_rank", "models10", "crypto_pack", "executor",
		"alpha_signals", "skim_pump", "curve_rider", "hft", "movers", "grid", "supervisor"}, "5m"},
	{"model.rank", "model", []string{"alpha_rank"}, "5m"},
	{"risk.state", "risk", []string{"risk"}, "5m"},
	{"review.verdict", "review", []string{"portfolio_review"}, "5m"},
	{"exec.quote", "exec", []string{"maker"}, "60s"},
	{"system.health", "system", []string{"healthpub", "master"}, "10m"},
}

func Names() []string {
	out := make([]string, 0, len(All))
	for _, t := range All {
		out = append(out, t.Name)
	}
	return out
}

func Get(name string) (Topic, bool) {
	for _, t := range All {
		if t.Name == name {
			return t, true
		}
	}
	return Topic{}, false
}
