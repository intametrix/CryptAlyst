package main

import (
	"crypto/rand"
	"encoding/hex"
	"net"
	"strings"
	"sync"
	"time"
)

// cooldown suppresses repeat alerts for the same symbol and direction.
//
// A developing pump trips the flow gate on dozens of consecutive seconds; this
// collapses that into one alert per cooldown window.
type cooldown struct {
	mu     sync.Mutex
	last   map[string]time.Time
	window time.Duration
}

func newCooldown(seconds float64) *cooldown {
	if seconds < 0 {
		seconds = 0
	}
	return &cooldown{
		last:   make(map[string]time.Time),
		window: time.Duration(seconds * float64(time.Second)),
	}
}

func (c *cooldown) allow(symbol, kind string) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	at, ok := c.last[symbol+"|"+kind]
	if !ok {
		return true
	}
	return time.Since(at) >= c.window
}

func (c *cooldown) mark(symbol, kind string) {
	c.mu.Lock()
	c.last[symbol+"|"+kind] = time.Now()
	c.mu.Unlock()
}

// severityRank orders severities for the --min-severity filter.
func severityRank(severity string) int {
	switch strings.ToUpper(severity) {
	case "CRITICAL":
		return 2
	case "ALERT":
		return 1
	default:
		return 0
	}
}

// eventID returns a short random identifier for an alert.
func eventID() string {
	var buf [6]byte
	if _, err := rand.Read(buf[:]); err != nil {
		// A collision is harmless (the UI keys on it only for animation), so
		// fall back to a timestamp rather than failing the alert.
		return hex.EncodeToString([]byte(time.Now().Format("150405")))
	}
	return hex.EncodeToString(buf[:])
}

func splitHostPort(addr string) (string, string, error) {
	return net.SplitHostPort(addr)
}
