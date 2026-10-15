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
