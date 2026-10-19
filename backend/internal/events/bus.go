// Package events is a minimal fan-out hub backing the Server-Sent Events
// stream that drives the dashboard.
//
// SSE rather than websockets on this side: the data flow is strictly
// server->client, and SSE comes free with net/http, survives proxies, and
// reconnects automatically in the browser.
package events

import (
	"encoding/json"
	"log"
	"sync"
	"sync/atomic"
)

// subscriberBuffer is how many messages may queue for one slow client before
// it starts missing them. The dashboard only needs the latest state, so
// dropping is preferable to blocking the producer.
const subscriberBuffer = 64

// Subscriber is one connected dashboard.
type Subscriber struct {
	ch     chan []byte
	hub    *Hub
	closed atomic.Bool
}

// C is the receive channel.
func (s *Subscriber) C() <-chan []byte { return s.ch }

// Close detaches the subscriber. Safe to call more than once.
func (s *Subscriber) Close() {
	if s.closed.CompareAndSwap(false, true) {
		s.hub.remove(s)
	}
}

// Hub broadcasts JSON messages to every subscriber.
type Hub struct {
	mu          sync.RWMutex
	subscribers map[*Subscriber]struct{}

	dropped atomic.Int64
	sent    atomic.Int64
}

// NewHub creates an empty hub.
func NewHub() *Hub {
	return &Hub{subscribers: make(map[*Subscriber]struct{})}
}

// Subscribe registers a new subscriber.
func (h *Hub) Subscribe() *Subscriber {
	sub := &Subscriber{ch: make(chan []byte, subscriberBuffer), hub: h}
	h.mu.Lock()
	h.subscribers[sub] = struct{}{}
	h.mu.Unlock()
	return sub
}

func (h *Hub) remove(sub *Subscriber) {
	h.mu.Lock()
	if _, ok := h.subscribers[sub]; ok {
		delete(h.subscribers, sub)
		close(sub.ch)
	}
	h.mu.Unlock()
}

// Clients returns the number of connected subscribers.
func (h *Hub) Clients() int {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return len(h.subscribers)
}

// Sent returns how many messages have been delivered.
func (h *Hub) Sent() int64 { return h.sent.Load() }

// Dropped returns how many messages were dropped for slow clients.
func (h *Hub) Dropped() int64 { return h.dropped.Load() }

// Broadcast marshals payload and queues it for every subscriber.
func (h *Hub) Broadcast(payload any) {
	data, err := json.Marshal(payload)
	if err != nil {
		log.Printf("events: marshal failed: %v", err)
		return
	}
	h.BroadcastRaw(data)
}

// BroadcastRaw queues an already-encoded message.
func (h *Hub) BroadcastRaw(data []byte) {
	h.mu.RLock()
	subs := make([]*Subscriber, 0, len(h.subscribers))
	for sub := range h.subscribers {
		subs = append(subs, sub)
	}
	h.mu.RUnlock()

	for _, sub := range subs {
		select {
		case sub.ch <- data:
			h.sent.Add(1)
		default:
			// Client is not keeping up. Drop this message rather than block
			// the scanner; the next tick carries fresher state anyway.
			h.dropped.Add(1)
		}
	}
}
