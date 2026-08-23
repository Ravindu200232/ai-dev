package deploy

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// fakeAWS puts a script called "aws" on PATH that records how it was called and
// prints whatever the test wants back. It is the only way to check the shape of
// a command without an account to run it against.
func fakeAWS(t *testing.T, body string) (dir string, calls func() []string) {
	t.Helper()
	dir = t.TempDir()
	log := filepath.Join(dir, "calls.log")

	script := "#!/bin/sh\n" +
		"{ printf '%s\\n' \"$*\"; } >> " + log + "\n" +
		"cat >> " + filepath.Join(dir, "stdin.log") + "\n" +
		body + "\n"
	if err := os.WriteFile(filepath.Join(dir, "aws"), []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))

	return dir, func() []string {
		raw, err := os.ReadFile(log)
		if err != nil {
			return nil
		}
		out := []string{}
		for _, line := range strings.Split(strings.TrimSpace(string(raw)), "\n") {
			if strings.TrimSpace(line) != "" {
				out = append(out, line)
			}
		}
		return out
	}
}

func TestPutSecretNeverPutsAValueInAnArgument(t *testing.T) {
	dir, calls := fakeAWS(t, "echo '{}'")
	aws := AWS{Region: DefaultRegion}

	secret := "mongodb+srv://user:hunter2@cluster.mongodb.net/shop"
	err := aws.PutSecret(context.Background(), "arn:aws:secretsmanager:x:y:secret:z",
		map[string]string{"MONGODB_URI": secret, "BETTER_AUTH_SECRET": "s3cr3t"})
	if err != nil {
		t.Fatal(err)
	}

	made := calls()
	if len(made) != 1 {
		t.Fatalf("calls = %v", made)
	}
	if strings.Contains(made[0], "hunter2") || strings.Contains(made[0], "s3cr3t") {
		t.Errorf("a secret was passed as an argument: %s", made[0])
	}
	if !strings.Contains(made[0], "--secret-string file:///dev/stdin") {
		t.Errorf("the secret did not go over stdin: %s", made[0])
	}
	// It really did arrive, on the input.
	body, err := os.ReadFile(filepath.Join(dir, "stdin.log"))
	if err != nil || !strings.Contains(string(body), "hunter2") {
		t.Errorf("stdin = %q %v", body, err)
	}
}

func TestPutSecretReportsAFailure(t *testing.T) {
	_, _ = fakeAWS(t, "echo 'AccessDeniedException' >&2; exit 254")
	err := (AWS{Region: DefaultRegion}).PutSecret(context.Background(), "arn", map[string]string{"A": "b"})
	if err == nil || !strings.Contains(err.Error(), "put-secret-value") {
		t.Fatalf("err = %v", err)
	}
	if strings.Contains(err.Error(), "file:///dev/stdin") {
		t.Errorf("the error quotes the invocation: %v", err)
	}
}

func TestRegistryIsEmptiedInBatchesTheAPIAccepts(t *testing.T) {
	// 250 images: three delete calls, none over the cap.
	ids := make([]string, 0, 250)
	for i := 0; i < 250; i++ {
		ids = append(ids, `{"imageDigest":"sha256:`+strings.Repeat("a", 60)+itoa(i)+`"}`)
	}
	_, calls := fakeAWS(t, `case "$*" in
  *list-images*) echo '{"imageIds":[`+strings.Join(ids, ",")+`]}' ;;
  *) echo '{}' ;;
esac`)

	if err := emptyRegistry(context.Background(), AWS{Region: DefaultRegion}, "shop"); err != nil {
		t.Fatal(err)
	}
	deletes := 0
	for _, call := range calls() {
		if !strings.Contains(call, "batch-delete-image") {
			continue
		}
		deletes++
		if count := strings.Count(call, "imageDigest"); count > imageBatch {
			t.Errorf("one call carried %d images, the cap is %d", count, imageBatch)
		}
	}
	if deletes != 3 {
		t.Errorf("deletes = %d, want 3", deletes)
	}
}

func TestAnEmptyRegistryIsNotDeletedFrom(t *testing.T) {
	_, calls := fakeAWS(t, `case "$*" in
  *list-images*) echo '{"imageIds":[]}' ;;
  *) echo '{}' ;;
esac`)
	if err := emptyRegistry(context.Background(), AWS{Region: DefaultRegion}, "shop"); err != nil {
		t.Fatal(err)
	}
	for _, call := range calls() {
		if strings.Contains(call, "batch-delete-image") {
			t.Errorf("nothing to delete, but it asked: %s", call)
		}
	}
}

func TestATimedOutWaitSaysSo(t *testing.T) {
	_, _ = fakeAWS(t, "echo '{}'")
	aws := AWS{Region: DefaultRegion}

	timedOut := Output{Code: 124, TimedOut: true}
	err := aws.stackFailure(context.Background(), "shop-bootstrap", timedOut, time.Now())
	if err == nil || !strings.Contains(err.Error(), "did not finish within") {
		t.Fatalf("err = %v", err)
	}
}

func TestAStackFailureIgnoresAnOlderOperation(t *testing.T) {
	old := time.Now().UTC().Add(-48 * time.Hour).Format(time.RFC3339Nano)
	recent := time.Now().UTC().Format(time.RFC3339Nano)
	_, _ = fakeAWS(t, `case "$*" in
  *describe-stack-events*) echo '{"StackEvents":[
     {"Timestamp":"`+recent+`","LogicalResourceId":"AppInstance","ResourceStatus":"CREATE_IN_PROGRESS","ResourceStatusReason":""},
     {"Timestamp":"`+old+`","LogicalResourceId":"OldBucket","ResourceStatus":"CREATE_FAILED","ResourceStatusReason":"the bucket already exists"}]}' ;;
  *) echo '{}' ;;
esac`)

	aws := AWS{Region: DefaultRegion}
	since := time.Now().UTC().Add(-time.Minute)
	err := aws.stackFailure(context.Background(), "shop-bootstrap", Output{Code: 255}, since)
	if err == nil {
		t.Fatal("a failed wait is an error")
	}
	if strings.Contains(err.Error(), "OldBucket") {
		t.Errorf("a failure from two days ago was blamed: %v", err)
	}
}

func TestAStackFailureReportsTheCurrentOne(t *testing.T) {
	recent := time.Now().UTC().Format(time.RFC3339Nano)
	_, _ = fakeAWS(t, `case "$*" in
  *describe-stack-events*) echo '{"StackEvents":[
     {"Timestamp":"`+recent+`","LogicalResourceId":"AppInstance","ResourceStatus":"CREATE_FAILED","ResourceStatusReason":"instance type not available"}]}' ;;
  *) echo '{}' ;;
esac`)

	err := (AWS{Region: DefaultRegion}).stackFailure(context.Background(), "shop-bootstrap",
		Output{Code: 255}, time.Now().UTC().Add(-time.Minute))
	if err == nil || !strings.Contains(err.Error(), "AppInstance") ||
		!strings.Contains(err.Error(), "instance type not available") {
		t.Fatalf("err = %v", err)
	}
}

func TestAStackFailureStillReportsWhenTheClocksDisagree(t *testing.T) {
	recent := time.Now().UTC().Format(time.RFC3339Nano)
	_, _ = fakeAWS(t, `case "$*" in
  *describe-stack-events*) echo '{"StackEvents":[
     {"Timestamp":"`+recent+`","LogicalResourceId":"AppInstance","ResourceStatus":"CREATE_FAILED","ResourceStatusReason":"instance type not available"}]}' ;;
  *) echo '{}' ;;
esac`)

	// This machine's clock runs an hour ahead of AWS, so every event of the
	// operation looks old. Losing the diagnosis to a wrong clock is worse than
	// reporting one that might be from an earlier attempt.
	ahead := time.Now().UTC().Add(time.Hour)
	err := (AWS{Region: DefaultRegion}).stackFailure(context.Background(), "shop-bootstrap",
		Output{Code: 255}, ahead)
	if err == nil || !strings.Contains(err.Error(), "instance type not available") {
		t.Fatalf("err = %v", err)
	}
}

func TestTheSecretNeverBecomesAWindowsPathThatDoesNotExist(t *testing.T) {
	// Windows has no /dev/stdin. Passing it anyway is how every deployment
	// from a Windows machine used to stop at the secrets step.
	parameter, stdin, cleanup, err := secretParameter("windows", `{"MONGODB_URI":"mongodb://x"}`)
	if err != nil {
		t.Fatal(err)
	}
	defer cleanup()
	if strings.Contains(parameter, "/dev/stdin") || !strings.HasPrefix(parameter, "file://") {
		t.Fatalf("parameter = %q", parameter)
	}
	if stdin != "" {
		t.Errorf("nothing goes on standard input there: %q", stdin)
	}
	path := strings.TrimPrefix(parameter, "file://")
	body, err := os.ReadFile(path)
	if err != nil || !strings.Contains(string(body), "mongodb://x") {
		t.Fatalf("the bundle was not written: %v %q", err, body)
	}
	cleanup()
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Errorf("the file outlived the command: %v", err)
	}
}

func TestOnUnixTheSecretGoesOnStandardInput(t *testing.T) {
	parameter, stdin, cleanup, err := secretParameter("linux", `{"A":"b"}`)
	defer cleanup()
	if err != nil || parameter != "file:///dev/stdin" || stdin != `{"A":"b"}` {
		t.Fatalf("parameter = %q stdin = %q err = %v", parameter, stdin, err)
	}
}
