package core

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

// A cloud model the daemon already serves must work without a key of our own:
// `ollama signin` leaves the daemon able to reach it. Every -cloud model used
// to fail outright on a machine with no saved key, whatever the daemon had.
func TestACloudModelTheDaemonServesNeedsNoKey(t *testing.T) {
	const model = "gemma4:31b-cloud"

	daemon := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/tags" {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"models":[{"name":"` + model + `"}]}`))
	}))
	defer daemon.Close()

	noSettings(t)
	t.Setenv("OLLAMA_HOST", daemon.URL)

	if _, err := NewLLM().client(model); err != nil {
		t.Fatalf("a cloud model the daemon serves should not need a key: %v", err)
	}
}

// One nothing can serve must still say so, rather than failing later and deeper.
func TestACloudModelNobodyServesStillFails(t *testing.T) {
	noSettings(t)
	t.Setenv("OLLAMA_HOST", "http://127.0.0.1:1")

	if _, err := NewLLM().client("gemma4:31b-cloud"); err == nil {
		t.Error("a cloud model with no key and no daemon serving it must fail")
	}
}

// noSettings puts the test somewhere with no settings file and no key in the
// environment, so nothing the machine happens to have configured leaks in.
func noSettings(t *testing.T) {
	t.Helper()
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home) // the one os.UserHomeDir reads on Windows
	t.Setenv("OLLAMA_API_KEY", "")
}
