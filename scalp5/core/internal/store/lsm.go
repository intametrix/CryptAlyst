package store

import (
	"encoding/json"

	"github.com/cockroachdb/pebble/v2"
)

// LSM is the RocksDB-class store: a log-structured merge tree with sorted keys,
// point lookups, range scans and snapshots. It is Pebble, the pure-Go equivalent
// of RocksDB, because RocksDB itself needs librocksdb and CGO (see rocksdb.go for
// that build when the library is present).
//
// Two keyspaces live in one LSM:
//
//	byid/<id>                 the envelope
//	latest/<topic>\x00<key>   pointer to the newest envelope for that pair
//
// Sorted keys are the point: "latest/model.fill/*" is a range scan, not a filter.

const (
	prefixByID   = "byid/"
	prefixLatest = "latest/"
)

// noLogger keeps the store quiet: a WAL replay notice is not news to the platform.
type noLogger struct{}

func (noLogger) Infof(string, ...interface{})  {}
func (noLogger) Errorf(string, ...interface{}) {}
func (noLogger) Fatalf(string, ...interface{}) {}

type LSM struct {
	db *pebble.DB
}

func OpenLSM(path string) (*LSM, error) {
	opts := &pebble.Options{Logger: noLogger{}}
	db, err := pebble.Open(path, opts)
	if err != nil {
		return nil, err
	}
	return &LSM{db: db}, nil
}

func (l *LSM) Name() string { return "lsm" }

func latestKey(topic, key string) []byte {
	// \x00 cannot appear in a topic or a key, so the pair is unambiguous and the
	// prefix "latest/<topic>\x00" scans all keys of one topic in order.
	return append(append([]byte(prefixLatest), topic...), append([]byte{0}, key...)...)
}

func (l *LSM) Put(e Envelope) error {
	body, err := json.Marshal(e)
	if err != nil {
		return err
	}
	batch := l.db.NewBatch()
	defer batch.Close()

	if err := batch.Set([]byte(prefixByID+e.ID), body, nil); err != nil {
		return err
	}
	// the newest wins: only overwrite if this envelope is not older than what is there
	cur, closer, err := l.db.Get(latestKey(e.Topic, e.Key))
	if err == nil {
		var prev Envelope
		if json.Unmarshal(cur, &prev) == nil && prev.TS > e.TS {
			closer.Close()
			return batch.Commit(pebble.NoSync)
		}
		closer.Close()
	} else if err != pebble.ErrNotFound {
		return err
	}
	if err := batch.Set(latestKey(e.Topic, e.Key), body, nil); err != nil {
		return err
	}
	return batch.Commit(pebble.NoSync)
}

func (l *LSM) Latest(topic, key string) (Envelope, bool, error) {
	body, closer, err := l.db.Get(latestKey(topic, key))
	if err == pebble.ErrNotFound {
		return Envelope{}, false, nil
	}
	if err != nil {
		return Envelope{}, false, err
	}
	defer closer.Close()
	var e Envelope
	if err := json.Unmarshal(body, &e); err != nil {
		return Envelope{}, false, err
	}
	return e, true, nil
}

func (l *LSM) Get(id string) (Envelope, bool, error) {
	body, closer, err := l.db.Get([]byte(prefixByID + id))
	if err == pebble.ErrNotFound {
		return Envelope{}, false, nil
	}
	if err != nil {
		return Envelope{}, false, err
	}
	defer closer.Close()
	var e Envelope
	if err := json.Unmarshal(body, &e); err != nil {
		return Envelope{}, false, err
	}
	return e, true, nil
}

func (l *LSM) Count() (int, error) {
	it, err := l.db.NewIter(&pebble.IterOptions{
		LowerBound: []byte(prefixByID),
		UpperBound: []byte(prefixByID + "\xff"),
	})
	if err != nil {
		return 0, err
	}
	defer it.Close()
	n := 0
	for it.First(); it.Valid(); it.Next() {
		n++
	}
	return n, it.Error()
}

// TopicKeys lists the keys of one topic, in sorted order — the thing a merge tree
// is actually for, and the reason not to model this as a hash map.
func (l *LSM) TopicKeys(topic string, limit int) ([]string, error) {
	lower := append([]byte(prefixLatest), topic...)
	lower = append(lower, 0)
	it, err := l.db.NewIter(&pebble.IterOptions{LowerBound: lower, UpperBound: append(lower, 0xff)})
	if err != nil {
		return nil, err
	}
	defer it.Close()
	var out []string
	for it.First(); it.Valid(); it.Next() {
		k := it.Key()
		if i := len(lower); len(k) > i {
			out = append(out, string(k[i:]))
		}
		if limit > 0 && len(out) >= limit {
			break
		}
	}
	return out, it.Error()
}

func (l *LSM) Close() error { return l.db.Close() }
