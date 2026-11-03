package api

import (
	"errors"

	"pumpdump/backend/internal/envelope"
)

var errTestExchangeDown = errors.New("mock exchange: 451 unavailable")

// envelopeCounts reads the test journal.
func envelopeCounts() (map[string]int, error) {
	return envelope.Count(envelopeJournalPath())
}
