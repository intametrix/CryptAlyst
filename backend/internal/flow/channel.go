package flow

// A genuine Donchian channel over recent ticks.
//
// WHY THIS EXISTS. The turtle entry confirm was being fed a channel computed from
// the CURRENT price:
//
//	donchianHigh = price + 2*sigma
//
// and the entry then required `price >= donchianHigh`, which expands to
// `price >= price + 2*sigma` -- FALSE for every positive sigma. The pump entry
// could not fire. Ever. Neither could the dump. The engine reported "no Donchian
// breakout" for every alert it evaluated, which reads like a cautious system
// declining a marginal setup rather than a condition that is mathematically
// unsatisfiable.
//
// It is the third impossible-condition bug in this project: an agreement metric
// that scored a lone horizon at 100%, a precursor whose quiet and surge conditions
// were mutually contradictory, and now a breakout that required price to exceed
// itself. All three looked like working code and all three could never fire.
//
// The fix is a real channel: the highest and lowest price actually seen over a
// recent window, which is what a Donchian breakout means. Breaking it is then both
// meaningful and reachable.

// ChannelWindowSeconds is the lookback for the breakout channel.
//
// Deliberately short. The classic Turtle used 20 DAILY bars; on a system measuring
// onset at seconds, the equivalent question is "has price exceeded its recent
// range", and a window much longer than a few minutes would be answering a
// question about yesterday.
const ChannelWindowSeconds = 120

// Channel is the recent price envelope for one symbol.
type Channel struct {
	High float64 `json:"high"`
	Low  float64 `json:"low"`
	// Samples counts the ticks the channel was built from. A channel from two
	// ticks is not a range, and a caller can refuse to act on one.
	Samples int `json:"samples"`
	// SpanSec is how much time the samples actually cover.
	SpanSec float64 `json:"span_sec"`
	// Measured is false when there are too few samples for the envelope to mean
	// anything. Reported rather than omitted so "not measurable" is distinguishable
	// from "measured and narrow" -- the distinction that three bugs have now turned
	// on.
	Measured bool `json:"measured"`
}

// MinChannelSamples is the floor below which an envelope is not a range.
const MinChannelSamples = 10

// PriceChannel computes the recent high and low from the tick ring.
func (s *symbolFlow) PriceChannel(windowSec float64, now int64) Channel {
	if windowSec <= 0 {
		windowSec = ChannelWindowSeconds
	}
	cutoff := now - int64(windowSec*1000)

	var hi, lo float64
	samples := 0
	var oldest, newest int64
	for _, t := range s.buf {
		if t.TS < cutoff || t.Price <= 0 {
			continue
		}
		if samples == 0 || t.Price > hi {
			hi = t.Price
		}
		if samples == 0 || t.Price < lo {
			lo = t.Price
		}
		if oldest == 0 || t.TS < oldest {
			oldest = t.TS
		}
		if t.TS > newest {
			newest = t.TS
		}
		samples++
	}

	out := Channel{High: hi, Low: lo, Samples: samples}
	if samples > 0 && newest > oldest {
		out.SpanSec = roundTo(float64(newest-oldest)/1000, 2)
	}
	out.Measured = samples >= MinChannelSamples && hi > lo
	return out
}

// ChannelFor is the public accessor, so the API can build a real channel for the
// turtle entry confirm instead of a synthetic band.
func (e *Engine) ChannelFor(symbol string, windowSec float64, now int64) (Channel, bool) {
	e.mu.Lock()
	s, ok := e.states[symbol]
	e.mu.Unlock()
	if !ok || s == nil {
		return Channel{}, false
	}
	return s.PriceChannel(windowSec, now), true
}

// Breakout reports which side of a channel price has broken, if either.
//
// A breakout needs a MEASURED channel. Breaking an unmeasured one is meaningless,
// and returning "no breakout" for it would be indistinguishable from a real
// decline -- the ambiguity this whole exercise is about removing.
func (c Channel) Breakout(price float64) (direction int, ok bool) {
	if !c.Measured || price <= 0 {
		return 0, false
	}
	if price > c.High {
		return 1, true
	}
	if price < c.Low {
		return -1, true
	}
	return 0, false
}
