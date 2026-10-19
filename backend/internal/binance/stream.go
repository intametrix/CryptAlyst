package binance

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"math/rand"
	"sort"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

// MaxStreamsPerConnection is Binance's documented limit. We stay clear of the
// edge because a rejected connection is harder to debug than a truncated list.
const MaxStreamsPerConnection = 1000

// TickerStream builds the per-symbol 24h ticker stream name.
func TickerStream(symbol string) string {
	return lower(symbol) + "@ticker"
}

// AggTradeStream builds the per-symbol aggregated-trade stream name.
func AggTradeStream(symbol string) string {
	return lower(symbol) + "@aggTrade"
}

func lower(s string) string {
	out := []byte(s)
	for i := range out {
		if out[i] >= 'A' && out[i] <= 'Z' {
			out[i] += 'a' - 'A'
		}
	}
	return string(out)
}

// MessageHandler receives one decoded payload from a combined stream, along
// with the stream name it arrived on.
type MessageHandler func(stream string, data json.RawMessage)

// Stream is a combined-stream websocket client with dynamic subscription.
//
// One connection carries every stream, which is what makes watching several
// hundred symbols practical. Subscriptions can be changed on a live socket, so
// a symbol that trips the cheap gates can be escalated to trade-level data
// without reconnecting.
type Stream struct {
	base    string
	handle  MessageHandler
	onState func(connected bool, streams int)

	mu       sync.Mutex
	desired  map[string]struct{}
	active   map[string]struct{}
	pending  map[string]struct{}
	dropping map[string]struct{}

	conn    *websocket.Conn
	sendMu  sync.Mutex
	writeMu sync.Mutex

	connected atomicBool
	messages  atomicInt64
	reconnect atomicInt64
	lastMsg   atomicInt64
}

// NewStream creates a combined-stream client. `base` is a wss:// host.
func NewStream(base string, handle MessageHandler, onState func(bool, int)) *Stream {
	return &Stream{
		base:     base,
		handle:   handle,
		onState:  onState,
		desired:  make(map[string]struct{}),
		active:   make(map[string]struct{}),
		pending:  make(map[string]struct{}),
		dropping: make(map[string]struct{}),
	}
}

// IsConnected reports whether the socket is currently up.
func (s *Stream) IsConnected() bool { return s.connected.Load() }

// Messages returns the number of payloads received.
func (s *Stream) Messages() int64 { return s.messages.Load() }

// Reconnects returns the number of reconnection attempts made.
func (s *Stream) Reconnects() int64 { return s.reconnect.Load() }

// Streams returns the number of active subscriptions.
func (s *Stream) Streams() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.active)
}

// SetStreams replaces the entire desired subscription set.
func (s *Stream) SetStreams(streams []string) {
	wanted := make(map[string]struct{}, len(streams))
	for _, name := range streams {
		wanted[name] = struct{}{}
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(wanted) > MaxStreamsPerConnection {
		trimmed := make([]string, 0, len(wanted))
		for name := range wanted {
			trimmed = append(trimmed, name)
		}
		sort.Strings(trimmed)
		wanted = make(map[string]struct{}, MaxStreamsPerConnection)
		for _, name := range trimmed[:MaxStreamsPerConnection] {
			wanted[name] = struct{}{}
		}
	}
	s.desired = wanted
	s.pending = make(map[string]struct{})
	s.dropping = make(map[string]struct{})
	for name := range wanted {
		if _, ok := s.active[name]; !ok {
			s.pending[name] = struct{}{}
		}
	}
	for name := range s.active {
		if _, ok := wanted[name]; !ok {
			s.dropping[name] = struct{}{}
		}
	}
}

// Subscribe adds streams to the desired set.
func (s *Stream) Subscribe(streams ...string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, name := range streams {
		s.desired[name] = struct{}{}
		if _, ok := s.active[name]; !ok {
			s.pending[name] = struct{}{}
		}
	}
}

// Unsubscribe removes streams from the desired set.
func (s *Stream) Unsubscribe(streams ...string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, name := range streams {
		delete(s.desired, name)
		if _, ok := s.active[name]; ok {
			s.dropping[name] = struct{}{}
		}
	}
}

// Run keeps the socket connected until the context is cancelled.
func (s *Stream) Run(ctx context.Context) {
	backoff := time.Second
	for ctx.Err() == nil {
		err := s.session(ctx)
		if ctx.Err() != nil {
			return
		}
		s.connected.Store(false)
		if s.onState != nil {
			s.onState(false, 0)
		}
		s.reconnect.Add(1)
		if err != nil {
			delay := backoff
			if delay > 30*time.Second {
				delay = 30 * time.Second
			}
			// Jitter so a restarted fleet does not reconnect in lockstep.
			delay = time.Duration(float64(delay) * (0.7 + rand.Float64()*0.6))
			log.Printf("stream: disconnected (%v); retrying in %s", err, delay.Round(time.Millisecond))
			select {
			case <-ctx.Done():
				return
			case <-time.After(delay):
			}
			if backoff < 30*time.Second {
				backoff *= 2
			}
		}
	}
}

func (s *Stream) session(ctx context.Context) error {
	url := s.buildURL()
	dialer := websocket.Dialer{
		HandshakeTimeout: 20 * time.Second,
		ReadBufferSize:   1 << 20,
		WriteBufferSize:  1 << 16,
	}
	conn, _, err := dialer.DialContext(ctx, url, nil)
	if err != nil {
		return err
	}
	defer conn.Close()

	s.writeMu.Lock()
	s.conn = conn
	s.writeMu.Unlock()

	// A fresh socket starts with exactly the streams in the URL; reset our
	// view of what is active to match, then queue the difference.
	s.mu.Lock()
	s.active = make(map[string]struct{}, len(s.desired))
	for name := range s.desired {
		s.active[name] = struct{}{}
	}
	s.pending = make(map[string]struct{})
	s.dropping = make(map[string]struct{})
	activeCount := len(s.active)
	s.mu.Unlock()

	s.connected.Store(true)
	s.lastMsg.Store(time.Now().UnixMilli())
	if s.onState != nil {
		s.onState(true, activeCount)
	}
	log.Printf("stream: connected with %d streams", activeCount)

	// Binance closes idle sockets after 24h; pings keep it healthy.
	conn.SetReadDeadline(time.Now().Add(90 * time.Second))
	conn.SetPongHandler(func(string) error {
		return conn.SetReadDeadline(time.Now().Add(90 * time.Second))
	})

	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()
	ping := time.NewTicker(20 * time.Second)
	defer ping.Stop()

	readErr := make(chan error, 1)
	go func() {
		for {
			_, payload, err := conn.ReadMessage()
			if err != nil {
				readErr <- err
				return
			}
			s.messages.Add(1)
			s.lastMsg.Store(time.Now().UnixMilli())
			s.dispatch(payload)
		}
	}()

	for {
		select {
		case <-ctx.Done():
			return nil
		case err := <-readErr:
			return err
		case <-ping.C:
			if err := conn.WriteControl(websocket.PingMessage, nil, time.Now().Add(5*time.Second)); err != nil {
				return err
			}
		case <-ticker.C:
			if err := s.flushSubscriptions(); err != nil {
				return err
			}
			// Watchdog: a connected-but-silent socket is worse than a closed
			// one, because nothing else will notice.
			idle := time.Since(time.UnixMilli(s.lastMsg.Load()))
			if idle > 60*time.Second {
				return fmt.Errorf("stream idle for %s", idle.Round(time.Second))
			}
		}
	}
}

func (s *Stream) buildURL() string {
	s.mu.Lock()
	names := make([]string, 0, len(s.desired))
	for name := range s.desired {
		names = append(names, name)
	}
	s.mu.Unlock()
	sort.Strings(names)
	if len(names) > MaxStreamsPerConnection {
		names = names[:MaxStreamsPerConnection]
	}
	if len(names) == 0 {
		names = []string{"btcusdt@ticker"}
	}
	joined := names[0]
	for _, name := range names[1:] {
		joined += "/" + name
	}
	return s.base + "/stream?streams=" + joined
}

func (s *Stream) flushSubscriptions() error {
	s.mu.Lock()
	toAdd := make([]string, 0, len(s.pending))
	for name := range s.pending {
		toAdd = append(toAdd, name)
	}
	toDrop := make([]string, 0, len(s.dropping))
	for name := range s.dropping {
		toDrop = append(toDrop, name)
	}
	s.mu.Unlock()
	if len(toAdd) == 0 && len(toDrop) == 0 {
		return nil
	}
	sort.Strings(toAdd)
	sort.Strings(toDrop)

	s.writeMu.Lock()
	conn := s.conn
	s.writeMu.Unlock()
	if conn == nil {
		return nil
	}

	if len(toAdd) > 0 {
		if err := s.write(conn, "SUBSCRIBE", toAdd); err != nil {
			return err
		}
		s.mu.Lock()
		for _, name := range toAdd {
			s.active[name] = struct{}{}
			delete(s.pending, name)
		}
		s.mu.Unlock()
	}
	if len(toDrop) > 0 {
		if err := s.write(conn, "UNSUBSCRIBE", toDrop); err != nil {
			return err
		}
		s.mu.Lock()
		for _, name := range toDrop {
			delete(s.active, name)
			delete(s.dropping, name)
		}
		s.mu.Unlock()
	}
	return nil
}

func (s *Stream) write(conn *websocket.Conn, method string, params []string) error {
	s.sendMu.Lock()
	defer s.sendMu.Unlock()
	payload, err := json.Marshal(map[string]any{
		"method": method,
		"params": params,
		"id":     1,
	})
	if err != nil {
		return err
	}
	return conn.WriteMessage(websocket.TextMessage, payload)
}

func (s *Stream) dispatch(payload []byte) {
	// Combined frames are {"stream":"...","data":{...}}.
	var envelope struct {
		Stream string          `json:"stream"`
		Data   json.RawMessage `json:"data"`
	}
	if err := json.Unmarshal(payload, &envelope); err != nil {
		return
	}
	if len(envelope.Data) == 0 {
		return
	}
	s.handle(envelope.Stream, envelope.Data)
}

// --------------------------------------------------------------------------
// Tiny atomics (avoid pulling in sync/atomic generics for two counters)
// --------------------------------------------------------------------------

type atomicBool struct {
	mu sync.Mutex
	v  bool
}

func (a *atomicBool) Load() bool {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.v
}

func (a *atomicBool) Store(v bool) {
	a.mu.Lock()
	a.v = v
	a.mu.Unlock()
}

type atomicInt64 struct {
	mu sync.Mutex
	v  int64
}

func (a *atomicInt64) Load() int64 {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.v
}

func (a *atomicInt64) Store(v int64) {
	a.mu.Lock()
	a.v = v
	a.mu.Unlock()
}

func (a *atomicInt64) Add(delta int64) {
	a.mu.Lock()
	a.v += delta
	a.mu.Unlock()
}
