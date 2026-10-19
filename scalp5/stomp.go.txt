// stomp publisher: the Go services talk to the same broker as the Python ones.
package main

import (
	"bufio"
	"fmt"
	"net"
	"strings"
	"sync"
	"time"
)

var (
	stompMu   sync.Mutex
	stompConn net.Conn
	stompAddr = "127.0.0.1:61613"
)

func stompEnsure() (net.Conn, error) {
	if stompConn != nil {
		return stompConn, nil
	}
	c, err := net.DialTimeout("tcp", stompAddr, 3*time.Second)
	if err != nil {
		return nil, err
	}
	frame := "CONNECT\naccept-version:1.2\nhost:/\nlogin:cryptalyst\npasscode:cryptalyst\n\n\x00"
	if _, err := c.Write([]byte(frame)); err != nil {
		c.Close()
		return nil, err
	}
	c.SetReadDeadline(time.Now().Add(3 * time.Second))
	rd := bufio.NewReader(c)
	for {
		line, err := rd.ReadString('\n')
		if err != nil {
			c.Close()
			return nil, err
		}
		if strings.HasPrefix(line, "CONNECTED") {
			break
		}
	}
	c.SetReadDeadline(time.Time{})
	stompConn = c
	return c, nil
}

// publish sends one message to an exchange + routing key. Failure is silent by
// design: the trading path must never block on telemetry.
func publish(exchange, topic string, body []byte) {
	stompMu.Lock()
	defer stompMu.Unlock()
	c, err := stompEnsure()
	if err != nil {
		return
	}
	dest := "/exchange/" + exchange + "/" + topic
	msg := fmt.Sprintf("SEND\ndestination:%s\ncontent-type:application/json\ncontent-length:%d\n\n",
		dest, len(body))
	if _, err := c.Write([]byte(msg)); err != nil {
		c.Close()
		stompConn = nil
		return
	}
	if _, err := c.Write(body); err != nil {
		c.Close()
		stompConn = nil
		return
	}
	if _, err := c.Write([]byte("\x00")); err != nil {
		c.Close()
		stompConn = nil
	}
}
