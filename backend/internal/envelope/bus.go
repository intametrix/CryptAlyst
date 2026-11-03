package envelope

// The bus publisher: RabbitMQ over STOMP.
//
// The platform's broker is RabbitMQ with the STOMP plugin on :61613 — the same
// bus the core services already publish candles and depths on. Envelopes go to one
// exchange, `cryptalyst.envelope`, with the topic as the routing key, so a consumer
// binds to the topics it cares about and never learns who produced them.
//
// The publisher is deliberately best-effort: if the broker is down the packet is
// still recorded to the journal and the request still answers. A bus outage must
// not stop a forecast from being computed; it stops it from being broadcast.

import (
	"bufio"
	"errors"
	"fmt"
	"net"
	"strings"
	"sync"
	"time"
)

// The exchange is the plane, exactly as the architecture registry defines it: the
// broker already carries `market`, `intel`, `model`, `risk`, `system`, `alert`,
// `exec` and `review`, and a topic's plane decides which one carries it.
//
// Routing key is the topic, so a consumer binds `intel.forecast` and receives only
// those packets without knowing which producer wrote them.

// Bus is a minimal STOMP client: connect, publish, close.
type Bus struct {
	Addr     string
	Login    string
	Passcode string

	mu   sync.Mutex
	conn net.Conn
	rd   *bufio.Reader
}

// NewBus returns a publisher for a broker address (host:port).
func NewBus(addr, login, passcode string) *Bus {
	return &Bus{Addr: addr, Login: login, Passcode: passcode}
}

// Connect dials the broker and performs the STOMP handshake.
func (b *Bus) Connect() error {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.conn != nil {
		return nil
	}
	conn, err := net.DialTimeout("tcp", b.Addr, 3*time.Second)
	if err != nil {
		return err
	}
	frame := fmt.Sprintf("CONNECT\naccept-version:1.2\nhost:/\nlogin:%s\npasscode:%s\nheart-beat:0,0\n\n\x00",
		b.Login, b.Passcode)
	if _, err := conn.Write([]byte(frame)); err != nil {
		_ = conn.Close()
		return err
	}
	rd := bufio.NewReader(conn)
	_ = conn.SetReadDeadline(time.Now().Add(4 * time.Second))
	for {
		line, err := rd.ReadString('\n')
		if err != nil {
			_ = conn.Close()
			return err
		}
		if strings.HasPrefix(line, "CONNECTED") {
			break
		}
		if strings.HasPrefix(line, "ERROR") {
			_ = conn.Close()
			return errors.New("broker refused the connection")
		}
	}
	_ = conn.SetReadDeadline(time.Time{})
	b.conn, b.rd = conn, rd
	return nil
}

// Close disconnects.
func (b *Bus) Close() error {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.conn == nil {
		return nil
	}
	_, _ = b.conn.Write([]byte("DISCONNECT\n\n\x00"))
	err := b.conn.Close()
	b.conn, b.rd = nil, nil
	return err
}

// Publish sends one packet. The body is the envelope JSON; provenance also travels
// as STOMP headers so a consumer can filter without parsing the body.
func (b *Bus) Publish(p Packet) error {
	body := mustJSON(p)
	return b.publish(string(p.Meta.Plane), p.Meta.Topic, body, map[string]string{
		"topic":       p.Meta.Topic,
		"plane":       string(p.Meta.Plane),
		"key":         p.Meta.Key,
		"producer":    p.Meta.Producer,
		"cid":         p.Meta.CID,
		"generation":  fmt.Sprintf("%d", p.Meta.Generation),
		"computed_at": fmt.Sprintf("%d", p.Meta.ComputedAt),
	})
}

func (b *Bus) publish(exchange, routingKey string, body []byte, headers map[string]string) error {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.conn == nil {
		if err := b.connectLocked(); err != nil {
			return err
		}
	}
	var extra strings.Builder
	for k, v := range headers {
		clean := strings.NewReplacer("\n", " ", "\r", " ").Replace(v)
		extra.WriteString(k + ":" + clean + "\n")
	}
	frame := fmt.Sprintf("SEND\ndestination:/exchange/%s/%s\n%scontent-type:application/json\ncontent-length:%d\n\n",
		exchange, routingKey, extra.String(), len(body))
	if _, err := b.conn.Write([]byte(frame)); err != nil {
		b.dropLocked()
		return err
	}
	if _, err := b.conn.Write(body); err != nil {
		b.dropLocked()
		return err
	}
	if _, err := b.conn.Write([]byte{0}); err != nil {
		b.dropLocked()
		return err
	}
	return nil
}

// connectLocked dials while the caller holds the mutex.
func (b *Bus) connectLocked() error {
	conn, err := net.DialTimeout("tcp", b.Addr, 3*time.Second)
	if err != nil {
		return err
	}
	frame := fmt.Sprintf("CONNECT\naccept-version:1.2\nhost:/\nlogin:%s\npasscode:%s\nheart-beat:0,0\n\n\x00",
		b.Login, b.Passcode)
	if _, err := conn.Write([]byte(frame)); err != nil {
		_ = conn.Close()
		return err
	}
	rd := bufio.NewReader(conn)
	_ = conn.SetReadDeadline(time.Now().Add(4 * time.Second))
	for {
		line, err := rd.ReadString('\n')
		if err != nil {
			_ = conn.Close()
			return err
		}
		if strings.HasPrefix(line, "CONNECTED") {
			break
		}
		if strings.HasPrefix(line, "ERROR") {
			_ = conn.Close()
			return errors.New("broker refused the connection")
		}
	}
	_ = conn.SetReadDeadline(time.Time{})
	b.conn, b.rd = conn, rd
	return nil
}

func (b *Bus) dropLocked() {
	if b.conn != nil {
		_ = b.conn.Close()
	}
	b.conn, b.rd = nil, nil
}
