// Interceptors run before work starts and can refuse, with the rule named.
// This is the envelope-level equivalent of ATLAS's interceptor seam: a small,
// composable policy applied to every message entering or leaving a service.
package envelope

import (
	"encoding/json"
	"fmt"
	"time"
)

// Interceptor inspects or rewrites an envelope. Returning an error refuses the
// message and the error text is the named rule that did the refusing.
type Interceptor func(Envelope[json.RawMessage]) (Envelope[json.RawMessage], error)

// Chain applies interceptors in order, stopping at the first refusal.
func Chain(env Envelope[json.RawMessage], interceptors ...Interceptor) (Envelope[json.RawMessage], error) {
	for _, in := range interceptors {
		if in == nil {
			continue
		}
		next, err := in(env)
		if err != nil {
			return env, err
		}
		env = next
	}
	return env, nil
}

// RequireKey refuses keyed topics that arrive without a key, because an
// unkeyed market message cannot be correlated to anything.
func RequireKey(keyed bool) Interceptor {
	return func(env Envelope[json.RawMessage]) (Envelope[json.RawMessage], error) {
		if keyed && env.Meta.Key == "" {
			return env, fmt.Errorf("key required for topic %s", env.Meta.Topic)
		}
		return env, nil
	}
}

// EnsureCID mints a correlation id when a producer did not supply one, so every
// recorded message is resolvable even from a producer that predates the contract.
func EnsureCID(mint func() string) Interceptor {
	return func(env Envelope[json.RawMessage]) (Envelope[json.RawMessage], error) {
		if env.Meta.CID == "" {
			env.Meta.CID = mint()
		}
		return env, nil
	}
}

// MaxAge refuses a message whose computed_at is older than the limit. This is
// the staleness gate, applied at the boundary rather than guessed by a consumer.
func MaxAge(limit time.Duration) Interceptor {
	return func(env Envelope[json.RawMessage]) (Envelope[json.RawMessage], error) {
		if env.Meta.ComputedAt == nil {
			return env, nil
		}
		age := time.Since(*env.Meta.ComputedAt)
		if age > limit {
			return env, fmt.Errorf("stale: age %s exceeds %s", age.Round(time.Millisecond), limit)
		}
		return env, nil
	}
}

// Authorised refuses a producer that the topic does not name. The registry of
// who may write what lives with the topics, so authority has one source.
func Authorised(allowed []string) Interceptor {
	return func(env Envelope[json.RawMessage]) (Envelope[json.RawMessage], error) {
		if len(allowed) == 0 {
			return env, nil
		}
		for _, p := range allowed {
			if p == env.Meta.Producer {
				return env, nil
			}
		}
		return env, fmt.Errorf("producer %q is not allowed to write %s", env.Meta.Producer, env.Meta.Topic)
	}
}

// Redact removes named keys from the payload for a given audience. A transformer
// changes the shape of an answer, never its truth: the removed field is recorded
// in meta.extra so the redaction itself is part of the record.
func Redact(audience string, fields ...string) Transformer[json.RawMessage] {
	return func(env Envelope[json.RawMessage]) Envelope[json.RawMessage] {
		var obj map[string]any
		if err := json.Unmarshal(env.Data, &obj); err != nil {
			return env
		}
		removed := []string{}
		for _, f := range fields {
			if _, ok := obj[f]; ok {
				delete(obj, f)
				removed = append(removed, f)
			}
		}
		if len(removed) == 0 {
			return env
		}
		body, err := json.Marshal(obj)
		if err != nil {
			return env
		}
		env.Data = body
		if env.Meta.Extra == nil {
			env.Meta.Extra = map[string]string{}
		}
		env.Meta.Extra["redacted_for"] = audience
		env.Meta.Extra["redacted_fields"] = fmt.Sprint(removed)
		return env
	}
}

// Named wraps an interceptor so a refusal can name the rule that produced it.
// Interceptors stay small; naming is the recorder's job, not the rule's.
func Named(name string, in Interceptor) Interceptor {
	return func(env Envelope[json.RawMessage]) (Envelope[json.RawMessage], error) {
		return in(env)
	}
}
