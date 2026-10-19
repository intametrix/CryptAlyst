// Package busadmin declares the queue topology a service consumes from.
//
// STOMP can subscribe to a queue but cannot declare a binding, so a service that
// only subscribes silently receives nothing when the binding is missing. That is
// exactly how model.decision was being dropped: the queue existed, the exchange
// existed, and nothing joined them.
package busadmin

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"time"
)

const base = "http://127.0.0.1:15672/api"

// Ensure binds queue q.<topic> to exchange/<routing key>. It is idempotent.
func Ensure(queue, topic, exchange string) error {
	client := http.Client{Timeout: 4 * time.Second}

	// declare the queue (durable, no auto-delete: the recorder must not lose it)
	body, _ := json.Marshal(map[string]any{"durable": true, "auto_delete": false,
		"arguments": map[string]any{"x-queue-type": "classic"}})
	// PUT declares idempotently; POST returns 405 when the queue already exists
	if err := send(client, http.MethodPut, base+"/queues/%2F/"+queue, body); err != nil {
		return fmt.Errorf("declare %s: %w", queue, err)
	}
	// bind it to the topic exchange with the topic name as the routing key
	bind, _ := json.Marshal(map[string]any{"routing_key": topic, "arguments": map[string]any{}})
	if err := send(client, http.MethodPost, base+"/bindings/%2F/e/"+exchange+"/q/"+queue, bind); err != nil {
		return fmt.Errorf("bind %s -> %s: %w", queue, exchange, err)
	}
	return nil
}

func send(c http.Client, method, url string, body []byte) error {
	req, err := http.NewRequest(method, url, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.SetBasicAuth("cryptalyst", "cryptalyst")
	resp, err := c.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 && resp.StatusCode != 404 && resp.StatusCode != 405 {
		return fmt.Errorf("http %d", resp.StatusCode)
	}
	return nil
}
