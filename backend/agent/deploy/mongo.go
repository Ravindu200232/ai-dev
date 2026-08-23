package deploy

import (
	"context"
	"net/url"
	"sort"
	"strings"
	"time"

	"go.mongodb.org/mongo-driver/v2/mongo"
	"go.mongodb.org/mongo-driver/v2/mongo/options"
)

// Checking the customer's database before the deployment commits to it.
//
// This is the single most common thing that goes wrong: the connection string
// is right but Atlas has not been told to let this machine in, or it carries
// an option the Node driver rejects at build time even though a check that
// only connects would pass. So the check does both — it reads the URI the way
// the app's own driver will, and then it actually connects.

// knownOptions are the connection options the MongoDB drivers accept. Anything
// else fails the app's build, so it fails here, where there is someone to tell.
var knownOptions = map[string]bool{
	"appname": true, "authmechanism": true, "authmechanismproperties": true, "authsource": true,
	"compressors": true, "connecttimeoutms": true, "directconnection": true,
	"heartbeatfrequencyms": true, "journal": true, "loadbalanced": true, "localthresholdms": true,
	"maxconnecting": true, "maxidletimems": true, "maxpoolsize": true, "maxstalenessseconds": true,
	"minpoolsize": true, "proxyhost": true, "proxypassword": true, "proxyport": true,
	"proxyusername": true, "readconcernlevel": true, "readpreference": true,
	"readpreferencetags": true, "replicaset": true, "retryreads": true, "retrywrites": true,
	"servermonitoringmode": true, "serverselectiontimeoutms": true, "serverselectiontryonce": true,
	"sockettimeoutms": true, "srvmaxhosts": true, "srvservicename": true, "ssl": true,
	"timeoutms": true, "tls": true, "tlsallowinvalidcertificates": true,
	"tlsallowinvalidhostnames": true, "tlscafile": true, "tlscertificatekeyfile": true,
	"tlscertificatekeyfilepassword": true, "tlsdisablecertificaterevocationcheck": true,
	"tlsdisableocspendpointcheck": true, "tlsinsecure": true, "uuidrepresentation": true,
	"w": true, "waitqueuetimeoutms": true, "wtimeoutms": true, "zlibcompressionlevel": true,
}

// The reasons a check can end, as the Studio's database panel reads them.
const (
	MongoConnected   = "connected"
	MongoMalformed   = "malformed"
	MongoAuth        = "auth"
	MongoDNS         = "dns"
	MongoUnreachable = "unreachable"
	MongoFailed      = "failed"
)

// MongoCheck is what the deployment would actually get.
type MongoCheck struct {
	OK             bool     `json:"ok"`
	Code           string   `json:"code"`
	Message        string   `json:"message"`
	Detail         string   `json:"detail,omitempty"`
	ServerVersion  string   `json:"server_version,omitempty"`
	Database       string   `json:"database,omitempty"`
	DatabaseInURI  bool     `json:"database_in_uri,omitempty"`
	Databases      []string `json:"databases,omitempty"`
	UnknownOptions []string `json:"unknown_options,omitempty"`
}

// CheckMongo connects, pings, and reports what it found.
func CheckMongo(ctx context.Context, uri string) MongoCheck {
	uri = strings.TrimSpace(uri)
	if uri == "" || strings.ContainsAny(uri, " \t\n\r") {
		return MongoCheck{Code: MongoMalformed,
			Message: "The connection string is empty or contains whitespace."}
	}
	parsed, err := url.Parse(uri)
	if err != nil {
		return MongoCheck{Code: MongoMalformed,
			Message: "The connection string could not be parsed."}
	}
	if (parsed.Scheme != "mongodb" && parsed.Scheme != "mongodb+srv") || parsed.Hostname() == "" {
		return MongoCheck{Code: MongoMalformed,
			Message: "The URI must start with mongodb:// or mongodb+srv:// and include a host."}
	}

	if unknown := unknownOptions(parsed); len(unknown) > 0 {
		return MongoCheck{
			Code: MongoMalformed,
			Message: "The URI has an option MongoDB does not support: " +
				strings.Join(unknown, ", ") + ". Copy the connection string from Atlas again — " +
				"the app's driver rejects unknown options at build time, even though this " +
				"check could otherwise connect.",
			UnknownOptions: unknown,
		}
	}

	database := strings.TrimPrefix(parsed.Path, "/")
	ctx, cancel := context.WithTimeout(ctx, 8*time.Second)
	defer cancel()

	client, err := mongo.Connect(options.Client().ApplyURI(uri).
		SetServerSelectionTimeout(6 * time.Second).
		SetConnectTimeout(6 * time.Second).
		SetAppName("deployment-agent-check"))
	if err != nil {
		return mongoFailure(err, database)
	}
	defer func() { _ = client.Disconnect(context.Background()) }()

	if err := client.Ping(ctx, nil); err != nil {
		return mongoFailure(err, database)
	}

	check := MongoCheck{
		OK: true, Code: MongoConnected,
		Database: database, DatabaseInURI: database != "",
		Message: "Connected successfully.",
	}
	if database == "" {
		check.Message = "Connected, but the URI names no database — everything would be " +
			"written to `test`. Add the database to the end of the host, before the ?options."
	}
	if names, err := client.ListDatabaseNames(ctx, map[string]any{}); err == nil {
		sort.Strings(names)
		check.Databases = names
	}
	return check
}

// unknownOptions are the query parameters no driver knows.
func unknownOptions(parsed *url.URL) []string {
	unknown := []string{}
	for name := range parsed.Query() {
		if !knownOptions[strings.ToLower(name)] {
			unknown = append(unknown, name)
		}
	}
	sort.Strings(unknown)
	return unknown
}

// authHints and unreachableHints turn a driver error into the one sentence
// that tells the customer what to do about it.
var (
	authHints        = []string{"auth failed", "authentication failed", "bad auth"}
	unreachableHints = []string{"timed out", "no replica set members", "connection refused",
		"server selection error"}
	dnsHints = []string{"nodename nor servname", "name or service not known", "dns",
		"no such host"}
)

func mongoFailure(err error, database string) MongoCheck {
	text := err.Error()
	lowered := strings.ToLower(text)
	check := MongoCheck{Code: MongoFailed, Message: "Connection failed.",
		Detail: clip(RedactText(text), 400), Database: database}

	switch {
	case matchesAny(lowered, authHints):
		check.Code = MongoAuth
		check.Message = "Authentication failed. Check the username and password in the URI."
	case matchesAny(lowered, dnsHints):
		check.Code = MongoDNS
		check.Message = "The cluster hostname could not be resolved. Check the cluster address."
	case matchesAny(lowered, unreachableHints):
		check.Code = MongoUnreachable
		check.Message = "Could not reach the cluster. The most common cause is Atlas Network " +
			"Access: add this machine's IP, and after deployment add the EC2 public IP."
	}
	return check
}

func matchesAny(text string, hints []string) bool {
	for _, hint := range hints {
		if strings.Contains(text, hint) {
			return true
		}
	}
	return false
}
