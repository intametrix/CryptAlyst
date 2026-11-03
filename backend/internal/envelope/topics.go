package envelope

import "sync"

// The one topic table.
//
// Every topic declares its plane, its producers and its retention. A producer that
// is not listed is refused by the authority interceptor; a payload older than the
// topic's TTL is refused by the freshness interceptor. Both refusals are recorded
// as packets, so "why not" is something you can open.

// Topic is one channel in the registry.
type Topic struct {
	Name     string
	Plane    Plane
	Producer []string
	TTLMS    int64
	// Record is false for topics that live only for the moment they arrive.
	Record bool
}

var topics = []Topic{
	// market — live only, never recorded: the bus is a state channel, not a queue.
	{Name: "market.price", Plane: PlaneMarket, Producer: []string{"marketdata"}, TTLMS: 2_000},
	{Name: "market.depth", Plane: PlaneMarket, Producer: []string{"marketdata"}, TTLMS: 2_000},
	{Name: "market.candle", Plane: PlaneMarket, Producer: []string{"marketdata", "store"}, TTLMS: 60_000},
	{Name: "market.mini24h", Plane: PlaneMarket, Producer: []string{"marketdata"}, TTLMS: 5_000},
	{Name: "ticks", Plane: PlaneMarket, Producer: []string{"marketdata"}, TTLMS: 2_000},

	// intel — computed readings, each with a reference back to its inputs.
	{Name: "intel.signal", Plane: PlaneIntel, Producer: []string{"signals", "detect"}, TTLMS: 60_000, Record: true},
	{Name: "intel.flow", Plane: PlaneIntel, Producer: []string{"signals", "flow", "detect"}, TTLMS: 60_000, Record: true},
	{Name: "intel.movers", Plane: PlaneIntel, Producer: []string{"marketdata", "signals"}, TTLMS: 60_000, Record: true},
	{Name: "intel.board", Plane: PlaneIntel, Producer: []string{"market_agent", "engine"}, TTLMS: 60_000, Record: true},
	{Name: "intel.ta", Plane: PlaneIntel, Producer: []string{"tad", "forecast"}, TTLMS: 60_000, Record: true},
	{Name: "intel.forecast", Plane: PlaneIntel, Producer: []string{"forecast", "engine", "agent"}, TTLMS: 60_000, Record: true},
	{Name: "intel.forecast.outcome", Plane: PlaneIntel, Producer: []string{"recorder", "grader"}, TTLMS: 86_400_000, Record: true},
	{Name: "intel.backtest", Plane: PlaneIntel, Producer: []string{"forecast", "agent"}, TTLMS: 300_000, Record: true},

	// alert
	{Name: "alert.pumpdump", Plane: PlaneIntel, Producer: []string{"backend", "signal_bus", "detect"}, TTLMS: 300_000, Record: true},

	// model — decisions, fills and pnl. The trading trail.
	{Name: "model.sweep", Plane: PlaneModel, Producer: []string{"engine", "agent"}, TTLMS: 300_000, Record: true},
	{Name: "model.selection", Plane: PlaneModel, Producer: []string{"engine"}, TTLMS: 300_000, Record: true},
	{Name: "model.decision", Plane: PlaneModel, Producer: []string{"models10", "curve_rider", "engine", "risk"}, TTLMS: 300_000, Record: true},
	{Name: "model.fill", Plane: PlaneModel, Producer: []string{"models10", "curve_rider", "engine", "turtle"}, TTLMS: 300_000, Record: true},
	{Name: "model.pnl", Plane: PlaneModel, Producer: []string{"models10", "curve_rider", "engine", "turtle", "supervisor"}, TTLMS: 300_000, Record: true},

	// risk — the governor's verdict, including refusals.
	{Name: "risk.state", Plane: PlaneRisk, Producer: []string{"risk", "runner"}, TTLMS: 300_000, Record: true},
	{Name: "risk.refusal", Plane: PlaneRisk, Producer: []string{"risk", "runner"}, TTLMS: 300_000, Record: true},

	// control and system
	{Name: "control.engine", Plane: PlaneControl, Producer: []string{"engine"}, TTLMS: 60_000, Record: true},
	{Name: "system.error", Plane: PlaneSystem, Producer: []string{"platform", "ui"}, TTLMS: 86_400_000, Record: true},
}

// TopicByName looks a topic up in the registry.
func TopicByName(name string) (Topic, bool) {
	for _, t := range topics {
		if t.Name == name {
			return t, true
		}
	}
	return Topic{}, false
}

// Topics returns the registry, for the API and the UI.
func Topics() []Topic { return append([]Topic(nil), topics...) }

// MayProduce answers the authority interceptor's question.
func MayProduce(topic, producer string) bool {
	t, ok := TopicByName(topic)
	if !ok {
		return false
	}
	for _, p := range t.Producer {
		if p == producer {
			return true
		}
	}
	return false
}

// Generations are monotonic per (topic, key): a consumer can tell a newer answer
// from an older one without trusting a clock.
var (
	genMu sync.Mutex
	gens  = map[string]int64{}
)

// NextGeneration stamps the next sequence for a topic and key.
func NextGeneration(topic, key string) int64 {
	genMu.Lock()
	defer genMu.Unlock()
	gens[topic+"|"+key]++
	return gens[topic+"|"+key]
}

// Generation reads the current value without advancing it.
func Generation(topic, key string) int64 {
	genMu.Lock()
	defer genMu.Unlock()
	return gens[topic+"|"+key]
}
