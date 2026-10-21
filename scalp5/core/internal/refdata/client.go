// Client is the lake-side caller for a refdata service: the way a book, a
// recorder transformer, or any envelope consumer holding a bare symbol turns
// it into a canonical instrument at runtime. Answers are decoded with the
// envelope package, so callers already speaking the lake's shape parse
// refdata identically to everything else.
package refdata

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"cryptalyst/core/internal/envelope"
)

// Client talks to a cmd/refdata server. It is a value type; share one across
// goroutines freely.
type Client struct {
	base string
	hc   *http.Client
}

// NewClient targets a service base URL, e.g. "http://127.0.0.1:8790".
func NewClient(base string) *Client {
	return &Client{
		base: strings.TrimRight(base, "/"),
		hc:   &http.Client{Timeout: 5 * time.Second},
	}
}

// Health reports whether the service answers.
func (c *Client) Health(ctx context.Context) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.base+"/healthz", nil)
	if err != nil {
		return err
	}
	resp, err := c.hc.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("refdata: healthz status %d", resp.StatusCode)
	}
	return nil
}

// get envelopes one typed payload.
func (c *Client) get(ctx context.Context, path string, out any) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.base+path, nil)
	if err != nil {
		return err
	}
	resp, err := c.hc.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("refdata: GET %s status %d", path, resp.StatusCode)
	}
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 16<<20))
	if err != nil {
		return err
	}
	env, err := envelope.Decode[json.RawMessage](raw)
	if err != nil {
		return err
	}
	return json.Unmarshal(env.Data, out)
}

// Resolve maps an identifier to canonical instruments at runtime. An empty
// idType matches the value across all identifier types.
func (c *Client) Resolve(ctx context.Context, idType, value string) ([]Instrument, error) {
	q := url.Values{}
	if idType != "" {
		q.Set("type", idType)
	}
	q.Set("value", value)
	var out []Instrument
	if err := c.get(ctx, "/v1/resolve?"+q.Encode(), &out); err != nil {
		return nil, err
	}
	return out, nil
}

// Instrument fetches one canonical instrument by id.
func (c *Client) Instrument(ctx context.Context, id string) (*Instrument, error) {
	var out Instrument
	if err := c.get(ctx, "/v1/instruments/"+url.PathEscape(id), &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// Correlations returns every edge registered for an instrument — the
// cross-store handles that let the caller re-key its own record.
func (c *Client) Correlations(ctx context.Context, id string) ([]Edge, error) {
	var out []Edge
	if err := c.get(ctx, "/v1/instruments/"+url.PathEscape(id)+"/correlations", &out); err != nil {
		return nil, err
	}
	return out, nil
}
