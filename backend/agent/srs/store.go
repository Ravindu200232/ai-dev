package srs

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"

	"go.mongodb.org/mongo-driver/v2/bson"
	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
	"go.mongodb.org/mongo-driver/v2/mongo/readpref"
)

// Persistence is deliberately small. The whole service asks for equality
// matches on one or two fields and sorts on one key, so the store is eight
// methods over documents. That is what makes the in-memory fallback — needed
// because the app has to run with no mongod at all — a hundred lines instead of
// a database.

// The collections, matching the Python names exactly so an existing database
// keeps working.
const (
	// DatabaseName is the database the Python service used, so an existing
	// installation keeps its projects.
	DatabaseName = "agentforge_srs"

	CollProjects = "projects"
	CollSources  = "extracted_sources"
	CollSessions = "question_sessions"
	CollAnswers  = "answers"
	CollPlans    = "plans"
	CollVersions = "srs_versions"
	CollDiagrams = "diagrams"
	CollEvents   = "agent_events"
	CollTraces   = "prompt_traces"
	CollErrors   = "errors"
)

// indexed lists the collections that get a project_id index.
var indexed = []string{
	CollSources, CollVersions, CollEvents, CollSessions,
	CollAnswers, CollDiagrams, CollTraces, CollErrors,
}

// Doc is one stored record. Everything is JSON-shaped, so a field the service
// does not model survives a read-modify-write.
type Doc = map[string]any

// Store is the whole persistence surface.
type Store interface {
	InsertOne(ctx context.Context, coll string, doc Doc) error
	FindOne(ctx context.Context, coll string, query Doc) (Doc, error)
	Find(ctx context.Context, coll string, query Doc, sortKey string, sortDir, limit int) ([]Doc, error)
	UpdateOne(ctx context.Context, coll string, query, set Doc) (bool, error)
	DeleteMany(ctx context.Context, coll string, query Doc) error
	Ping(ctx context.Context) error
	Close(ctx context.Context) error
	Name() string
}

// --- mongo --------------------------------------------------------------------

type mongoStore struct {
	client *mongo.Client
	db     *mongo.Database
}

// Connect opens MongoDB, falling back to memory when it cannot be reached. The
// service must start either way: a machine with no database can still write a
// specification, it just will not survive a restart.
func Connect(ctx context.Context, uri, dbName string) Store {
	store, err := connectMongo(ctx, uri, dbName)
	if err == nil {
		return store
	}
	return NewMemoryStore()
}

func connectMongo(ctx context.Context, uri, dbName string) (Store, error) {
	client, err := mongo.Connect(options.Client().
		ApplyURI(uri).
		SetServerSelectionTimeout(1500 * time.Millisecond))
	if err != nil {
		return nil, err
	}
	pingCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	if err := client.Ping(pingCtx, readpref.Primary()); err != nil {
		_ = client.Disconnect(context.Background())
		return nil, err
	}
	s := &mongoStore{client: client, db: client.Database(dbName)}
	s.ensureIndexes(ctx)
	return s, nil
}

func (s *mongoStore) Name() string { return "mongodb" }

// ensureIndexes is best effort: an index that cannot be created is not a reason
// to refuse to run.
func (s *mongoStore) ensureIndexes(ctx context.Context) {
	unique := options.Index().SetUnique(true)
	_, _ = s.db.Collection(CollProjects).Indexes().CreateOne(ctx,
		mongo.IndexModel{Keys: bson.D{{Key: "id", Value: 1}}, Options: unique})
	for _, coll := range indexed {
		_, _ = s.db.Collection(coll).Indexes().CreateOne(ctx,
			mongo.IndexModel{Keys: bson.D{{Key: "project_id", Value: 1}}})
	}
}

func (s *mongoStore) InsertOne(ctx context.Context, coll string, doc Doc) error {
	_, err := s.db.Collection(coll).InsertOne(ctx, doc)
	return err
}

func (s *mongoStore) FindOne(ctx context.Context, coll string, query Doc) (Doc, error) {
	var out Doc
	err := s.db.Collection(coll).FindOne(ctx, filter(query)).Decode(&out)
	if err == mongo.ErrNoDocuments {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return stripOID(out), nil
}

func (s *mongoStore) Find(ctx context.Context, coll string, query Doc, sortKey string, sortDir, limit int) ([]Doc, error) {
	opts := options.Find()
	if sortKey != "" {
		opts.SetSort(bson.D{{Key: sortKey, Value: sortDir}})
	}
	if limit > 0 {
		opts.SetLimit(int64(limit))
	} else {
		// The Python store had a hard 1000-document ceiling; keep it, so a
		// runaway collection cannot pull the whole database into memory.
		opts.SetLimit(1000)
	}
	cursor, err := s.db.Collection(coll).Find(ctx, filter(query), opts)
	if err != nil {
		return nil, err
	}
	var raw []Doc
	if err := cursor.All(ctx, &raw); err != nil {
		return nil, err
	}
	for i := range raw {
		raw[i] = stripOID(raw[i])
	}
	return raw, nil
}

func (s *mongoStore) UpdateOne(ctx context.Context, coll string, query, set Doc) (bool, error) {
	res, err := s.db.Collection(coll).UpdateOne(ctx, filter(query), bson.M{"$set": set})
	if err != nil {
		return false, err
	}
	return res.ModifiedCount > 0, nil
}

func (s *mongoStore) DeleteMany(ctx context.Context, coll string, query Doc) error {
	_, err := s.db.Collection(coll).DeleteMany(ctx, filter(query))
	return err
}

func (s *mongoStore) Ping(ctx context.Context) error {
	return s.client.Ping(ctx, readpref.Primary())
}

func (s *mongoStore) Close(ctx context.Context) error {
	return s.client.Disconnect(ctx)
}

func filter(query Doc) bson.M {
	if query == nil {
		return bson.M{}
	}
	return bson.M(query)
}

// stripOID drops Mongo's own key so a document round-trips as plain JSON.
func stripOID(doc Doc) Doc {
	delete(doc, "_id")
	return doc
}

// --- memory -------------------------------------------------------------------

// memoryStore keeps documents in process. It supports exactly the query surface
// the service uses: equality, and `$in`.
type memoryStore struct {
	mu   sync.RWMutex
	data map[string][]Doc
}

func NewMemoryStore() Store {
	return &memoryStore{data: map[string][]Doc{}}
}

func (s *memoryStore) Name() string { return "memory" }

func (s *memoryStore) InsertOne(_ context.Context, coll string, doc Doc) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.data[coll] = append(s.data[coll], clone(doc))
	return nil
}

func (s *memoryStore) FindOne(_ context.Context, coll string, query Doc) (Doc, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	for _, doc := range s.data[coll] {
		if matches(doc, query) {
			return clone(doc), nil
		}
	}
	return nil, nil
}

func (s *memoryStore) Find(_ context.Context, coll string, query Doc, sortKey string, sortDir, limit int) ([]Doc, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	var out []Doc
	for _, doc := range s.data[coll] {
		if matches(doc, query) {
			out = append(out, clone(doc))
		}
	}
	if sortKey != "" {
		sort.SliceStable(out, func(i, j int) bool {
			less := compare(out[i][sortKey], out[j][sortKey]) < 0
			if sortDir < 0 {
				return !less && compare(out[i][sortKey], out[j][sortKey]) != 0
			}
			return less
		})
	}
	if limit > 0 && len(out) > limit {
		out = out[:limit]
	}
	return out, nil
}

func (s *memoryStore) UpdateOne(_ context.Context, coll string, query, set Doc) (bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, doc := range s.data[coll] {
		if matches(doc, query) {
			for k, v := range set {
				doc[k] = v
			}
			return true, nil
		}
	}
	return false, nil
}

func (s *memoryStore) DeleteMany(_ context.Context, coll string, query Doc) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	kept := s.data[coll][:0]
	for _, doc := range s.data[coll] {
		if !matches(doc, query) {
			kept = append(kept, doc)
		}
	}
	s.data[coll] = kept
	return nil
}

func (s *memoryStore) Ping(context.Context) error  { return nil }
func (s *memoryStore) Close(context.Context) error { return nil }

// matches is the whole query language: equality, and `$in`.
func matches(doc, query Doc) bool {
	for key, want := range query {
		got := doc[key]
		if spec, ok := want.(Doc); ok {
			list, has := spec["$in"].([]any)
			if !has {
				return false
			}
			found := false
			for _, candidate := range list {
				if equal(got, candidate) {
					found = true
					break
				}
			}
			if !found {
				return false
			}
			continue
		}
		if !equal(got, want) {
			return false
		}
	}
	return true
}

func equal(a, b any) bool {
	if a == nil || b == nil {
		return a == nil && b == nil
	}
	return fmt.Sprint(a) == fmt.Sprint(b)
}

// compare orders the two kinds of sort key the service uses: ISO timestamps
// (strings) and version numbers (ints).
func compare(a, b any) int {
	af, aNum := asFloat(a)
	bf, bNum := asFloat(b)
	if aNum && bNum {
		switch {
		case af < bf:
			return -1
		case af > bf:
			return 1
		}
		return 0
	}
	return strings.Compare(fmt.Sprint(a), fmt.Sprint(b))
}

func asFloat(v any) (float64, bool) {
	switch n := v.(type) {
	case int:
		return float64(n), true
	case int32:
		return float64(n), true
	case int64:
		return float64(n), true
	case float64:
		return n, true
	case float32:
		return float64(n), true
	}
	return 0, false
}

// clone keeps a caller from mutating what is stored.
func clone(doc Doc) Doc {
	raw, err := json.Marshal(doc)
	if err != nil {
		return doc
	}
	var out Doc
	if err := json.Unmarshal(raw, &out); err != nil {
		return doc
	}
	return out
}

// --- typed conversion ----------------------------------------------------------

// toDoc turns a typed value into a stored document, keeping the JSON names.
func toDoc(value any) (Doc, error) {
	raw, err := json.Marshal(value)
	if err != nil {
		return nil, err
	}
	var out Doc
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, err
	}
	return out, nil
}

// fromDoc reads a stored document back into a typed value.
func fromDoc(doc Doc, into any) error {
	if doc == nil {
		return nil
	}
	raw, err := json.Marshal(doc)
	if err != nil {
		return err
	}
	return json.Unmarshal(raw, into)
}

// --- repository ----------------------------------------------------------------
//
// Every query the service makes, in one place. There are no aggregations, no
// projections and no multi-field matches — which is why the memory store above
// can be a faithful stand-in.

// Repo is the typed API the rest of the package uses.
type Repo struct{ store Store }

func NewRepo(store Store) *Repo { return &Repo{store: store} }

func (r *Repo) Store() Store { return r.store }

// --- projects ---

func (r *Repo) CreateProject(ctx context.Context, p Project) error {
	doc, err := toDoc(p)
	if err != nil {
		return err
	}
	return r.store.InsertOne(ctx, CollProjects, doc)
}

func (r *Repo) GetProject(ctx context.Context, id string) (*Project, error) {
	doc, err := r.store.FindOne(ctx, CollProjects, Doc{"id": id})
	if err != nil || doc == nil {
		return nil, err
	}
	var p Project
	if err := fromDoc(doc, &p); err != nil {
		return nil, err
	}
	return &p, nil
}

// UpdateProject writes the named fields and stamps updated_at.
func (r *Repo) UpdateProject(ctx context.Context, id string, set Doc) error {
	set["updated_at"] = NowISO()
	_, err := r.store.UpdateOne(ctx, CollProjects, Doc{"id": id}, set)
	return err
}

func (r *Repo) ListProjects(ctx context.Context) ([]Project, error) {
	docs, err := r.store.Find(ctx, CollProjects, nil, "created_at", -1, 100)
	if err != nil {
		return nil, err
	}
	out := make([]Project, 0, len(docs))
	for _, doc := range docs {
		var p Project
		if fromDoc(doc, &p) == nil {
			out = append(out, p)
		}
	}
	return out, nil
}

// --- attachments ---

// Source is one thing the customer attached, already read into text.
type Source struct {
	ID        string         `json:"id"`
	ProjectID string         `json:"project_id"`
	Mode      string         `json:"mode"`
	Filename  string         `json:"filename,omitempty"`
	Text      string         `json:"text"`
	Meta      map[string]any `json:"meta"`
	CreatedAt string         `json:"created_at"`
}

func (r *Repo) AddSource(ctx context.Context, s Source) (Source, error) {
	if s.ID == "" {
		s.ID = NewID("src_")
	}
	s.CreatedAt = NowISO()
	doc, err := toDoc(s)
	if err != nil {
		return s, err
	}
	return s, r.store.InsertOne(ctx, CollSources, doc)
}

func (r *Repo) ListSources(ctx context.Context, projectID string) ([]Source, error) {
	docs, err := r.store.Find(ctx, CollSources, Doc{"project_id": projectID}, "created_at", 1, 0)
	if err != nil {
		return nil, err
	}
	out := make([]Source, 0, len(docs))
	for _, doc := range docs {
		var s Source
		if fromDoc(doc, &s) == nil {
			out = append(out, s)
		}
	}
	return out, nil
}

// --- interview ---

// SaveSession upserts the one session a project has. Mongo's own upsert is not
// used because the memory store would then need to implement it too.
func (r *Repo) SaveSession(ctx context.Context, s Session) error {
	if s.ID == "" {
		s.ID = NewID("qs_")
	}
	if s.CreatedAt == "" {
		s.CreatedAt = NowISO()
	}
	doc, err := toDoc(s)
	if err != nil {
		return err
	}
	existing, err := r.store.FindOne(ctx, CollSessions, Doc{"project_id": s.ProjectID})
	if err != nil {
		return err
	}
	if existing == nil {
		return r.store.InsertOne(ctx, CollSessions, doc)
	}
	_, err = r.store.UpdateOne(ctx, CollSessions, Doc{"project_id": s.ProjectID}, doc)
	return err
}

func (r *Repo) GetSession(ctx context.Context, projectID string) (*Session, error) {
	doc, err := r.store.FindOne(ctx, CollSessions, Doc{"project_id": projectID})
	if err != nil || doc == nil {
		return nil, err
	}
	var s Session
	if err := fromDoc(doc, &s); err != nil {
		return nil, err
	}
	return &s, nil
}

// SaveAnswers keeps the whole answer list in one document per project.
func (r *Repo) SaveAnswers(ctx context.Context, projectID string, answers []Answer) error {
	payload := Doc{"project_id": projectID, "answers": answers, "updated_at": NowISO()}
	doc, err := toDoc(payload)
	if err != nil {
		return err
	}
	existing, err := r.store.FindOne(ctx, CollAnswers, Doc{"project_id": projectID})
	if err != nil {
		return err
	}
	if existing == nil {
		doc["id"] = NewID("ans_")
		doc["created_at"] = NowISO()
		return r.store.InsertOne(ctx, CollAnswers, doc)
	}
	_, err = r.store.UpdateOne(ctx, CollAnswers, Doc{"project_id": projectID}, doc)
	return err
}

func (r *Repo) GetAnswers(ctx context.Context, projectID string) ([]Answer, error) {
	doc, err := r.store.FindOne(ctx, CollAnswers, Doc{"project_id": projectID})
	if err != nil || doc == nil {
		return nil, err
	}
	var wrapper struct {
		Answers []Answer `json:"answers"`
	}
	if err := fromDoc(doc, &wrapper); err != nil {
		return nil, err
	}
	return wrapper.Answers, nil
}

// --- plans (append-only, versioned) ---

func (r *Repo) SavePlan(ctx context.Context, p PlanRecordDoc) (PlanRecordDoc, error) {
	if p.ID == "" {
		p.ID = NewID("pln_")
	}
	p.CreatedAt = NowISO()
	doc, err := toDoc(p)
	if err != nil {
		return p, err
	}
	return p, r.store.InsertOne(ctx, CollPlans, doc)
}

func (r *Repo) ListPlans(ctx context.Context, projectID string) ([]PlanRecordDoc, error) {
	docs, err := r.store.Find(ctx, CollPlans, Doc{"project_id": projectID}, "version", 1, 0)
	if err != nil {
		return nil, err
	}
	out := make([]PlanRecordDoc, 0, len(docs))
	for _, doc := range docs {
		var p PlanRecordDoc
		if fromDoc(doc, &p) == nil {
			out = append(out, p)
		}
	}
	return out, nil
}

func (r *Repo) LatestPlan(ctx context.Context, projectID string) (*PlanRecordDoc, error) {
	docs, err := r.store.Find(ctx, CollPlans, Doc{"project_id": projectID}, "version", -1, 1)
	if err != nil || len(docs) == 0 {
		return nil, err
	}
	var p PlanRecordDoc
	if err := fromDoc(docs[0], &p); err != nil {
		return nil, err
	}
	return &p, nil
}

func (r *Repo) UpdatePlan(ctx context.Context, id string, set Doc) error {
	_, err := r.store.UpdateOne(ctx, CollPlans, Doc{"id": id}, set)
	return err
}

// --- srs versions (append-only) ---

// Version is one stored specification.
type Version struct {
	ID          string   `json:"id"`
	ProjectID   string   `json:"project_id"`
	Version     string   `json:"version"`
	Label       string   `json:"label,omitempty"`
	SRS         Envelope `json:"srs"`
	DiffSummary []string `json:"diff_summary"`
	CreatedAt   string   `json:"created_at"`
}

func (r *Repo) SaveVersion(ctx context.Context, v Version) (Version, error) {
	if v.ID == "" {
		v.ID = NewID("ver_")
	}
	v.CreatedAt = NowISO()
	doc, err := toDoc(v)
	if err != nil {
		return v, err
	}
	return v, r.store.InsertOne(ctx, CollVersions, doc)
}

func (r *Repo) ListVersions(ctx context.Context, projectID string) ([]Version, error) {
	docs, err := r.store.Find(ctx, CollVersions, Doc{"project_id": projectID}, "created_at", 1, 0)
	if err != nil {
		return nil, err
	}
	out := make([]Version, 0, len(docs))
	for _, doc := range docs {
		var v Version
		if fromDoc(doc, &v) == nil {
			out = append(out, v)
		}
	}
	return out, nil
}

// LatestVersion is the most recently written one, by time — not by version
// number, which is what the Python did and what a customization relies on.
func (r *Repo) LatestVersion(ctx context.Context, projectID string) (*Version, error) {
	docs, err := r.store.Find(ctx, CollVersions, Doc{"project_id": projectID}, "created_at", -1, 1)
	if err != nil || len(docs) == 0 {
		return nil, err
	}
	var v Version
	if err := fromDoc(docs[0], &v); err != nil {
		return nil, err
	}
	return &v, nil
}

// --- diagrams (full replace) ---

func (r *Repo) SaveDiagrams(ctx context.Context, projectID string, diagrams []Diagram) error {
	if err := r.store.DeleteMany(ctx, CollDiagrams, Doc{"project_id": projectID}); err != nil {
		return err
	}
	for _, d := range diagrams {
		doc, err := toDoc(d)
		if err != nil {
			continue
		}
		doc["project_id"] = projectID
		if doc["id"] == nil || doc["id"] == "" {
			doc["id"] = NewID("dia_")
		}
		if err := r.store.InsertOne(ctx, CollDiagrams, doc); err != nil {
			return err
		}
	}
	return nil
}

// DiscardSpecification removes a written specification and its diagrams.
//
// Discarding is meant to remove it. A customer throwing a draft away because
// of something in it that should never have been written down has not thrown
// it away while every endpoint still serves the whole document. The interview
// and the plans stay: the flow restarts from them.
func (r *Repo) DiscardSpecification(ctx context.Context, projectID string) error {
	if err := r.store.DeleteMany(ctx, CollVersions, Doc{"project_id": projectID}); err != nil {
		return err
	}
	return r.store.DeleteMany(ctx, CollDiagrams, Doc{"project_id": projectID})
}

func (r *Repo) ListDiagrams(ctx context.Context, projectID string) ([]Diagram, error) {
	docs, err := r.store.Find(ctx, CollDiagrams, Doc{"project_id": projectID}, "", 0, 0)
	if err != nil {
		return nil, err
	}
	out := make([]Diagram, 0, len(docs))
	for _, doc := range docs {
		var d Diagram
		if fromDoc(doc, &d) == nil {
			out = append(out, d)
		}
	}
	return out, nil
}

// --- events, traces, errors ---

// Event is one line of what the agent did, shown in the Studio's console.
type Event struct {
	ID        string         `json:"id"`
	ProjectID string         `json:"project_id"`
	Agent     string         `json:"agent"`
	Channel   string         `json:"channel"`
	Level     string         `json:"level"`
	Message   string         `json:"message"`
	Progress  *float64       `json:"progress"`
	Data      map[string]any `json:"data"`
	CreatedAt string         `json:"created_at"`
}

func (r *Repo) AddEvent(ctx context.Context, e Event) error {
	if e.ID == "" {
		e.ID = NewID("evt_")
	}
	e.CreatedAt = NowISO()
	doc, err := toDoc(e)
	if err != nil {
		return err
	}
	return r.store.InsertOne(ctx, CollEvents, doc)
}

func (r *Repo) ListEvents(ctx context.Context, projectID string, limit int) ([]Event, error) {
	docs, err := r.store.Find(ctx, CollEvents, Doc{"project_id": projectID}, "created_at", 1, limit)
	if err != nil {
		return nil, err
	}
	out := make([]Event, 0, len(docs))
	for _, doc := range docs {
		var e Event
		if fromDoc(doc, &e) == nil {
			out = append(out, e)
		}
	}
	return out, nil
}

// AddRecord appends to prompt_traces or errors — both are plain append-only logs.
func (r *Repo) AddRecord(ctx context.Context, coll, projectID string, payload Doc) error {
	doc := clone(payload)
	doc["project_id"] = projectID
	doc["created_at"] = NowISO()
	if doc["id"] == nil {
		doc["id"] = NewID(map[string]string{CollTraces: "trc_", CollErrors: "err_"}[coll])
	}
	return r.store.InsertOne(ctx, coll, doc)
}

func (r *Repo) ListRecords(ctx context.Context, coll, projectID string) ([]Doc, error) {
	return r.store.Find(ctx, coll, Doc{"project_id": projectID}, "created_at", 1, 0)
}
