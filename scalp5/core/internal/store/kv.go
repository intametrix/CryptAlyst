package store

import (
	"encoding/json"
	"fmt"
	"time"

	bolt "go.etcd.io/bbolt"
)

// KV answers the question the platform asks most often: what is true for this
// key right now? It stores the newest envelope per (topic, key) and every
// envelope by id, and it survives a restart without a scan.

var (
	bucketLatest = []byte("latest") // "topic|key" -> envelope id
	bucketByID   = []byte("byid")   // id -> envelope JSON
)

type KV struct {
	db *bolt.DB
}

func OpenKV(path string) (*KV, error) { return open(path, false) }

// OpenKVReadOnly is for tools that inspect a live store. It fails fast instead of
// waiting: a bolt file is locked by its writer, and a reader that blocks forever is
// worse than one that says why.
func OpenKVReadOnly(path string) (*KV, error) { return open(path, true) }

func open(path string, readOnly bool) (*KV, error) {
	db, err := bolt.Open(path, 0o644, &bolt.Options{Timeout: 2 * time.Second, ReadOnly: readOnly})
	if err != nil {
		return nil, err
	}
	err = db.Update(func(tx *bolt.Tx) error {
		if _, err := tx.CreateBucketIfNotExists(bucketLatest); err != nil {
			return err
		}
		_, err := tx.CreateBucketIfNotExists(bucketByID)
		return err
	})
	if err != nil {
		return nil, err
	}
	return &KV{db: db}, nil
}

func (k *KV) Name() string { return "kv" }

func (k *KV) Put(e Envelope) error {
	body, err := json.Marshal(e)
	if err != nil {
		return err
	}
	return k.db.Update(func(tx *bolt.Tx) error {
		if err := tx.Bucket(bucketByID).Put([]byte(e.ID), body); err != nil {
			return err
		}
		slot := fmt.Sprintf("%s|%s", e.Topic, e.Key)
		return tx.Bucket(bucketLatest).Put([]byte(slot), []byte(e.ID))
	})
}

func (k *KV) Latest(topic, key string) (Envelope, bool, error) {
	var out Envelope
	var found bool
	err := k.db.View(func(tx *bolt.Tx) error {
		id := tx.Bucket(bucketLatest).Get([]byte(fmt.Sprintf("%s|%s", topic, key)))
		if id == nil {
			return nil
		}
		body := tx.Bucket(bucketByID).Get(id)
		if body == nil {
			return nil
		}
		found = true
		return json.Unmarshal(body, &out)
	})
	return out, found, err
}

func (k *KV) Get(id string) (Envelope, bool, error) {
	var out Envelope
	var found bool
	err := k.db.View(func(tx *bolt.Tx) error {
		body := tx.Bucket(bucketByID).Get([]byte(id))
		if body == nil {
			return nil
		}
		found = true
		return json.Unmarshal(body, &out)
	})
	return out, found, err
}

func (k *KV) Count() (int, error) {
	n := 0
	err := k.db.View(func(tx *bolt.Tx) error {
		n = tx.Bucket(bucketByID).Stats().KeyN
		return nil
	})
	return n, err
}

func (k *KV) Close() error { return k.db.Close() }
