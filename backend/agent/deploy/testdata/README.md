# Golden deployment artifacts

Every file here is what the Python deployment agent generated for the fixture
project in `intake_test.go`, captured before it was removed. `generate_test.go`
regenerates them and compares byte for byte.

Two files are deliberately not what Python produced:

- `deployment-report.md` — Python's `textwrap.dedent` had nothing to strip once
  a multi-line value was interpolated into it, so every line came out indented
  twelve spaces and the Markdown did not render. The ECS report also described
  itself as a Vercel deployment, because the report only asked whether the
  target was EC2.
- `deploy/release.sh` — the allow-list is the same JSON without the spaces
  `json.dumps` puts after each comma. `jq` reads both.

`infra/bootstrap.yml` is not pinned here. It is `assets/bootstrap-*.yml` with
one value filled in, so pinning a copy would only pin the copy;
`TestBootstrapTemplates` checks the substitution instead.

To change a generated file on purpose: make the change, run
`go test ./deploy -run Golden -update`, and read the diff before committing.
