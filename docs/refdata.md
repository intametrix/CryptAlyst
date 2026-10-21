# Reference Data Service (refdata)

The lake's registry of instrument identity. The lake keeps carrying
envelopes; this is the call the lake (or any book holding a bare ticker,
ISIN, CUSIP, FIGI, or exchange symbol) makes to learn what the thing IS:
the canonical instrument, its class, venues, limits, attached definitions,
and every identifier other stores know it by.

Status: built, tested (race detector), smoke-tested live. Part of the
`scalp5/core` module, alongside `envelope`, `lake`, and `correlation`.

## Where it sits

```
books / lake consumers holding "BTCUSDT" or "US0378331005"
        |  HTTP (answers travel in the fixed envelope shape)
        v
cmd/refdata  --journal-->  <data>/refdata.jsonl
        |
        +-- internal/refdata.Service   (registry, resolution, correlation)
        +-- internal/correlation       (CID minting for answers)
        +-- internal/envelope          (the shape answers travel in)
```

- **The DATA LAKE still uses the ENVELOPE.** Refdata answers are
  `envelope.Envelope[any]` with `meta.plane: intel`, `meta.producer:
  refdata`, and a CID minted by the correlation package — so a refdata
  lookup can hang inside a decision chain the correlation service maps.
  Write requests accept either the bare payload or an envelope wrapping it.
- **Refdata is part of the lake**, not a separate silo: it follows the
  lake's storage philosophy (one `Backend` interface, JSONL journal as the
  durable path, memory for the hot path) and its data lives under
  `scalp5/state/refdata/` by default, next to `lake*/`.

## Data model

| Entity | What it is | Key |
|---|---|---|
| `Instrument` | canonical record: class, symbol, venues, limits (map), meta (map), status | `crypto_perp:<venue>:<symbol>` (lowercase) |
| `Document` | markdown blob: definitions, specs, risk notes, limits; sha256-addressed | `doc-<sha256[:12]>`, or explicit |
| `Edge` | correlation triple linking identifiers, instruments, venues, documents | `(subject, predicate, object)` |

Classes are not crypto-only: `crypto_spot`, `crypto_perp`, `crypto_future`,
`equity`, `fx`, `commodity`, `index`, `other` — an equity adapter later
needs no schema change.

Predicates: `same_as` (identifier ↔ instrument, alias ↔ alias),
`listed_on`, `has_document`, `issued_by`, `tracks`, `derived_from`.

Identifiers are case-insensitive at the registry boundary (`isin:GEN111`
== `isin:gen111`); canonical instrument ids are stored lowercase.
Identifier endpoints of edges are normalised at ingestion, so adapters
writing mixed case converge.

## Resolution

`Resolve(type, value)` maps any identifier to canonical instruments:
direct index hit, then a transitive walk over `same_as` edges (bounded at 8
hops, cycle-safe) so alias-of-alias chains resolve. `Correlations(id)`
returns every edge touching an instrument — the join that lets a record
keyed by ISIN in one store and by ticker in another land on the same
instrument.

## HTTP API (default 127.0.0.1:8790)

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | liveness (bare JSON) |
| GET | `/v1/stats` | registry + backend counts |
| PUT | `/v1/instruments` | upsert instrument (idempotent) |
| GET | `/v1/instruments` | list; filters `class`, `venue`, `status`, `q` |
| GET | `/v1/instruments/{id}` | one instrument |
| POST | `/v1/instruments/{id}/identifiers` | register `{type, value}` + same_as edge |
| GET | `/v1/instruments/{id}/documents` | attached documents |
| GET | `/v1/instruments/{id}/correlations` | all edges touching the instrument |
| GET | `/v1/resolve?type=&value=` | identifier → instruments (type optional) |
| PUT | `/v1/documents` | upsert document (body hashed) |
| GET | `/v1/documents/{id}` | one document |
| PUT | `/v1/edges` | record a correlation triple |
| GET | `/v1/edges?subject=&predicate=&object=` | filter edges |
| GET | `/v1/search?q=` | instruments + documents + edges |

## Storage and the swap point

`internal/refdata.Backend` is the swap point in the lake style:

```go
type Backend interface {
    Append(Entry) error
    Replay(func(Entry)) error
    Name() string
    Close() error
}
```

Shipped: `Journal` (append-only JSONL under `<data>/refdata.jsonl`,
replayed on open, last-write-wins for instruments/documents, edge-triple
dedupe, torn final line dropped not fatal) and `Memory` (hot path, tests).
**The lake decides placement for anything else**: a SQLite adapter for
structured attribute queries, RocksDB for high-write identifier aliasing, a
vector backend for document embeddings, Mongo for document payloads — each
implements exactly those four methods and nothing else changes. The
service is a projection of the journal; swap the backend, replay, identical
answers.

## Running

```
cd scalp5/core
go run ./cmd/refdata                      # defaults: ../state/refdata, 127.0.0.1:8790
go run ./cmd/refdata -seed cmd/refdata/seed.json   # idempotent ingest
```

Seed covers the ten USDT-M perps observed in the repo ledgers (BTC, ETH,
SOL, XRP, NEAR, ZEC, AVA, UNI, WLD, SPCXB) plus a `crypto_perp` class
definition. The venue label `binance_usdt_m` is inferred from bot
configuration and flagged as such in the class doc — confirm against venue
config before position-limit logic relies on it.

Port map on the platform box (verified 2026-10-21): 8799 Python health
service, 8801 recorder, 8802 envelope explorer, **8790 refdata**.

## Conventions and rules

- Tests use generated data only (Rule A3); the seed is committed fixture
  data, marked with `source` provenance.
- Nothing existing was modified except `scalp5/core/go.mod` (go directive
  1.21 → 1.22, required for `http.ServeMux` path values; toolchain is
  1.27.1 and the backend module already declares 1.27.1).
- Disclosed gap: `check.sh` gates `backend/` only. The core module passes
  `gofmt -l`/`go vet`/`go test -race` for the new packages, but several
  pre-existing core files (historian, recorder/refs_test, correlation,
  lake, marketdata/state, operator) were already unformatted before this
  work and remain so — a core-module gate step is a separate decision.
