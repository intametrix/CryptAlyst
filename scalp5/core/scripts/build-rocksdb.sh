#!/bin/bash
# Build RocksDB as a static library the Go binding can link.
#
#   ./scripts/build-rocksdb.sh            # into third_party/rocksdb10
#   ./scripts/run-with-rocksdb.sh <cmd>   # run a command with the CGO flags set
#
# ~770 MB, not committed. Snappy is disabled: the binding asks for -lsnappy, so the
# run script points the linker at an empty stub, which is all a snappy-free RocksDB
# needs.
set -eu
cd "$(dirname "$0")/.."
DIR="third_party/rocksdb10"

if [ ! -d "$DIR" ]; then
  echo "· cloning rocksdb v10.10.1"
  git clone --depth 1 --branch v10.10.1 https://github.com/facebook/rocksdb.git "$DIR"
fi

if [ ! -f "$DIR/librocksdb.a" ]; then
  echo "· building (about 10 minutes on 8 cores)"
  ( cd "$DIR" && make clean >/dev/null 2>&1 || true
    MACOSX_DEPLOYMENT_TARGET=13.0 make -j"$(sysctl -n hw.ncpu)" static_lib \
      DISABLE_JEMALLOC=1 DISABLE_WARNING_AS_ERROR=1 USE_SNAPPY=0 )
fi

mkdir -p "$DIR/stub"
if [ ! -f "$DIR/stub/libsnappy.a" ]; then
  echo "· stubbing snappy"
  printf 'void snappy_link_marker(void) {}\n' > "$DIR/stub/snappy_stub.c"
  clang -c "$DIR/stub/snappy_stub.c" -o "$DIR/stub/snappy_stub.o"
  ar rcs "$DIR/stub/libsnappy.a" "$DIR/stub/snappy_stub.o"
fi

echo "· ready: $DIR/librocksdb.a"
