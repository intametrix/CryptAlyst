package store

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"time"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
)

// Docs stores envelopes as documents. Mongo is used when it is reachable; the
// file implementation is the default and answers the same questions, so the rest
// of the platform does not care which is behind it.

type Docs interface {
	Store
}

type doc struct {
	ID       string          `bson:"_id" json:"id"`
	Topic    string          `bson:"topic" json:"topic"`
	Key      string          `bson:"key" json:"key"`
	Plane    string          `bson:"plane" json:"plane"`
	Producer string          `bson:"producer" json:"producer"`
	TS       int64           `bson:"ts" json:"ts"`
	Data     json.RawMessage `bson:"data" json:"data"`
}

// ---------------------------------------------------------------- file docs

type FileDocs struct {
	dir string
}

func OpenFileDocs(dir string) (*FileDocs, error) {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	return &FileDocs{dir: dir}, nil
}

func (f *FileDocs) Name() string { return "docs(file)" }

func (f *FileDocs) path(id string) string {
	safe := strings.NewReplacer("|", "_", "/", "_", ":", "_").Replace(id)
	return fmt.Sprintf("%s/%s.json", f.dir, safe)
}

func (f *FileDocs) Put(e Envelope) error {
	body, err := json.Marshal(doc{ID: e.ID, Topic: e.Topic, Key: e.Key, Plane: e.Plane,
		Producer: e.Producer, TS: e.TS, Data: e.Data})
	if err != nil {
		return err
	}
	return os.WriteFile(f.path(e.ID), body, 0o644)
}

func (f *FileDocs) Get(id string) (Envelope, bool, error) {
	body, err := os.ReadFile(f.path(id))
	if err != nil {
		if os.IsNotExist(err) {
			return Envelope{}, false, nil
		}
		return Envelope{}, false, err
	}
	var d doc
	if err := json.Unmarshal(body, &d); err != nil {
		return Envelope{}, false, err
	}
	return Envelope{ID: d.ID, Topic: d.Topic, Key: d.Key, Plane: d.Plane,
		Producer: d.Producer, TS: d.TS, Data: d.Data}, true, nil
}

// Latest scans the directory: acceptable for a projection, never for the record.
func (f *FileDocs) Latest(topic, key string) (Envelope, bool, error) {
	entries, err := os.ReadDir(f.dir)
	if err != nil {
		return Envelope{}, false, err
	}
	var best Envelope
	var found bool
	for _, e := range entries {
		body, err := os.ReadFile(f.dir + "/" + e.Name())
		if err != nil {
			continue
		}
		var d doc
		if json.Unmarshal(body, &d) != nil {
			continue
		}
		if d.Topic != topic || (key != "" && d.Key != key) {
			continue
		}
		if !found || d.TS > best.TS {
			best = Envelope{ID: d.ID, Topic: d.Topic, Key: d.Key, Plane: d.Plane,
				Producer: d.Producer, TS: d.TS, Data: d.Data}
			found = true
		}
	}
	return best, found, nil
}

func (f *FileDocs) Count() (int, error) {
	entries, err := os.ReadDir(f.dir)
	return len(entries), err
}

func (f *FileDocs) Close() error { return nil }

// ---------------------------------------------------------------- mongo docs

type MongoDocs struct {
	client *mongo.Client
	coll   *mongo.Collection
}

// OpenMongo connects only if it can, quickly: a store that hangs is worse than
// one that is absent.
func OpenMongo(uri, database, collection string) (*MongoDocs, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	client, err := mongo.Connect(options.Client().ApplyURI(uri))
	if err != nil {
		return nil, err
	}
	if err := client.Ping(ctx, nil); err != nil {
		_ = client.Disconnect(ctx)
		return nil, fmt.Errorf("mongo unreachable: %w", err)
	}
	return &MongoDocs{client: client, coll: client.Database(database).Collection(collection)}, nil
}

func (m *MongoDocs) Name() string { return "docs(mongo)" }

func (m *MongoDocs) Put(e Envelope) error {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	_, err := m.coll.ReplaceOne(ctx, bson.M{"_id": e.ID},
		doc{ID: e.ID, Topic: e.Topic, Key: e.Key, Plane: e.Plane,
			Producer: e.Producer, TS: e.TS, Data: e.Data},
		options.Replace().SetUpsert(true))
	return err
}

func (m *MongoDocs) Get(id string) (Envelope, bool, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	var d doc
	err := m.coll.FindOne(ctx, bson.M{"_id": id}).Decode(&d)
	if err == mongo.ErrNoDocuments {
		return Envelope{}, false, nil
	}
	if err != nil {
		return Envelope{}, false, err
	}
	return Envelope{ID: d.ID, Topic: d.Topic, Key: d.Key, Plane: d.Plane,
		Producer: d.Producer, TS: d.TS, Data: d.Data}, true, nil
}

func (m *MongoDocs) Latest(topic, key string) (Envelope, bool, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	filter := bson.M{"topic": topic}
	if key != "" {
		filter["key"] = key
	}
	var d doc
	err := m.coll.FindOne(ctx, filter, options.FindOne().SetSort(bson.M{"ts": -1})).Decode(&d)
	if err == mongo.ErrNoDocuments {
		return Envelope{}, false, nil
	}
	if err != nil {
		return Envelope{}, false, err
	}
	return Envelope{ID: d.ID, Topic: d.Topic, Key: d.Key, Plane: d.Plane,
		Producer: d.Producer, TS: d.TS, Data: d.Data}, true, nil
}

func (m *MongoDocs) Count() (int, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	n, err := m.coll.CountDocuments(ctx, bson.M{})
	return int(n), err
}

func (m *MongoDocs) Close() error {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	return m.client.Disconnect(ctx)
}
