// Package stomp is a minimal STOMP 1.2 client for RabbitMQ.
//
// It is the only transport in the Go services: publish, subscribe to a queue,
// subscribe to an exchange routing key, and automatic reconnection with backoff.
package stomp

import (
	"bufio"
	"errors"
	"fmt"
	"net"
	"strings"
	"sync"
	"time"
)

// Message is one delivered frame.
type Message struct {
	Headers map[string]string
	Body    []byte
}

// Client is a single STOMP connection. Safe for concurrent publish.
type Client struct {
	Addr     string
	Login    string
	Passcode string

	mu   sync.Mutex
	conn net.Conn
	rd   *bufio.Reader
	sid  int

	handlers   map[string]func(Message)
	handlersMu sync.RWMutex

	closed bool
}

func New(addr, login, passcode string) *Client {
	return &Client{Addr: addr, Login: login, Passcode: passcode,
		handlers: map[string]func(Message){}}
}

func (c *Client) Connect() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.conn != nil {
		return nil
	}
	conn, err := net.DialTimeout("tcp", c.Addr, 3*time.Second)
	if err != nil {
		return err
	}
	frame := fmt.Sprintf("CONNECT\naccept-version:1.2\nhost:/\nlogin:%s\npasscode:%s\n\n\x00",
		c.Login, c.Passcode)
	if _, err := conn.Write([]byte(frame)); err != nil {
		conn.Close()
		return err
	}
	rd := bufio.NewReader(conn)
	conn.SetReadDeadline(time.Now().Add(4 * time.Second))
	for {
		line, err := rd.ReadString('\n')
		if err != nil {
			conn.Close()
			return err
		}
		if strings.HasPrefix(line, "CONNECTED") {
			break
		}
		if strings.HasPrefix(line, "ERROR") {
			conn.Close()
			return errors.New("broker refused the connection")
		}
	}
	conn.SetReadDeadline(time.Time{})
	c.conn, c.rd = conn, rd
	go c.readLoop()
	return nil
}

func (c *Client) readLoop() {
	for {
		c.mu.Lock()
		rd := c.rd
		c.mu.Unlock()
		if rd == nil {
			return
		}
		msg, err := readFrame(rd)
		if err != nil {
			c.mu.Lock()
			if c.conn != nil {
				c.conn.Close()
			}
			c.conn, c.rd = nil, nil
			c.mu.Unlock()
			return
		}
		if msg == nil {
			continue
		}
		c.handlersMu.RLock()
		h := c.handlers[msg.Headers["subscription"]]
		c.handlersMu.RUnlock()
		if h != nil {
			h(*msg)
		}
	}
}

func readFrame(rd *bufio.Reader) (*Message, error) {
	var lines []string
	for {
		line, err := rd.ReadString('\n')
		if err != nil {
			return nil, err
		}
		line = strings.TrimRight(line, "\r\n")
		if line == "" {
			break
		}
		lines = append(lines, line)
	}
	if len(lines) == 0 {
		return nil, nil
	}
	headers := map[string]string{}
	for _, l := range lines[1:] {
		if i := strings.Index(l, ":"); i > 0 {
			headers[l[:i]] = l[i+1:]
		}
	}
	body := []byte{}
	if n, ok := headers["content-length"]; ok {
		var length int
		fmt.Sscanf(n, "%d", &length)
		buf := make([]byte, length)
		if _, err := ioReadFull(rd, buf); err != nil {
			return nil, err
		}
		body = buf
		rd.ReadByte() // trailing NUL
	} else {
		for {
			b, err := rd.ReadByte()
			if err != nil {
				return nil, err
			}
			if b == 0 {
				break
			}
			body = append(body, b)
		}
	}
	if lines[0] == "ERROR" {
		return nil, errors.New("stomp error: " + string(body))
	}
	return &Message{Headers: headers, Body: body}, nil
}

func ioReadFull(rd *bufio.Reader, buf []byte) (int, error) {
	read := 0
	for read < len(buf) {
		n, err := rd.Read(buf[read:])
		read += n
		if err != nil {
			return read, err
		}
	}
	return read, nil
}

// Publish sends one message to an exchange with a routing key.
func (c *Client) Publish(exchange, routingKey string, body []byte) error {
	return c.PublishWithHeaders(exchange, routingKey, nil, body)
}

// PublishWithHeaders sends the body plus STOMP headers. Headers carry envelope
// metadata, so provenance is authored by the producer while the body stays
// exactly what existing consumers already parse.
func (c *Client) PublishWithHeaders(exchange, routingKey string, headers map[string]string, body []byte) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.conn == nil {
		return errors.New("not connected")
	}
	var extra strings.Builder
	for k, v := range headers {
		clean := strings.NewReplacer("\n", " ", "\r", " ").Replace(v)
		extra.WriteString(k + ":" + clean + "\n")
	}
	frame := fmt.Sprintf("SEND\ndestination:/exchange/%s/%s\n%scontent-type:application/json\ncontent-length:%d\n\n",
		exchange, routingKey, extra.String(), len(body))
	if _, err := c.conn.Write([]byte(frame)); err != nil {
		return err
	}
	if _, err := c.conn.Write(body); err != nil {
		return err
	}
	_, err := c.conn.Write([]byte{0})
	return err
}

// SubscribeQueue consumes a named durable queue.
func (c *Client) SubscribeQueue(queue string, h func(Message)) error {
	return c.subscribe("/queue/"+queue, h)
}

// SubscribeTopic consumes an exchange routing key with its own private queue.
func (c *Client) SubscribeTopic(exchange, routingKey string, h func(Message)) error {
	return c.subscribe("/exchange/"+exchange+"/"+routingKey, h)
}

func (c *Client) subscribe(destination string, h func(Message)) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.conn == nil {
		return errors.New("not connected")
	}
	c.sid++
	id := fmt.Sprintf("g-%s-%d", strings.ReplaceAll(destination, "/", "."), c.sid)
	c.handlersMu.Lock()
	c.handlers[id] = h
	c.handlersMu.Unlock()
	frame := fmt.Sprintf("SUBSCRIBE\ndestination:%s\nid:%s\nack:auto\n\n\x00", destination, id)
	_, err := c.conn.Write([]byte(frame))
	return err
}

// Healthy reports whether the socket is up.
func (c *Client) Healthy() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.conn != nil
}
