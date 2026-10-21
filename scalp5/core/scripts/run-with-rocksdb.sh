#!/bin/bash
# Run a Go command linked against the locally built RocksDB.
#
#   ./scripts/run-with-rocksdb.sh go test -tags rocksdb ./internal/store/
#   ./scripts/run-with-rocksdb.sh go build -tags rocksdb -o bin/recorder ./cmd/recorder
set -eu
cd "$(dirname "$0")/.."
DIR="$PWD/third_party/rocksdb10"
export CGO_CFLAGS="-I$DIR/include"
export CGO_LDFLAGS="-L$DIR -lrocksdb -L$DIR/stub -lsnappy -L/opt/homebrew/lib -lzstd -llz4 -lbz2 -lz -lstdc++ -lm"
exec "$@"
