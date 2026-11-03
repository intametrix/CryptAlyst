package api

import "net/http"

// queryReader is the typed front door to a request's query string, so handlers do
// not each spell out the same three lines.
type queryReader struct{ r *http.Request }

func readQuery(r *http.Request) *queryReader { return &queryReader{r: r} }

func (q *queryReader) symbol() string                        { return paramSymbol(q.r) }
func (q *queryReader) interval(def string) string            { return paramInterval(q.r, def) }
func (q *queryReader) int(key string, def, lo, hi int) int   { return paramInt(q.r, key, def, lo, hi) }
func (q *queryReader) int64(key string, def int64) int64     { return paramInt64(q.r, key, def) }
func (q *queryReader) float(key string, def float64) float64 { return paramFloat(q.r, key, def) }
func (q *queryReader) text(key, def string) string {
	if v := q.r.URL.Query().Get(key); v != "" {
		return v
	}
	return def
}
