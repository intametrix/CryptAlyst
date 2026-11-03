package envelope

import "testing"

// The registry is the contract: an unregistered producer cannot write, and every
// finance topic the architecture lists is present.
func TestTopicRegistryMatchesTheArchitecture(t *testing.T) {
	required := []string{
		"market.price", "market.depth", "market.candle", "market.mini24h", "ticks",
		"intel.signal", "intel.flow", "intel.movers", "intel.board", "intel.ta", "intel.forecast",
		"alert.pumpdump",
		"model.decision", "model.fill", "model.pnl",
		"risk.state",
	}
	for _, name := range required {
		if _, ok := TopicByName(name); !ok {
			t.Errorf("topic %q is missing from the registry", name)
		}
	}
	// Unknown producers are refused, which is what makes the registry an enforcement
	// point rather than documentation.
	if MayProduce("model.decision", "not-a-model") {
		t.Error("an unregistered producer was allowed to write model.decision")
	}
	if !MayProduce("model.decision", "models10") {
		t.Error("a registered producer was refused model.decision")
	}
}

// Generations advance per topic and key, and never go backwards.
func TestGenerationsAreMonotonicPerTopicAndKey(t *testing.T) {
	a := NextGeneration("intel.forecast", "AAA")
	b := NextGeneration("intel.forecast", "AAA")
	c := NextGeneration("intel.forecast", "BBB")
	if !(a < b) {
		t.Fatalf("generations did not advance: %d then %d", a, b)
	}
	if c >= b {
		t.Fatalf("a different key shared the counter: BBB=%d after AAA=%d", c, b)
	}
}
