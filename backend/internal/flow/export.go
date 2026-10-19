package flow

// FlowBin is one reconstructed 10-second flow bucket for the history API.
type FlowBin struct {
	T             int64   `json:"t"` // bin CLOSE time, epoch ms
	FlowQuote     float64 `json:"flow_quote"`
	BaselineQuote float64 `json:"baseline_quote"`
	BurstMultiple float64 `json:"burst_multiple"`
	Change10s     float64 `json:"change_10s"`
}

// History reconstructs the most recent 10-second flow bins for a symbol from
// its tick ring buffer, oldest first, at most `bins` bins. It returns an empty
// slice for an unknown symbol. Bucket convention matches accumulateBucket:
// t.TS/10000. Change10s is measured from the first to the last tick inside
// the bin. Safe for concurrent use.
func (e *Engine) History(symbol string, bins int) []FlowBin {
	e.mu.Lock()
	defer e.mu.Unlock()

	s, ok := e.states[symbol]
	if !ok || s.count == 0 || bins <= 0 {
		return []FlowBin{}
	}

	type agg struct {
		flow        float64
		first, last float64
	}
	order := make([]int64, 0, bins)
	byBucket := make(map[int64]*agg, bins)
	for i := 0; i < s.count; i++ {
		t := s.at(i)
		bucket := t.TS / 10000
		a, seen := byBucket[bucket]
		if !seen {
			a = &agg{first: t.Price}
			byBucket[bucket] = a
			order = append(order, bucket)
		}
		a.flow += t.DQuote
		a.last = t.Price
	}

	if len(order) > bins {
		order = order[len(order)-bins:]
	}

	baseline := s.baseline.reference()
	out := make([]FlowBin, 0, len(order))
	for _, bucket := range order {
		a := byBucket[bucket]
		change := 0.0
		if a.first > 0 && a.last > 0 {
			change = (a.last - a.first) / a.first
		}
		multiple := 0.0
		if baseline > 0 {
			multiple = a.flow / baseline
		}
		out = append(out, FlowBin{
			T:             (bucket + 1) * 10000,
			FlowQuote:     a.flow,
			BaselineQuote: baseline,
			BurstMultiple: multiple,
			Change10s:     change,
		})
	}
	return out
}
