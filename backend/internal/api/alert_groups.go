package api

import (
	"fmt"
	"net/http"
	"sort"
	"strings"
	"time"
)

// Alert groups: the retained history, ordered by time and bucketed into
// windows the caller defines.
//
// Why grouping rather than a flat list: a feed answers "what just happened",
// but it cannot answer "how busy was the last hour" or "did the 14:00 bucket
// look different from the 15:00 one". Those are the questions you ask when
// deciding whether a threshold is right, and they need counts per window, not
// another 200 rows.
//
// The bucket is caller-defined because the useful resolution depends on the
// question: minutes to watch a live burst, hours to see a session's shape, days
// to sanity-check a week. Hardcoding one would make the feature answer exactly
// one question well and the rest badly.

// bucketSpec resolves a bucket name or an explicit second count.
type bucketSpec struct {
	Seconds int
	Label   string
}

var namedBuckets = map[string]int{
	"10s": 10, "30s": 30,
	"1m": 60, "5m": 300, "15m": 900,
	"1h": 3600, "4h": 14400, "1d": 86400,
}

func resolveBucket(name string, seconds int) (bucketSpec, bool) {
	if seconds > 0 {
		if seconds < 1 || seconds > 86400*7 {
			return bucketSpec{}, false
		}
		return bucketSpec{Seconds: seconds, Label: humanDuration(seconds)}, true
	}
	if name == "" {
		name = "1m"
	}
	if secs, ok := namedBuckets[name]; ok {
		return bucketSpec{Seconds: secs, Label: name}, true
	}
	return bucketSpec{}, false
}

// AlertGroup is one time bucket.
type AlertGroup struct {
	FromMS   int64          `json:"from_ms"`
	ToMS     int64          `json:"to_ms"`
	Label    string         `json:"label"`
	Count    int            `json:"count"`
	Severity map[string]int `json:"by_severity"`
	Kind     map[string]int `json:"by_kind"`
	Symbols  []string       `json:"symbols"`
	Alerts   []Alert        `json:"alerts"`
}

// handleAlertGroups serves the retained history ordered by time and bucketed.
func (s *Server) handleAlertGroups(w http.ResponseWriter, r *http.Request) {
	if s.deps.Alerts == nil {
		http.Error(w, "alert history unavailable", http.StatusServiceUnavailable)
		return
	}
	query := r.URL.Query()
	spec, ok := resolveBucket(strings.TrimSpace(query.Get("bucket")), queryInt(r, "bucket_sec", 0))
	if !ok {
		http.Error(w, fmt.Sprintf("unsupported bucket %q; use 10s,30s,1m,5m,15m,1h,4h,1d or bucket_sec=1..604800",
			query.Get("bucket")), http.StatusBadRequest)
		return
	}

	symbol := strings.ToUpper(strings.TrimSpace(query.Get("symbol")))
	since := int64(queryInt(r, "since_ms", 0))
	until := int64(queryInt(r, "until_ms", 0))
	// Per-group cap, so one frantic minute cannot return ten thousand rows.
	perGroup := queryInt(r, "per_group", 50)
	order := strings.ToLower(strings.TrimSpace(query.Get("order")))
	if order != "asc" {
		order = "desc"
	}

	alerts, matched, err := s.deps.Alerts.ReadHistory(symbol, since, 2000)
	if err != nil {
		http.Error(w, "history read failed: "+err.Error(), http.StatusInternalServerError)
		return
	}
	// ReadHistory returns newest-first; group oldest-first so bucket ordering is
	// natural, then reverse at the end if the caller asked for newest-first.
	sort.SliceStable(alerts, func(i, j int) bool { return alerts[i].TSMS < alerts[j].TSMS })

	width := int64(spec.Seconds) * 1000
	groups := make([]AlertGroup, 0, 32)
	index := make(map[int64]int, 32)

	for _, alert := range alerts {
		if until > 0 && alert.TSMS > until {
			continue
		}
		// Floor to the bucket boundary so buckets are stable regardless of when
		// the query runs -- an unstable boundary makes two identical queries
		// disagree, which is the sort of thing nobody trusts twice.
		from := (alert.TSMS / width) * width
		pos, seen := index[from]
		if !seen {
			groups = append(groups, AlertGroup{
				FromMS:   from,
				ToMS:     from + width - 1,
				Label:    time.UnixMilli(from).Format(bucketLayout(spec.Seconds)),
				Severity: map[string]int{},
				Kind:     map[string]int{},
				Alerts:   []Alert{},
			})
			pos = len(groups) - 1
			index[from] = pos
		}
		group := &groups[pos]
		group.Count++
		group.Severity[alert.Severity]++
		group.Kind[alert.Kind]++
		if len(group.Alerts) < perGroup {
			group.Alerts = append(group.Alerts, alert)
		}
	}

	// Symbol lists, deduplicated, per group.
	for i := range groups {
		seen := make(map[string]struct{}, 8)
		list := make([]string, 0, 8)
		for _, alert := range groups[i].Alerts {
			if _, dup := seen[alert.Symbol]; dup {
				continue
			}
			seen[alert.Symbol] = struct{}{}
			list = append(list, alert.Symbol)
		}
		sort.Strings(list)
		groups[i].Symbols = list
	}

	if order == "desc" {
		for i, j := 0, len(groups)-1; i < j; i, j = i+1, j-1 {
			groups[i], groups[j] = groups[j], groups[i]
		}
		for i := range groups {
			for a, b := 0, len(groups[i].Alerts)-1; a < b; a, b = a+1, b-1 {
				groups[i].Alerts[a], groups[i].Alerts[b] = groups[i].Alerts[b], groups[i].Alerts[a]
			}
		}
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"type":        "alert_groups",
		"bucket":      spec.Label,
		"bucket_sec":  spec.Seconds,
		"order":       order,
		"symbol":      symbol,
		"groups":      groups,
		"group_count": len(groups),
		"matched":     matched,
		"stats":       s.deps.Alerts.HistoryStats(),
	})
}

// bucketLayout picks a timestamp format that makes the bucket width obvious at
// a glance: seconds for sub-minute buckets, time for hours, date for days.
func bucketLayout(seconds int) string {
	switch {
	case seconds < 60:
		return "15:04:05"
	case seconds < 86400:
		return "15:04"
	default:
		return "2006-01-02"
	}
}

func humanDuration(seconds int) string {
	switch {
	case seconds%86400 == 0:
		return fmt.Sprintf("%dd", seconds/86400)
	case seconds%3600 == 0:
		return fmt.Sprintf("%dh", seconds/3600)
	case seconds%60 == 0:
		return fmt.Sprintf("%dm", seconds/60)
	default:
		return fmt.Sprintf("%ds", seconds)
	}
}
