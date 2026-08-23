package deploy

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"sync"
	"time"
)

// Signing in to AWS from inside the Studio.
//
// This is the IAM Identity Center device flow: the agent registers itself, the
// customer opens a URL and approves it, and the agent exchanges the result for
// credentials for one account and one role. Every call is unauthenticated by
// design until the last one, which is why none of it needs a signed request.
//
// What comes back never reaches disk. It goes into a vault that holds it for
// an hour, hands it to one deployment as environment variables, and forgets it.

const (
	ssoClientName = "deployment-agent"
	ssoClientType = "public"
	ssoScope      = "sso:account:access"

	// flowTTL is how long an unfinished sign-in is kept.
	flowTTL = 15 * time.Minute
	// vaultTTL is how long credentials live once they arrive.
	vaultTTL = time.Hour
)

// --- the vault ------------------------------------------------------------------------------

// bundle is one set of credentials, and when it arrived.
type bundle struct {
	values  map[string]string
	created time.Time
}

// Vault holds short-lived credentials in memory and nowhere else. It has no
// serialisation, no file, and no way to list what it holds: a reference is the
// only way to get anything back out.
type Vault struct {
	mu    sync.Mutex
	items map[string]bundle
}

// NewVault is the process's one credential store.
func NewVault() *Vault { return &Vault{items: map[string]bundle{}} }

// Put stores credentials and returns the reference they are known by.
func (v *Vault) Put(values map[string]string) string {
	reference := "vault_" + randomID()
	kept := map[string]string{}
	for key, value := range values {
		if value != "" {
			kept[key] = value
		}
	}
	v.mu.Lock()
	defer v.mu.Unlock()
	v.prune()
	v.items[reference] = bundle{values: kept, created: time.Now()}
	return reference
}

// Get is the credentials behind a reference, or the reason there are none.
func (v *Vault) Get(reference string) (map[string]string, error) {
	v.mu.Lock()
	defer v.mu.Unlock()
	v.prune()

	item, held := v.items[reference]
	if !held {
		return nil, badRequest("Deployment credentials expired or were not supplied; " +
			"reconnect the provider and retry")
	}
	out := make(map[string]string, len(item.values))
	for key, value := range item.values {
		out[key] = value
	}
	return out, nil
}

// Clear forgets one reference, overwriting what it held first.
func (v *Vault) Clear(reference string) {
	v.mu.Lock()
	defer v.mu.Unlock()
	if item, held := v.items[reference]; held {
		for key := range item.values {
			item.values[key] = ""
			delete(item.values, key)
		}
		delete(v.items, reference)
	}
}

// prune drops anything older than the vault's lifetime. It runs on every
// access, so nothing lingers because nobody came back for it.
func (v *Vault) prune() {
	cutoff := time.Now().Add(-vaultTTL)
	for reference, item := range v.items {
		if item.created.Before(cutoff) {
			delete(v.items, reference)
		}
	}
}

// Environment is the credentials in the form a process takes them.
func Environment(values map[string]string) map[string]string {
	out := map[string]string{}
	for from, to := range map[string]string{
		"aws_access_key_id":     "AWS_ACCESS_KEY_ID",
		"aws_secret_access_key": "AWS_SECRET_ACCESS_KEY",
		"aws_session_token":     "AWS_SESSION_TOKEN",
		"region":                "AWS_REGION",
	} {
		if value := values[from]; value != "" {
			out[to] = value
		}
	}
	return out
}

func randomID() string {
	var raw [18]byte
	if _, err := rand.Read(raw[:]); err != nil {
		return strings.ReplaceAll(NowISO(), ":", "")
	}
	return base64.RawURLEncoding.EncodeToString(raw[:])
}

// --- the device flow --------------------------------------------------------------------

// flow is one sign-in in progress.
type flow struct {
	ID        string
	Region    string
	StartURL  string
	ClientID  string
	Secret    string
	Device    string
	Interval  int
	ExpiresAt time.Time
	Token     string // the vault reference for the access token
	Created   time.Time
}

// SSO drives the sign-ins the Studio's account panel starts.
type SSO struct {
	Vault *Vault

	mu    sync.Mutex
	flows map[string]*flow
}

// NewSSO is the process's sign-in manager.
func NewSSO(vault *Vault) *SSO { return &SSO{Vault: vault, flows: map[string]*flow{}} }

// Start begins a sign-in and returns what the customer has to do about it.
func (s *SSO) Start(ctx context.Context, startURL, region string) (map[string]any, error) {
	startURL = strings.TrimSpace(startURL)
	region = strings.TrimSpace(region)
	if !strings.HasPrefix(startURL, "https://") {
		return nil, badRequest("An IAM Identity Center start URL beginning with https:// is required")
	}
	if region == "" {
		return nil, badRequest("An AWS region is required")
	}

	var registration struct {
		ClientID     string `json:"clientId"`
		ClientSecret string `json:"clientSecret"`
	}
	if err := oidcCall(ctx, region, "/client/register", map[string]any{
		"clientName": ssoClientName, "clientType": ssoClientType, "scopes": []string{ssoScope},
	}, &registration); err != nil {
		return nil, err
	}

	var authorization struct {
		DeviceCode              string `json:"deviceCode"`
		UserCode                string `json:"userCode"`
		VerificationURI         string `json:"verificationUri"`
		VerificationURIComplete string `json:"verificationUriComplete"`
		ExpiresIn               int    `json:"expiresIn"`
		Interval                int    `json:"interval"`
	}
	if err := oidcCall(ctx, region, "/device_authorization", map[string]any{
		"clientId": registration.ClientID, "clientSecret": registration.ClientSecret,
		"startUrl": startURL,
	}, &authorization); err != nil {
		return nil, err
	}

	interval := authorization.Interval
	if interval <= 0 {
		interval = 5
	}
	expiresIn := authorization.ExpiresIn
	if expiresIn <= 0 {
		expiresIn = 600
	}
	current := &flow{
		ID: "sso_" + randomID(), Region: region, StartURL: startURL,
		ClientID: registration.ClientID, Secret: registration.ClientSecret,
		Device: authorization.DeviceCode, Interval: interval,
		ExpiresAt: time.Now().Add(time.Duration(expiresIn) * time.Second),
		Created:   time.Now(),
	}

	s.mu.Lock()
	s.prune()
	s.flows[current.ID] = current
	s.mu.Unlock()

	return map[string]any{
		"flow_id":                   current.ID,
		"verification_uri_complete": authorization.VerificationURIComplete,
		"verification_uri":          authorization.VerificationURI,
		"user_code":                 authorization.UserCode,
		"interval":                  interval,
		"expires_in":                expiresIn,
	}, nil
}

// Poll asks once whether the customer has approved it yet. The Studio calls
// this on the interval AWS asked for; it never blocks.
func (s *SSO) Poll(ctx context.Context, flowID string) (map[string]any, error) {
	current, err := s.flow(flowID)
	if err != nil {
		return nil, err
	}
	if current.Token != "" {
		accounts, err := s.Accounts(ctx, flowID)
		if err != nil {
			return nil, err
		}
		return map[string]any{"status": "complete", "accounts": accounts}, nil
	}
	if time.Now().After(current.ExpiresAt) {
		return nil, badRequest("The AWS sign-in request expired; start the connection again")
	}

	var token struct {
		AccessToken string `json:"accessToken"`
	}
	err = oidcCall(ctx, current.Region, "/token", map[string]any{
		"clientId": current.ClientID, "clientSecret": current.Secret,
		"grantType":  "urn:ietf:params:oauth:grant-type:device_code",
		"deviceCode": current.Device,
	}, &token)
	if err != nil {
		switch {
		case isOIDC(err, "AuthorizationPending"), isOIDC(err, "SlowDown"):
			return map[string]any{"status": "pending"}, nil
		case isOIDC(err, "ExpiredToken"):
			return nil, badRequest("The AWS sign-in request expired; start the connection again")
		}
		return nil, badRequest(RedactText(err.Error()))
	}

	reference := s.Vault.Put(map[string]string{"access_token": token.AccessToken})
	s.mu.Lock()
	current.Token = reference
	s.mu.Unlock()

	accounts, err := s.Accounts(ctx, flowID)
	if err != nil {
		return nil, err
	}
	return map[string]any{"status": "complete", "accounts": accounts}, nil
}

// Accounts are the AWS accounts this sign-in can reach.
func (s *SSO) Accounts(ctx context.Context, flowID string) ([]map[string]string, error) {
	current, token, err := s.authorized(flowID)
	if err != nil {
		return nil, err
	}
	var listed struct {
		AccountList []struct {
			AccountID    string `json:"accountId"`
			AccountName  string `json:"accountName"`
			EmailAddress string `json:"emailAddress"`
		} `json:"accountList"`
	}
	if err := portalCall(ctx, current.Region, "/assignment/accounts", token, nil, &listed); err != nil {
		return nil, err
	}

	accounts := []map[string]string{}
	for _, item := range listed.AccountList {
		accounts = append(accounts, map[string]string{
			"account_id": item.AccountID, "account_name": item.AccountName,
			"email": item.EmailAddress,
		})
	}
	sort.SliceStable(accounts, func(i, j int) bool {
		return strings.ToLower(accounts[i]["account_name"]) < strings.ToLower(accounts[j]["account_name"])
	})
	return accounts, nil
}

// Roles are what this sign-in may do in one account.
func (s *SSO) Roles(ctx context.Context, flowID, accountID string) ([]string, error) {
	current, token, err := s.authorized(flowID)
	if err != nil {
		return nil, err
	}
	var listed struct {
		RoleList []struct {
			RoleName string `json:"roleName"`
		} `json:"roleList"`
	}
	if err := portalCall(ctx, current.Region, "/assignment/roles", token,
		map[string]string{"account_id": accountID}, &listed); err != nil {
		return nil, err
	}
	roles := []string{}
	for _, item := range listed.RoleList {
		if item.RoleName != "" {
			roles = append(roles, item.RoleName)
		}
	}
	sort.Strings(roles)
	return roles, nil
}

// Select exchanges the sign-in for credentials for one role, and vaults them.
// The credentials themselves are never returned — only the reference.
func (s *SSO) Select(ctx context.Context, flowID, accountID, roleName string) (map[string]any, error) {
	current, token, err := s.authorized(flowID)
	if err != nil {
		return nil, err
	}
	var issued struct {
		RoleCredentials struct {
			AccessKeyID     string `json:"accessKeyId"`
			SecretAccessKey string `json:"secretAccessKey"`
			SessionToken    string `json:"sessionToken"`
			Expiration      int64  `json:"expiration"`
		} `json:"roleCredentials"`
	}
	if err := portalCall(ctx, current.Region, "/federation/credentials", token,
		map[string]string{"account_id": accountID, "role_name": roleName}, &issued); err != nil {
		return nil, err
	}

	reference := s.Vault.Put(map[string]string{
		"aws_access_key_id":     issued.RoleCredentials.AccessKeyID,
		"aws_secret_access_key": issued.RoleCredentials.SecretAccessKey,
		"aws_session_token":     issued.RoleCredentials.SessionToken,
		"region":                current.Region,
	})
	return map[string]any{
		"credential_reference": reference,
		"account_id":           accountID,
		"role_name":            roleName,
		"region":               current.Region,
		"expires_at":           issued.RoleCredentials.Expiration / 1000,
	}, nil
}

// StartURL is the sign-in address a flow was started with, which the profile
// written to ~/.aws/config has to record.
func (s *SSO) StartURL(flowID string) string {
	current, err := s.flow(flowID)
	if err != nil {
		return ""
	}
	return current.StartURL
}

// Forget ends a sign-in and drops anything it left behind.
func (s *SSO) Forget(flowID string) {
	s.mu.Lock()
	current, held := s.flows[flowID]
	delete(s.flows, flowID)
	s.mu.Unlock()
	if held && current.Token != "" {
		s.Vault.Clear(current.Token)
	}
}

func (s *SSO) flow(flowID string) (*flow, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.prune()
	current, held := s.flows[strings.TrimSpace(flowID)]
	if !held {
		return nil, badRequest("The AWS sign-in session expired; start the connection again")
	}
	return current, nil
}

// authorized is a flow that has got as far as having a token.
func (s *SSO) authorized(flowID string) (*flow, string, error) {
	current, err := s.flow(flowID)
	if err != nil {
		return nil, "", err
	}
	if current.Token == "" {
		return nil, "", conflict("AWS sign-in has not completed yet")
	}
	values, err := s.Vault.Get(current.Token)
	if err != nil {
		return nil, "", err
	}
	return current, values["access_token"], nil
}

func (s *SSO) prune() {
	cutoff := time.Now().Add(-flowTTL)
	for id, current := range s.flows {
		if current.Created.Before(cutoff) {
			delete(s.flows, id)
			if current.Token != "" {
				s.Vault.Clear(current.Token)
			}
		}
	}
}

// --- the two endpoints ------------------------------------------------------------------

// oidcError carries the code AWS returned so a pending sign-in can be told
// apart from a failed one.
type oidcError struct {
	Code    string
	Message string
}

func (e oidcError) Error() string {
	if e.Message == "" {
		return e.Code
	}
	return e.Code + ": " + e.Message
}

func isOIDC(err error, code string) bool {
	var failure oidcError
	return errors.As(err, &failure) &&
		strings.EqualFold(strings.TrimSuffix(failure.Code, "Exception"), code)
}

// oidcCall is one unsigned call to the device-flow service.
func oidcCall(ctx context.Context, region, path string, body, into any) error {
	return jsonPost(ctx, "https://oidc."+region+".amazonaws.com"+path, "", body, into)
}

// portalCall is one call to the sign-in portal, which takes the access token
// in a header of its own rather than an Authorization one.
func portalCall(ctx context.Context, region, path, token string,
	query map[string]string, into any) error {
	address := "https://portal.sso." + region + ".amazonaws.com" + path
	if len(query) > 0 {
		values := url.Values{}
		for key, value := range query {
			values.Set(key, value)
		}
		address += "?" + values.Encode()
	}
	return jsonPost(ctx, address, token, nil, into)
}

func jsonPost(ctx context.Context, address, token string, body, into any) error {
	method := http.MethodPost
	var payload io.Reader
	if body != nil {
		encoded, err := json.Marshal(body)
		if err != nil {
			return err
		}
		payload = bytes.NewReader(encoded)
	} else {
		method = http.MethodGet
	}

	request, err := http.NewRequestWithContext(ctx, method, address, payload)
	if err != nil {
		return err
	}
	if body != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	if token != "" {
		request.Header.Set("x-amz-sso_bearer_token", token)
	}

	response, err := (&http.Client{Timeout: 30 * time.Second}).Do(request)
	if err != nil {
		return oidcError{Code: "Unreachable", Message: RedactText(err.Error())}
	}
	defer response.Body.Close()

	answer, _ := io.ReadAll(io.LimitReader(response.Body, 4<<20))
	if response.StatusCode >= 400 {
		var failure struct {
			Error       string `json:"error"`
			Code        string `json:"__type"`
			Description string `json:"error_description"`
			Message     string `json:"message"`
		}
		_ = json.Unmarshal(answer, &failure)
		code := firstOf(failure.Code, failure.Error, http.StatusText(response.StatusCode))
		// AWS returns the type as a qualified name; the last part is the one
		// the flow branches on.
		if at := strings.LastIndex(code, "#"); at >= 0 {
			code = code[at+1:]
		}
		return oidcError{Code: code,
			Message: RedactText(firstOf(failure.Description, failure.Message))}
	}
	if into == nil || len(bytes.TrimSpace(answer)) == 0 {
		return nil
	}
	return json.Unmarshal(answer, into)
}

func firstOf(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return value
		}
	}
	return ""
}
