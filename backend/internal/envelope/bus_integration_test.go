package envelope

import (
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"strings"
	"testing"
	"time"
)

func stringsReader(s string) io.Reader { return strings.NewReader(s) }

// The bus test is an integration test: it needs the platform's broker. It skips
// rather than fails when the broker or its management API is absent, so `go test`
// stays runnable on a machine without RabbitMQ — but where the broker is up, this
// proves the packet actually left the process.

const (
	testStomp = "127.0.0.1:61613"
	testMgmt  = "http://127.0.0.1:15672"
)

func brokerUp(t *testing.T) {
	t.Helper()
	c, err := net.DialTimeout("tcp", testStomp, 500*time.Millisecond)
	if err != nil {
		t.Skip("broker not reachable on " + testStomp)
	}
	_ = c.Close()
	res := mgmt(t, http.MethodGet, "/api/overview", "")
	_ = res.Body.Close()
	if res.StatusCode != http.StatusOK {
		t.Skipf("management API answered %d on %s", res.StatusCode, testMgmt)
	}
}

func mgmt(t *testing.T, method, path string, body string) *http.Response {
	t.Helper()
	req, err := http.NewRequest(method, testMgmt+path, stringsReader(body))
	if err != nil {
		t.Fatal(err)
	}
	req.SetBasicAuth("guest", "guest")
	req.Header.Set("content-type", "application/json")
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	return res
}

func TestPacketReachesTheBroker(t *testing.T) {
	brokerUp(t)
	queue := fmt.Sprintf("test-envelope-%d", time.Now().UnixNano())
	bound := mgmt(t, http.MethodPut, "/api/queues/%2F/"+queue, `{"durable":true}`)
	_ = bound.Body.Close()
	if bound.StatusCode >= 300 {
		t.Fatalf("declare queue: %d", bound.StatusCode)
	}
	bind := mgmt(t, http.MethodPost, "/api/bindings/%2F/e/intel/q/"+queue, `{"routing_key":"intel.forecast","arguments":{}}`)
	_ = bind.Body.Close()
	if bind.StatusCode >= 300 {
		t.Fatalf("bind queue: %d", bind.StatusCode)
	}

	bus := NewBus(testStomp, "cryptalyst", "cryptalyst")
	if err := bus.Connect(); err != nil {
		t.Fatalf("connect: %v", err)
	}
	p, _, err := Run(Request{Topic: "intel.forecast", Key: "TESTUSDT", Producer: "forecast"},
		time.Now(), func() (any, error) { return map[string]any{"probe": queue}, nil })
	if err != nil {
		t.Fatal(err)
	}
	if err := bus.Publish(p); err != nil {
		t.Fatalf("publish: %v", err)
	}
	_ = bus.Close()

	// The broker is asynchronous; give it a moment, then read the queue.
	deadline := time.Now().Add(4 * time.Second)
	for time.Now().Before(deadline) {
		res := mgmt(t, http.MethodGet, "/api/queues/%2F/"+queue, "")
		var body struct {
			Messages int `json:"messages"`
			Stats    struct {
				Publish int `json:"publish"`
			} `json:"message_stats"`
		}
		_ = json.NewDecoder(res.Body).Decode(&body)
		_ = res.Body.Close()
		if body.Messages > 0 || body.Stats.Publish > 0 {
			return
		}
		time.Sleep(200 * time.Millisecond)
	}
	t.Fatal("the packet did not reach the broker")
}
