package deploy

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"regexp"
	"strings"
)

// Nothing with a secret in it reaches disk, the console, or an export.
//
// A deployment run reads the customer's own .env files, their git remotes and
// their cloud credentials, and it writes all of that into a record the Studio
// displays and the evidence bundle ships. So everything is redacted on the way
// in — at the store, not at each caller, because one caller that forgets is
// one connection string in a support bundle.

const redacted = "***REDACTED***"

// secretName matches a variable whose value is a secret whatever it contains.
var secretName = regexp.MustCompile(
	`(?i)(SECRET|TOKEN|PASSWORD|PASSWD|PRIVATE_KEY|ACCESS_KEY|API_KEY|MONGODB_URI|DATABASE_URL)`)

// secretValues match a secret by its shape, for the ones nobody named.
var secretValues = []*regexp.Regexp{
	regexp.MustCompile(`AKIA[0-9A-Z]{16}`),
	regexp.MustCompile(`gh[pousr]_[A-Za-z0-9_]{20,}`),
	regexp.MustCompile(`github_pat_[A-Za-z0-9_]{20,}`),
	regexp.MustCompile(`(?s)-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----`),
	regexp.MustCompile(`(?i)mongodb(\+srv)?://[^\s'"<>]+`),
	regexp.MustCompile(`(?i)https?://[^/\s:@]+:[^/\s@]+@[^\s'"<>]+`),
	// Spaces and tabs, never a newline: a pattern that crosses a line
	// boundary swallows whatever follows it, and something else may be
	// reading those lines.
	regexp.MustCompile(`(?i)\bBearer[ \t]+[A-Za-z0-9._~+/=-]{12,}`),
}

// assignment is a `NAME=value` line whose name says the value is a secret.
var assignment = regexp.MustCompile(
	`(?im)^([A-Z][A-Z0-9_]*(SECRET|TOKEN|PASSWORD|URI|KEY)[A-Z0-9_]*\s*=\s*).+$`)

// IsSecretName reports whether a variable's name means its value is a secret.
func IsSecretName(name string) bool { return secretName.MatchString(name) }

// RedactText removes anything that looks like a secret from one string.
func RedactText(value string) string {
	for _, pattern := range secretValues {
		value = pattern.ReplaceAllString(value, redacted)
	}
	return assignment.ReplaceAllString(value, "${1}"+redacted)
}

// Redact walks a value and removes every secret in it: by the key's name where
// there is one, and by the value's shape everywhere else.
func Redact(value any) any {
	switch v := value.(type) {
	case nil:
		return nil
	case string:
		return RedactText(v)
	case map[string]any:
		out := make(map[string]any, len(v))
		for key, item := range v {
			if IsSecretName(key) {
				out[key] = redacted
				continue
			}
			out[key] = Redact(item)
		}
		return out
	case []any:
		out := make([]any, 0, len(v))
		for _, item := range v {
			out = append(out, Redact(item))
		}
		return out
	case []string:
		out := make([]string, 0, len(v))
		for _, item := range v {
			out = append(out, RedactText(item))
		}
		return out
	}

	// Anything else — a struct, a typed slice — is redacted through its own
	// JSON form, so a new field cannot leak by not having been thought about.
	raw, err := json.Marshal(value)
	if err != nil {
		return value
	}
	var tree any
	if err := json.Unmarshal(raw, &tree); err != nil {
		return value
	}
	// Only a composite is worth walking again. A number or a boolean would
	// round-trip to itself for ever.
	switch tree.(type) {
	case map[string]any, []any, string:
		return Redact(tree)
	}
	return tree
}

// SHA256 is how an artifact records what it wrote, and what was there before.
func SHA256(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

// SHA256File is the same for a file that may not exist.
func SHA256File(path string) string {
	body, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return SHA256(body)
}

// SafeJSON is a value serialised with every secret already removed.
func SafeJSON(value any) string {
	body, err := json.Marshal(Redact(value))
	if err != nil {
		return "{}"
	}
	return strings.TrimSpace(string(body))
}
