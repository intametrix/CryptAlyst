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

**RocksDB is not in the build.** It needs `librocksdb` and CGO, and this machine has
neither (no Homebrew, no library). What is in the build is **Pebble**, CockroachDB's
pure-Go LSM — the same shape as RocksDB: sorted keys, point lookups, range scans,
snapshots, write batches.

The real adapter is parked in `rocksdb.go.disabled`, complete and compiling once the
library exists:

```bash
brew install rocksdb
go get github.com/linxGnu/grocksdb
git mv rocksdb.go.disabled rocksdb.go
go build -tags rocksdb ./...
```

Saying "we use RocksDB" when the binary links Pebble would be false; saying "we use a
RocksDB-class LSM, and here is the RocksDB adapter one install away" is true, so that
is what the platform says.

## Durability of the projections

Pebble writes are committed with `NoSync`: the projection may lose its last few
writes on a hard stop. That is deliberate — the journal is the record, and the
projections are rebuildable from it (see `TestJournalRebuildsTheProjections`). The
recorder counts store failures rather than failing the write.
