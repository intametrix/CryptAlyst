package api

import (
	"bufio"
	"encoding/json"
	"log"
	"os"
	"strings"
)

// ForSymbol returns up to `limit` of a symbol's alerts, oldest first.
//
// Oldest-first is deliberate: chart libraries lay markers down in order, and a
// caller that has to reverse them itself will eventually forget to.
func (b *AlertBuffer) ForSymbol(symbol string, limit int) []Alert {
	b.mu.RLock()
	history := append([]Alert(nil), b.bySymbol[symbol]...)
	b.mu.RUnlock()

	// Oldest first, capped to the most recent `limit`.
	if limit > 0 && len(history) > limit {
		history = history[len(history)-limit:]
	}
	return history
}

// SetHistoryPath enables durable alert history at `path` and loads anything
// already there.
//
// Without this, "previous markers" only means "markers since the last restart",
// which is exactly the wrong answer for a chart: the interesting question is
// what the symbol did earlier.
func (b *AlertBuffer) SetHistoryPath(path string) {
	if strings.TrimSpace(path) == "" {
		return
	}
	b.mu.Lock()
	b.path = path
	b.mu.Unlock()
	b.loadFromDisk(path)
}

func (b *AlertBuffer) loadFromDisk(path string) {
	file, err := os.Open(path)
	if err != nil {
		// A missing file just means no history yet.
		return
	}
	defer file.Close()

	loaded := 0
	scanner := bufio.NewScanner(file)
	// Alert lines can be long (nested metrics), so raise the scanner limit.
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for scanner.Scan() {
		line := scanner.Bytes()
		if len(line) == 0 {
			continue
		}
		var alert Alert
		if err := json.Unmarshal(line, &alert); err != nil {
			continue
		}
		if alert.Symbol == "" {
			continue
		}
		alert.Type = "alert"
		b.mu.Lock()
		history := append(b.bySymbol[alert.Symbol], alert)
		if len(history) > 400 {
			history = history[len(history)-400:]
		}
		b.bySymbol[alert.Symbol] = history
		b.mu.Unlock()
		loaded++
	}
	if loaded > 0 {
		log.Printf("alert history: loaded %d alerts from %s", loaded, path)
	}
}

// appendToDisk writes one alert, best effort. History is a convenience, so a
// write failure must never take the scanner down with it.
func (b *AlertBuffer) appendToDisk(path string, alert Alert) {
	if path == "" {
		return
	}
	data, err := json.Marshal(alert)
	if err != nil {
		return
	}
	b.fileMu.Lock()
	defer b.fileMu.Unlock()
	file, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return
	}
	defer file.Close()
	if _, err := file.Write(append(data, '\n')); err != nil {
		log.Printf("alert history: write failed: %v", err)
	}
}

// --------------------------------------------------------------------------
// Retention
// --------------------------------------------------------------------------
//
// The alert history file is APPEND-ONLY and is never truncated, rotated or
// rewritten. That is a deliberate guarantee, not an accident of the current
// code, so it is written down here where a future change would have to
// consciously break it:
//
//   - alerts are opened with O_APPEND|O_CREATE|O_WRONLY and never O_TRUNC
//   - nothing in this package rotates, renames or compacts the file
//   - the in-memory caps (a ring of `limit`, and 400 per symbol) bound MEMORY
//     only; they never delete history
//
// The reason: a detector whose past is erasable cannot be evaluated. Every
// measurement in this project -- the ledger, the learner, the marker history on
// every chart -- reads from records that were written once and never revised.
// Deleting history to save disk would quietly invalidate all of it.

// HistoryStats describes the retained alert history.
type HistoryStats struct {
	Path     string `json:"path"`
	Records  int    `json:"records"`
	Bytes    int64  `json:"bytes"`
	OldestMS int64  `json:"oldest_ms"`
	NewestMS int64  `json:"newest_ms"`
	Symbols  int    `json:"symbols"`
	// Retained is always true; it is reported so a client can render the
	// guarantee rather than assume it.
	Retained bool `json:"retained"`
	// Malformed counts unparseable lines. A crash or power loss can leave a
	// partial trailing line, and a strict parser would then declare the whole
	// history unreadable over one bad byte. Skipping is right; skipping
	// SILENTLY is not, because a reader that quietly drops records looks
	// identical to one reading a shorter history.
	Malformed int    `json:"malformed"`
	Note      string `json:"note"`
}

// HistoryStats scans the retained file. It is O(file) and reads line by line,
// so it is safe on a history far larger than memory.
func (b *AlertBuffer) HistoryStats() HistoryStats {
	b.mu.RLock()
	path := b.path
	b.mu.RUnlock()

	stats := HistoryStats{
		Path:     path,
		Retained: true,
		Note:     "alert history is append-only; nothing truncates or rotates it",
	}
	if path == "" {
		stats.Note = "alert history is disabled (no path configured)"
		return stats
	}
	file, err := os.Open(path)
	if err != nil {
		return stats
	}
	defer file.Close()
	if info, err := file.Stat(); err == nil {
		stats.Bytes = info.Size()
	}

	symbols := make(map[string]struct{}, 64)
	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for scanner.Scan() {
		line := scanner.Bytes()
		if len(line) == 0 {
			continue
		}
		var alert Alert
		if err := json.Unmarshal(line, &alert); err != nil {
			// Skipped, not fatal -- and counted, so it is visible.
			stats.Malformed++
			continue
		}
		stats.Records++
		if alert.Symbol != "" {
			symbols[alert.Symbol] = struct{}{}
		}
		if stats.OldestMS == 0 || alert.TSMS < stats.OldestMS {
			stats.OldestMS = alert.TSMS
		}
		if alert.TSMS > stats.NewestMS {
			stats.NewestMS = alert.TSMS
		}
	}
	stats.Symbols = len(symbols)
	if stats.Malformed > 0 {
		log.Printf("alert history: skipped %d malformed line(s) in %s (partial write or corruption)",
			stats.Malformed, path)
	}
	return stats
}

// ReadHistory answers queries against the FULL retained history, not the
// in-memory ring.
//
// The ring holds the most recent `limit` alerts for the live feed. Without this
// reader, "history is kept" would be true on disk and useless in practice: an
// alert from yesterday would exist and be unreachable. Newest-first, capped.
func (b *AlertBuffer) ReadHistory(symbol string, sinceMS int64, limit int) ([]Alert, int, error) {
	b.mu.RLock()
	path := b.path
	b.mu.RUnlock()
	if path == "" {
		return nil, 0, nil
	}
	if limit <= 0 || limit > 2000 {
		limit = 200
	}

	file, err := os.Open(path)
	if err != nil {
		return nil, 0, err
	}
	defer file.Close()

	// Keep a bounded tail while scanning, so a large history is read in one
	// pass without holding all of it in memory.
	matched := make([]Alert, 0, limit)
	total := 0
	malformed := 0
	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for scanner.Scan() {
		line := scanner.Bytes()
		if len(line) == 0 {
			continue
		}
		var alert Alert
		if err := json.Unmarshal(line, &alert); err != nil {
			malformed++
			continue
		}
		if symbol != "" && alert.Symbol != symbol {
			continue
		}
		if sinceMS > 0 && alert.TSMS < sinceMS {
			continue
		}
		total++
		matched = append(matched, alert)
		if len(matched) > limit {
			matched = matched[len(matched)-limit:]
		}
	}
	// Newest first for a feed reader.
	for i, j := 0, len(matched)-1; i < j; i, j = i+1, j-1 {
		matched[i], matched[j] = matched[j], matched[i]
	}
	if malformed > 0 {
		log.Printf("alert history: %d malformed line(s) skipped while reading %s", malformed, path)
	}
	return matched, total, nil
}

// HistoryForSymbol answers the "load this ticker's alerts NOW" path.
//
// It reads from memory first, because the user is waiting: an on-disk scan is
// O(file) and the file grows forever, so the fastest path is the one that does
// not touch it. The file is consulted only when memory cannot satisfy the
// request, which is the correct trade -- immediacy for the common case, and
// completeness when explicitly asked for.
//
// Newest first, because a ticker panel wants the recent past at the top.
func (b *AlertBuffer) HistoryForSymbol(symbol string, limit int) []Alert {
	if limit <= 0 {
		limit = 200
	}
	b.mu.RLock()
	history := b.bySymbol[symbol]
	if len(history) > limit {
		history = history[len(history)-limit:]
	}
	// Copy under the lock, reverse after, so the reverse cannot race a writer.
	out := make([]Alert, len(history))
	copy(out, history)
	b.mu.RUnlock()

	for i, j := 0, len(out)-1; i < j; i, j = i+1, j-1 {
		out[i], out[j] = out[j], out[i]
	}
	return out
}
