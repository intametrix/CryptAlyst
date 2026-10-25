package graph

// backtest.go replays a graph over historical contexts and measures the
// outcome: hit rate and compounded equity. The graph's source nodes read a
// shared *Context that the backtest mutates per bar, so the same graph that
// runs live can be replayed without recompiling.

// Context is the mutable market state source nodes read during a replay.
// A live feed and a historical feed both populate it; the graph does not know
// which one it is.
type Context struct {
	TSMS       int64
	Symbol     string
	Price      float64
	Volume     float64
	TakerRatio float64 // 0..1 buy fraction
	TA         float64
	Regime     string  // rising / falling / sideways
	Sentiment  float64 // 0..100
}

// ContextSources builds the standard source nodes that read a shared context.
// The closures capture the pointer, so the backtest updates the pointed-to
// value per bar and every source sees the new bar.
func ContextSources(ctx *Context) []Node {
	return []Node{
		SourceNode{
			IDStr: "source.price", TitleStr: "Market Price",
			Outs: []Port{{Name: "price", Dir: Output, Type: TypePrice}},
			Emit: func() map[string]Signal {
				return map[string]Signal{"price": New(TypePrice, ctx.Symbol, ctx.Price, 0)}
			},
		},
		SourceNode{
			IDStr: "source.volume", TitleStr: "Volume",
			Outs: []Port{{Name: "volume", Dir: Output, Type: TypeVolume}},
			Emit: func() map[string]Signal {
				return map[string]Signal{"volume": New(TypeVolume, ctx.Symbol, ctx.Volume, 0)}
			},
		},
		SourceNode{
			IDStr: "source.flow", TitleStr: "Flow Analysis",
			Outs: []Port{{Name: "flow", Dir: Output, Type: TypeScore}},
			Emit: func() map[string]Signal {
				return map[string]Signal{"flow": New(TypeScore, ctx.Symbol, ctx.TakerRatio*100, 0)}
			},
		},
		SourceNode{
			IDStr: "source.ta", TitleStr: "TA Indicator",
			Outs: []Port{{Name: "ta", Dir: Output, Type: TypeTA}},
			Emit: func() map[string]Signal {
				return map[string]Signal{"ta": New(TypeTA, ctx.Symbol, ctx.TA, 0)}
			},
		},
		SourceNode{
			IDStr: "source.regime", TitleStr: "Market Regime",
			Outs: []Port{{Name: "direction", Dir: Output, Type: TypeDirection}},
			Emit: func() map[string]Signal {
				d := 0.0
				if ctx.Regime == "rising" {
					d = 1
				} else if ctx.Regime == "falling" {
					d = -1
				}
				return map[string]Signal{"direction": New(TypeDirection, ctx.Symbol, d, 0).WithBasis("regime " + ctx.Regime)}
			},
		},
		SourceNode{
			IDStr: "source.sentiment", TitleStr: "Sentiment",
			Outs: []Port{{Name: "score", Dir: Output, Type: TypeScore}},
			Emit: func() map[string]Signal {
				return map[string]Signal{"score": New(TypeScore, ctx.Symbol, ctx.Sentiment, 0)}
			},
		},
	}
}

// VerdictNode is a sink that records the latest direction it consumed.
type VerdictNode struct {
	Last Signal
}

// ID implements Node.
func (v *VerdictNode) ID() string { return "sink.verdict" }

// Title implements Node.
func (v *VerdictNode) Title() string { return "Verdict" }

// Kind implements Node.
func (v *VerdictNode) Kind() Kind { return Sink }

// Inputs implements Node.
func (v *VerdictNode) Inputs() []Port {
	return []Port{{Name: "in", Dir: Input, Type: TypeDirection, Desc: "final direction"}}
}

// Outputs implements Node.
func (v *VerdictNode) Outputs() []Port { return nil }

// Eval implements Node.
func (v *VerdictNode) Eval(in map[string]Signal) (map[string]Signal, error) {
	if s, ok := in["in"]; ok {
		v.Last = s
	}
	return map[string]Signal{}, nil
}

// Trade is one signal and its measured outcome.
type Trade struct {
	TSMS   int64
	Symbol string
	Dir    int
	Entry  float64
	Exit   float64
	Ret    float64 // signed return in the signal's direction
	Right  bool
}

// Result summarises a replay.
type Result struct {
	Bars    int
	Signals int
	Right   int
	HitRate float64
	Equity  float64 // compounded; 1.0 is breakeven
	Trades  []Trade
}

// Backtest replays a graph over pre-computed contexts.
type Backtest struct {
	Graph    *Graph
	Ctx      *Context // the shared context the graph's sources read
	Contexts []Context
	Closes   []float64 // parallel to Contexts; the price at each bar
	Verdict  *VerdictNode
	Forward  int // bars ahead to measure the outcome (>= 1)
}

// Run executes the graph at every bar and measures each directional signal
// against the close Forward bars later.
func (b *Backtest) Run() (Result, error) {
	res := Result{Bars: len(b.Contexts)}
	fwd := b.Forward
	if fwd <= 0 {
		fwd = 1
	}
	equity := 1.0
	for i := range b.Contexts {
		*b.Ctx = b.Contexts[i]
		if _, err := b.Graph.Evaluate(); err != nil {
			return res, err
		}
		dir := b.Verdict.Last.Direction()
		if dir == 0 {
			continue
		}
		entry := b.priceAt(i)
		if entry == 0 {
			continue
		}
		j := i + fwd
		if j >= len(b.Closes) {
			break // no forward bar to measure
		}
		exit := b.Closes[j]
		ret := (exit - entry) / entry * float64(dir)
		right := ret > 0
		t := Trade{
			TSMS:   b.Contexts[i].TSMS,
			Symbol: b.Verdict.Last.Symbol,
			Dir:    dir,
			Entry:  entry,
			Exit:   exit,
			Ret:    ret,
			Right:  right,
		}
		res.Signals++
		if right {
			res.Right++
		}
		equity *= 1 + ret
		res.Trades = append(res.Trades, t)
	}
	if res.Signals > 0 {
		res.HitRate = float64(res.Right) / float64(res.Signals)
	}
	res.Equity = equity
	return res, nil
}

func (b *Backtest) priceAt(i int) float64 {
	if i < len(b.Closes) {
		return b.Closes[i]
	}
	return 0
}
