package graph

// sources.go defines the bus source nodes: market data, volume, flow, TA,
// regime, and sentiment. Each is a provider-based source — it wraps a function
// that reads the live value, so the node is testable with a mock and usable
// with the real engines wired in the server.
//
// The pattern is uniform: a constructor takes a symbol and a provider, and
// returns a Source node whose Emit reads the provider and wraps it in a Signal.

// SourceNode is a generic bus source wrapping an emit function.
type SourceNode struct {
	IDStr    string
	TitleStr string
	Outs     []Port
	Emit     func() map[string]Signal
}

// ID implements Node.
func (s SourceNode) ID() string { return s.IDStr }

// Title implements Node.
func (s SourceNode) Title() string { return s.TitleStr }

// Kind implements Node.
func (s SourceNode) Kind() Kind { return Source }

// Inputs implements Node.
func (s SourceNode) Inputs() []Port { return nil }

// Outputs implements Node.
func (s SourceNode) Outputs() []Port { return s.Outs }

// Eval implements Node.
func (s SourceNode) Eval(map[string]Signal) (map[string]Signal, error) {
	if s.Emit == nil {
		return map[string]Signal{}, nil
	}
	return s.Emit(), nil
}

// PriceSource emits the current price for a fixed symbol.
func PriceSource(symbol string, price func(string) float64) Node {
	return SourceNode{
		IDStr: "source.price", TitleStr: "Market Price",
		Outs: []Port{{Name: "price", Dir: Output, Type: TypePrice, Desc: "last price"}},
		Emit: func() map[string]Signal {
			return map[string]Signal{"price": New(TypePrice, symbol, price(symbol), 0)}
		},
	}
}

// VolumeSource emits the current volume for a fixed symbol.
func VolumeSource(symbol string, volume func(string) float64) Node {
	return SourceNode{
		IDStr: "source.volume", TitleStr: "Volume",
		Outs: []Port{{Name: "volume", Dir: Output, Type: TypeVolume, Desc: "rolling volume"}},
		Emit: func() map[string]Signal {
			return map[string]Signal{"volume": New(TypeVolume, symbol, volume(symbol), 0)}
		},
	}
}

// FlowSource emits taker buy ratio as a 0..100 score for a fixed symbol.
func FlowSource(symbol string, takerRatio func(string) float64) Node {
	return SourceNode{
		IDStr: "source.flow", TitleStr: "Flow Analysis",
		Outs: []Port{{Name: "flow", Dir: Output, Type: TypeScore, Desc: "taker buy ratio (0..100)"}},
		Emit: func() map[string]Signal {
			return map[string]Signal{"flow": New(TypeScore, symbol, takerRatio(symbol)*100, 0)}
		},
	}
}

// TASource emits a technical-analysis indicator value for a fixed symbol.
func TASource(symbol string, indicator func(string) float64) Node {
	return SourceNode{
		IDStr: "source.ta", TitleStr: "TA Indicator",
		Outs: []Port{{Name: "ta", Dir: Output, Type: TypeTA, Desc: "indicator value"}},
		Emit: func() map[string]Signal {
			return map[string]Signal{"ta": New(TypeTA, symbol, indicator(symbol), 0)}
		},
	}
}

// RegimeSource emits the broad-market regime as a direction:
// +1 rising, -1 falling, 0 sideways.
func RegimeSource(regime func() string) Node {
	return SourceNode{
		IDStr: "source.regime", TitleStr: "Market Regime",
		Outs: []Port{{Name: "direction", Dir: Output, Type: TypeDirection, Desc: "rising/falling/sideways"}},
		Emit: func() map[string]Signal {
			d := 0.0
			switch regime() {
			case "rising":
				d = 1
			case "falling":
				d = -1
			}
			return map[string]Signal{"direction": New(TypeDirection, "", d, 0).WithBasis("regime " + regime())}
		},
	}
}

// SentimentSource emits a 0..100 sentiment score. The provider is a placeholder
// until a sentiment feed is wired; it already has the right shape.
func SentimentSource(symbol string, sentiment func(string) float64) Node {
	return SourceNode{
		IDStr: "source.sentiment", TitleStr: "Sentiment",
		Outs: []Port{{Name: "score", Dir: Output, Type: TypeScore, Desc: "sentiment 0..100"}},
		Emit: func() map[string]Signal {
			return map[string]Signal{"score": New(TypeScore, symbol, sentiment(symbol), 0)}
		},
	}
}
