package flow

// LastPrice returns the most recently observed price for a symbol.
//
// This exists so the ledger's resolution tracker can sample prices every few
// seconds without issuing a REST request per symbol per sample. The engine
// already holds the freshest price for every tracked symbol, so resolution
// tracking costs no exchange weight at all and is never stale by more than a
// single tick.
func (e *Engine) LastPrice(symbol string) (float64, bool) {
	e.mu.Lock()
	defer e.mu.Unlock()
	state, ok := e.states[symbol]
	if !ok || !state.hasLast || state.lastPric <= 0 {
		return 0, false
	}
	return state.lastPric, true
}
