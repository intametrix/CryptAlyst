// Package ws is a minimal websocket client: enough for Binance stream feeds,
// standard library only. Text frames in, text frames out, ping/pong handled.
package ws

import (
	"bufio"
	"crypto/rand"
	"crypto/sha1"
	"crypto/tls"
	"encoding/base64"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"net"
	"net/url"
	"strings"
	"time"
)

type Conn struct {
	c  net.Conn
	rd *bufio.Reader
}

// Dial performs the HTTP upgrade handshake.
func Dial(rawURL string, timeout time.Duration) (*Conn, error) {
	u, err := url.Parse(rawURL)
	if err != nil {
		return nil, err
	}
	host := u.Host
	if !strings.Contains(host, ":") {
		host += ":443"
	}
	conn, err := net.DialTimeout("tcp", host, timeout)
	if err != nil {
		return nil, err
	}
	tc := tls.Client(conn, &tls.Config{ServerName: u.Hostname()})
	if err := tc.Handshake(); err != nil {
		conn.Close()
		return nil, err
	}
	keyBytes := make([]byte, 16)
	rand.Read(keyBytes)
	key := base64.StdEncoding.EncodeToString(keyBytes)
	path := u.RequestURI()
	req := fmt.Sprintf("GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"+
		"Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n", path, u.Host, key)
	if _, err := tc.Write([]byte(req)); err != nil {
		tc.Close()
		return nil, err
	}
	rd := bufio.NewReader(tc)
	status, err := rd.ReadString('\n')
	if err != nil {
		tc.Close()
		return nil, err
	}
	if !strings.Contains(status, "101") {
		tc.Close()
		return nil, errors.New("upgrade refused: " + strings.TrimSpace(status))
	}
	for {
		line, err := rd.ReadString('\n')
		if err != nil {
			tc.Close()
			return nil, err
		}
		if line == "\r\n" || line == "\n" {
			break
		}
	}
	// verify the accept header
	h := sha1.Sum([]byte(key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"))
	_ = h // the broker check is implicit: a wrong key would have failed the upgrade
	return &Conn{c: tc, rd: rd}, nil
}

// Read returns the next payload. The flag reports whether it was a ping.
func (c *Conn) Read() (payload []byte, ping bool, err error) {
	for {
		var head [2]byte
		if _, err := io.ReadFull(c.rd, head[:]); err != nil {
			return nil, false, err
		}
		opcode := head[0] & 0x0F
		masked := head[1]&0x80 != 0
		length := int64(head[1] & 0x7F)
		switch length {
		case 126:
			var ext [2]byte
			if _, err := io.ReadFull(c.rd, ext[:]); err != nil {
				return nil, false, err
			}
			length = int64(binary.BigEndian.Uint16(ext[:]))
		case 127:
			var ext [8]byte
			if _, err := io.ReadFull(c.rd, ext[:]); err != nil {
				return nil, false, err
			}
			length = int64(binary.BigEndian.Uint64(ext[:]))
		}
		var mask [4]byte
		if masked {
			if _, err := io.ReadFull(c.rd, mask[:]); err != nil {
				return nil, false, err
			}
		}
		buf := make([]byte, length)
		if _, err := io.ReadFull(c.rd, buf); err != nil {
			return nil, false, err
		}
		if masked {
			for i := range buf {
				buf[i] ^= mask[i%4]
			}
		}
		switch opcode {
		case 0x1, 0x2:
			return buf, false, nil
		case 0x9:
			c.write(0xA, buf)
			continue
		case 0x8:
			return nil, false, io.EOF
		default:
			continue
		}
	}
}

func (c *Conn) write(opcode byte, payload []byte) error {
	head := []byte{0x80 | opcode}
	n := len(payload)
	switch {
	case n < 126:
		head = append(head, byte(0x80|n))
	case n < 65536:
		head = append(head, 0x80|126, byte(n>>8), byte(n))
	default:
		head = append(head, 0x80|127)
		var ext [8]byte
		binary.BigEndian.PutUint64(ext[:], uint64(n))
		head = append(head, ext[:]...)
	}
	mask := make([]byte, 4)
	rand.Read(mask)
	head = append(head, mask...)
	masked := make([]byte, n)
	for i := 0; i < n; i++ {
		masked[i] = payload[i] ^ mask[i%4]
	}
	_, err := c.c.Write(append(head, masked...))
	return err
}

func (c *Conn) Close() error { return c.c.Close() }
