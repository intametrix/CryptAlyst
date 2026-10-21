// Real RocksDB — disabled by default because it needs librocksdb and CGO.
//
// To enable it:
//   brew install rocksdb              (or build librocksdb from source)
//   go get github.com/linxGnu/grocksdb
//   git mv rocksdb.go.disabled rocksdb.go
//   go build -tags rocksdb ./...
//
//
//	go get github.com/linxGnu/grocksdb
//	brew install rocksdb            # or build librocksdb from source
//	go build -tags rocksdb ./...
//
// The interface is identical to the LSM store, so switching is a flag, not a
// rewrite. Kept in the tree so the claim "we use RocksDB" is either true or
// provably one library install away — not a diagram.

import (
	"encoding/json"
	"fmt"

	"github.com/linxGnu/grocksdb"
)

type Rocks struct {
	db  *grocksdb.DB
	ro  *grocksdb.ReadOptions
	wo  *grocksdb.WriteOptions
}

func OpenRocks(path string) (*Rocks, error) {
	opts := grocksdb.NewDefaultOptions()
	opts.SetCreateIfMissing(true)
	db, err := grocksdb.OpenDb(opts, path)
	if err != nil {
		return nil, fmt.Errorf("rocksdb: %w", err)
	}
	return &Rocks{db: db, ro: grocksdb.NewDefaultReadOptions(), wo: grocksdb.NewDefaultWriteOptions()}, nil
}

func (r *Rocks) Name() string { return "rocksdb" }

func (r *Rocks) Put(e Envelope) error {
	body, err := json.Marshal(e)
	if err != nil {
		return err
	}
	if err := r.db.Put(r.wo, []byte(prefixByID+e.ID), body); err != nil {
		return err
	}
	return r.db.Put(r.wo, latestKey(e.Topic, e.Key), body)
}

func (r *Rocks) Latest(topic, key string) (Envelope, bool, error) {
	slice, err := r.db.Get(r.ro, latestKey(topic, key))
	if err != nil {
		return Envelope{}, false, err
	}
	defer slice.Free()
	if !slice.Exists() {
		return Envelope{}, false, nil
	}
	var e Envelope
	if err := json.Unmarshal(slice.Data(), &e); err != nil {
		return Envelope{}, false, err
	}
	return e, true, nil
}

func (r *Rocks) Get(id string) (Envelope, bool, error) {
	slice, err := r.db.Get(r.ro, []byte(prefixByID+id))
	if err != nil {
		return Envelope{}, false, err
	}
	defer slice.Free()
	if !slice.Exists() {
		return Envelope{}, false, nil
	}
	var e Envelope
	if err := json.Unmarshal(slice.Data(), &e); err != nil {
		return Envelope{}, false, err
	}
	return e, true, nil
}

func (r *Rocks) Count() (int, error) {
	it := r.db.NewIterator(r.ro)
	defer it.Close()
	n := 0
	for it.Seek([]byte(prefixByID)); it.Valid(); it.Next() {
		n++
	}
	return n, it.Err()
}

func (r *Rocks) Close() error {
	r.db.Close()
	return nil
}
