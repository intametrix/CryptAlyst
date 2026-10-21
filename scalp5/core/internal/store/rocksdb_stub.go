//go:build !rocksdb

package store

import "errors"

// OpenRocks exists only when the rocksdb build tag is set; without it the flag is
// accepted and the store politely declines, rather than the binary failing to build.
func OpenRocks(string) (*rocksStub, error) {
	return nil, errors.New("built without -tags rocksdb")
}

type rocksStub struct{}

func (r *rocksStub) Name() string                                  { return "rocksdb" }
func (r *rocksStub) Put(Envelope) error                            { return nil }
func (r *rocksStub) Latest(string, string) (Envelope, bool, error) { return Envelope{}, false, nil }
func (r *rocksStub) Get(string) (Envelope, bool, error)            { return Envelope{}, false, nil }
func (r *rocksStub) Count() (int, error)                           { return 0, nil }
func (r *rocksStub) Close() error                                  { return nil }
