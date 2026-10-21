// Command refdata serves the lake's reference-data registry over HTTP.
//
// The lake keeps carrying envelopes; this service is the call the lake (and
// any book holding a bare ticker, ISIN, or exchange symbol) makes to learn
// what the thing IS: canonical instrument, class, venues, limits, attached
// definitions, and every identifier other stores know it by. Every answer
// travels in the fixed envelope shape, so bus-side consumers parse it with
// the same decoder as everything else.
//
//	Wiring, in one picture:
//
//		lake envelopes / book with a ticker
//		         |  HTTP
//		         v
//		refdata.Service  --journal-->  <data>/refdata.jsonl
//
// Flags: -data dir (default ../state/refdata), -addr (default 127.0.0.1:8790),
// -backend jsonl|memory, -seed file (idempotent one-shot ingest).
package main

import (
	"context"
	"embed"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"io/fs"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"cryptalyst/core/internal/correlation"
	"cryptalyst/core/internal/envelope"
	"cryptalyst/core/internal/refdata"
)

// staticFS holds the embedded registry explorer (the viewer app), served by
// this binary the same way the envelope explorer is: one process, one port,
// API under /v1, app everywhere else.
//
//go:embed static
var staticFS embed.FS

// decodeBody reads a write request. Callers may send the bare payload or an
// envelope wrapping it; the answer is always enveloped, so the request side
// stays permissive for bus-side clients.
func decodeBody[T any](r *http.Request) (T, error) {
	var zero T
	raw, err := io.ReadAll(io.LimitReader(r.Body, 8<<20))
	if err != nil {
		return zero, err
	}
	var top map[string]json.RawMessage
	if err := json.Unmarshal(raw, &top); err != nil {
		return zero, fmt.Errorf("invalid JSON: %w", err)
	}
	payload, ok := top["data"]
	if !ok || top["meta"] == nil {
		payload = raw // bare payload
	}
	var out T
	if err := json.Unmarshal(payload, &out); err != nil {
		return zero, fmt.Errorf("invalid payload: %w", err)
	}
	return out, nil
}

// wrap answers in the fixed envelope shape with a fresh correlation id, so a
// refdata lookup can sit inside a decision chain the correlation service maps.
func wrap(topic string, payload any) envelope.Envelope[any] {
	return envelope.New[any](payload, envelope.Meta{
		Plane:    envelope.PlaneIntel,
		Topic:    topic,
		Producer: "refdata",
		Key:      topic,
		CID:      correlation.Mint(),
		Extra: map[string]string{
			"service": "refdata",
		},
	})
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func writeErr(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}

// server adapts refdata.Service to HTTP. Methods live on the struct so tests
// can mount the mux without a listener.
type server struct {
	svc *refdata.Service
}

func (s *server) routes() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]bool{"ok": true})
	})
	mux.HandleFunc("GET /v1/stats", s.stats)
	mux.HandleFunc("PUT /v1/instruments", s.putInstrument)
	mux.HandleFunc("GET /v1/instruments", s.listInstruments)
	mux.HandleFunc("GET /v1/instruments/{id}", s.getInstrument)
	mux.HandleFunc("GET /v1/instruments/{id}/documents", s.instrumentDocuments)
	mux.HandleFunc("POST /v1/instruments/{id}/identifiers", s.addIdentifier)
	mux.HandleFunc("GET /v1/instruments/{id}/correlations", s.correlations)
	mux.HandleFunc("GET /v1/resolve", s.resolve)
	mux.HandleFunc("PUT /v1/documents", s.putDocument)
	mux.HandleFunc("GET /v1/documents", s.listDocuments)
	mux.HandleFunc("GET /v1/documents/{id}", s.getDocument)
	mux.HandleFunc("PUT /v1/edges", s.putEdge)
	mux.HandleFunc("GET /v1/edges", s.edges)
	mux.HandleFunc("GET /v1/search", s.search)
	mux.Handle("/", uiHandler())
	return mux
}

// uiHandler serves the embedded explorer: static assets under /static/,
// the app shell at every other non-API path (client-side routing), with
// index.html as the fallback.
func uiHandler() http.Handler {
	sub, err := fs.Sub(staticFS, "static")
	if err != nil {
		log.Fatalf("embedded ui: %v", err)
	}
	fileServer := http.FileServer(http.FS(sub))
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		p := r.URL.Path
		if p == "/" || p == "/ui" || p == "/ui/" {
			serveUIFile(w, sub, "index.html")
			return
		}
		if strings.HasPrefix(p, "/static/") {
			r2 := *r
			r2.URL = cloneURLWithPath(r, strings.TrimPrefix(p, "/static"))
			fileServer.ServeHTTP(w, &r2)
			return
		}
		// Unknown non-API path: the app shell handles it client-side.
		serveUIFile(w, sub, "index.html")
	})
}

func cloneURLWithPath(r *http.Request, path string) *url.URL {
	u := *r.URL
	u.Path = path
	u.RawPath = ""
	return &u
}

func serveUIFile(w http.ResponseWriter, fsys fs.FS, name string) {
	raw, err := fs.ReadFile(fsys, name)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "embedded ui missing")
		return
	}
	if strings.HasSuffix(name, ".html") {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
	} else if strings.HasSuffix(name, ".css") {
		w.Header().Set("Content-Type", "text/css")
	} else if strings.HasSuffix(name, ".js") {
		w.Header().Set("Content-Type", "text/javascript")
	}
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(raw)
}

func (s *server) stats(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, wrap("refdata.stats", s.svc.Stats()))
}

func (s *server) putInstrument(w http.ResponseWriter, r *http.Request) {
	body, err := decodeBody[refdata.Instrument](r)
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	inst, err := s.svc.PutInstrument(body)
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, wrap("refdata.instrument", inst))
}

func (s *server) listInstruments(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	filter := refdata.Filter{
		Class:  refdata.Class(q.Get("class")),
		Venue:  q.Get("venue"),
		Status: q.Get("status"),
		Q:      q.Get("q"),
	}
	writeJSON(w, http.StatusOK, wrap("refdata.instruments", s.svc.List(filter)))
}

func (s *server) getInstrument(w http.ResponseWriter, r *http.Request) {
	inst, err := s.svc.GetInstrument(r.PathValue("id"))
	if err != nil {
		writeErr(w, http.StatusNotFound, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, wrap("refdata.instrument", inst))
}

func (s *server) instrumentDocuments(w http.ResponseWriter, r *http.Request) {
	docs := s.svc.DocumentsFor(r.PathValue("id"))
	writeJSON(w, http.StatusOK, wrap("refdata.documents", docs))
}

func (s *server) addIdentifier(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Type  string `json:"type"`
		Value string `json:"value"`
	}
	dec, err := decodeBody[struct {
		Type  string `json:"type"`
		Value string `json:"value"`
	}](r)
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	body = dec
	edge, err := s.svc.AddIdentifier(r.PathValue("id"), body.Type, body.Value)
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, wrap("refdata.edge", edge))
}

func (s *server) correlations(w http.ResponseWriter, r *http.Request) {
	edges := s.svc.Correlations(r.PathValue("id"))
	writeJSON(w, http.StatusOK, wrap("refdata.edges", edges))
}

func (s *server) resolve(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	idType, value := q.Get("type"), q.Get("value")
	if value == "" {
		writeErr(w, http.StatusBadRequest, "value query parameter is required")
		return
	}
	writeJSON(w, http.StatusOK, wrap("refdata.resolve", s.svc.Resolve(idType, value)))
}

func (s *server) putDocument(w http.ResponseWriter, r *http.Request) {
	body, err := decodeBody[refdata.Document](r)
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	doc, err := s.svc.PutDocument(body)
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, wrap("refdata.document", doc))
}

func (s *server) getDocument(w http.ResponseWriter, r *http.Request) {
	doc, err := s.svc.GetDocument(r.PathValue("id"))
	if err != nil {
		writeErr(w, http.StatusNotFound, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, wrap("refdata.document", doc))
}

func (s *server) listDocuments(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	filter := refdata.DocFilter{
		InstrumentID: q.Get("instrument"),
		Class:        refdata.Class(q.Get("class")),
		Kind:         q.Get("kind"),
		Q:            q.Get("q"),
	}
	writeJSON(w, http.StatusOK, wrap("refdata.documents", s.svc.ListDocuments(filter)))
}

func (s *server) putEdge(w http.ResponseWriter, r *http.Request) {
	body, err := decodeBody[refdata.Edge](r)
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	edge, err := s.svc.PutEdge(body)
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, wrap("refdata.edge", edge))
}

func (s *server) edges(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	writeJSON(w, http.StatusOK, wrap("refdata.edges", s.svc.Edges(
		q.Get("subject"), q.Get("predicate"), q.Get("object"))))
}

func (s *server) search(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query().Get("q")
	if strings.TrimSpace(q) == "" {
		writeErr(w, http.StatusBadRequest, "q query parameter is required")
		return
	}
	writeJSON(w, http.StatusOK, wrap("refdata.search", s.svc.Search(q)))
}

// seedFile is the shape of -seed JSON: upserts, applied in order, idempotent
// because every Put is an upsert keyed by id.
type seedFile struct {
	Instruments []refdata.Instrument `json:"instruments"`
	Documents   []refdata.Document   `json:"documents"`
	Edges       []refdata.Edge       `json:"edges"`
}

func applySeed(svc *refdata.Service, path string) (int, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return 0, err
	}
	var seed seedFile
	if err := json.Unmarshal(raw, &seed); err != nil {
		return 0, fmt.Errorf("seed: %w", err)
	}
	n := 0
	for _, inst := range seed.Instruments {
		if _, err := svc.PutInstrument(inst); err != nil {
			return n, fmt.Errorf("seed instrument %s: %w", inst.ID, err)
		}
		n++
	}
	for _, doc := range seed.Documents {
		if _, err := svc.PutDocument(doc); err != nil {
			return n, fmt.Errorf("seed document %s: %w", doc.Title, err)
		}
		n++
	}
	for _, edge := range seed.Edges {
		if _, err := svc.PutEdge(edge); err != nil {
			return n, fmt.Errorf("seed edge %s-%s-%s: %w", edge.Subject, edge.Predicate, edge.Object, err)
		}
		n++
	}
	return n, nil
}

func main() {
	log.SetFlags(log.LstdFlags | log.Lmicroseconds)
	dataDir := flag.String("data", "../state/refdata", "refdata journal directory")
	// 8790 is verified free on the platform box; 8799 is taken by the Python
	// health service, 8801 by the recorder, 8802 by the envelope explorer.
	addr := flag.String("addr", "127.0.0.1:8790", "listen address")
	backendFlag := flag.String("backend", "jsonl", "storage backend: jsonl | memory")
	seedPath := flag.String("seed", "", "optional seed JSON, applied idempotently at boot")
	flag.Parse()

	var backend refdata.Backend
	switch *backendFlag {
	case "memory":
		backend = refdata.NewMemory()
	case "jsonl":
		j, err := refdata.OpenJournal(filepath.Join(*dataDir, "refdata.jsonl"))
		if err != nil {
			log.Fatalf("open journal: %v", err)
		}
		backend = j
	default:
		log.Fatalf("unknown -backend %q (want jsonl|memory)", *backendFlag)
	}
	svc, err := refdata.NewService(backend)
	if err != nil {
		log.Fatalf("refdata: %v", err)
	}
	defer svc.Close()

	if *seedPath != "" {
		n, err := applySeed(svc, *seedPath)
		if err != nil {
			log.Fatalf("seed: %v", err)
		}
		log.Printf("seed applied: %d records from %s", n, *seedPath)
	}
	stats := svc.Stats()
	log.Printf("refdata backend=%s instruments=%d documents=%d edges=%d replayed=%d",
		stats.Backend, stats.Instruments, stats.Documents, stats.Edges, stats.Replayed)

	srv := &server{svc: svc}
	httpServer := &http.Server{
		Addr:              *addr,
		Handler:           srv.routes(),
		ReadHeaderTimeout: 5 * time.Second,
	}

	ln, err := net.Listen("tcp", *addr)
	if err != nil {
		log.Fatalf("listen: %v", err)
	}
	log.Printf("refdata listening on %s (data %s)", ln.Addr().String(), *dataDir)

	errCh := make(chan error, 1)
	go func() { errCh <- httpServer.Serve(ln) }()
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	select {
	case sig := <-sigCh:
		log.Printf("shutting down: %s", sig)
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		_ = httpServer.Shutdown(ctx)
	case err := <-errCh:
		if err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Fatalf("serve: %v", err)
		}
	}
	log.Printf("refdata stopped: %+v", svc.Stats())
}
