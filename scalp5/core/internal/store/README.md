# store — three specialised stores, one interface

The journal is the record. Everything here is a projection of it, chosen for the
question it answers fastest.

| Backend | Answers | File |
|---|---|---|
| journal | every envelope, in order, replayable | `envelopes.jsonl` |
| lsm (pebble) | what is true now, plus ordered scans by topic | `lsm/` |
| kv (bbolt) | the same question, smaller footprint, one writer | `kv.db` |
| sql (sqlite) | join books, trades and envelopes; survive restarts | `store.db` |
| docs (mongo) | searchable envelope documents; falls back to files | `docs/` |

```go
type Store interface {
    Put(Envelope) error
    Latest(topic, key string) (Envelope, bool, error)
    Get(id string) (Envelope, bool, error)
    Count() (int, error)
}
```

## Running

The recorder projects each envelope it writes:

```bash
./bin/recorder --port 8801 --data ../state/lake3 \
  --kv ../state/kv.db --sql ../state/store.db --docs ../state/docs
```

Mongo is used when it is reachable and skipped when it is not:

```bash
--mongo "mongodb://127.0.0.1:27017"
```

## Reading

```bash
./bin/storectl -cmd stats  -kv ../state/kv.db -sql ../state/store.db
./bin/storectl -cmd latest -kv ../state/kv.db -topic market.depth -key BTCUSDT
./bin/storectl -cmd sync   -sql ../state/store.db -packs ../state
./bin/storectl -cmd trades -sql ../state/store.db
```

`sync` projects the engines' own `pack_*.json` into the books and trades tables.
The packs stay the engines' truth; sqlite is what makes them queryable.

## Two rules the tests enforce

- **a failing backend never loses a write for the others** — `Multi` counts the
  failure and writes on (see `TestMultiSurvivesAFailingBackend`)
- **the projections are rebuildable from the journal alone** — see
  `TestJournalRebuildsTheProjections`

A bolt file is locked by its writer, so tools open it read-only with a two-second
timeout and say so rather than hanging.

## RocksDB, precisely

**RocksDB is built and linked.** `scripts/build-rocksdb.sh` compiles v10.10.1 into
`third_party/rocksdb10/librocksdb.a` (770 MB, not committed), and
`scripts/run-with-rocksdb.sh` sets the CGO flags so `-tags rocksdb` links it.

```bash
./scripts/build-rocksdb.sh                       # once, ~10 minutes
./scripts/run-with-rocksdb.sh go test -tags rocksdb ./internal/store/
./scripts/run-with-rocksdb.sh go build -tags rocksdb -o bin/recorder ./cmd/recorder
```

Three details that cost time to discover, so they are written down:

- **v10.10.1 is the version that matches grocksdb v1.10.8.** v1.11 wants a newer
  C API, v1.9 wants an older one; either way the build fails with
  "could not determine what C.rocksdb_… refers to".
- **Snappy is disabled** (`USE_SNAPPY=0`) because the machine has no libsnappy, but
  the binding still asks for `-lsnappy`, so a one-symbol stub library satisfies the
  linker. Everything else (zstd, lz4, bz2, zlib) comes from /opt/homebrew/lib.
- **`DISABLE_WARNING_AS_ERROR=1`** is required: RocksDB's Makefile turns the modern
  SDK's libc++ deprecation warnings into errors.

Without the tag the platform still builds and runs; `--rocksdb` is then accepted and
declines politely rather than the binary failing to exist.

Pebble remains available as `--lsm`: same interface, no C++ toolchain, useful on a
machine where the library has not been built.

## Durability of the projections

Pebble writes are committed with `NoSync`: the projection may lose its last few
writes on a hard stop. That is deliberate — the journal is the record, and the
projections are rebuildable from it (see `TestJournalRebuildsTheProjections`). The
recorder counts store failures rather than failing the write.
