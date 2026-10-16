package routes

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

const oxMonitorTestHealth = `{"status":"ok","version":"1.6.1","instance_id":"test-instance","uptime_ms":42000,"checks":{"api":"ok"},"plugins":{"total":3}}`
const oxMonitorTestRecords = `{"ok":true,"next_cursor":"1720000000000:7","records":[{"id":8,"questions_json":[{"name":"example.com.","qtype":"A","qclass":"IN"}]}]}`
const oxMonitorTestDetail = `{"ok":true,"record":{"id":8,"questions_json":[],"steps":[{"kind":"matcher"}]}}`

func oxMonitorTestConfig(t *testing.T, endpoint, plugins string) *Config {
	t.Helper()
	cfg := &Config{ServiceType: "oxidns", Token: "agent-secret", ConfigPath: filepath.Join(t.TempDir(), "config.yaml")}
	content := "api:\n  http:\n    listen: " + strings.TrimPrefix(endpoint, "http://") + "\n    auth: {type: basic, username: monitor-user, password: core-secret}\nplugins:\n" + plugins
	if err := os.WriteFile(cfg.ConfigPath, []byte(content), 0600); err != nil {
		t.Fatal(err)
	}
	return cfg
}

func oxMonitorTestRequest(cfg *Config, method, target string) *httptest.ResponseRecorder {
	mux := http.NewServeMux()
	RegisterRoutes(mux, cfg)
	r := httptest.NewRequest(method, target, nil)
	r.Header.Set("Authorization", "Bearer "+cfg.Token)
	r.Header.Set("Cookie", "agent-session=never-forward")
	w := httptest.NewRecorder()
	mux.ServeHTTP(w, r)
	return w
}

func oxMonitorTestError(t *testing.T, response *httptest.ResponseRecorder, status int, code string) {
	t.Helper()
	var body struct {
		Success bool   `json:"success"`
		Code    string `json:"code"`
		Message string `json:"message"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &body); err != nil || response.Code != status || body.Success || body.Code != code || body.Message == "" {
		t.Fatalf("expected %d %s, got %d %s (%v)", status, code, response.Code, response.Body.String(), err)
	}
	for _, secret := range []string{"core-secret", "agent-secret", "private-database", "monitor-user"} {
		if strings.Contains(response.Body.String(), secret) {
			t.Fatalf("error leaked %q", secret)
		}
	}
}

func TestOxiDNSMonitorSuccessAndReadOnly(t *testing.T) {
	var calls []string
	var callsMu sync.Mutex
	core := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		username, password, ok := r.BasicAuth()
		if !ok || username != "monitor-user" || password != "core-secret" || r.Header.Get("Cookie") != "" || r.Method != http.MethodGet {
			t.Errorf("invalid upstream auth, headers or method")
		}
		callsMu.Lock()
		calls = append(calls, r.URL.RequestURI())
		callsMu.Unlock()
		switch r.URL.Path {
		case "/api/health":
			fmt.Fprint(w, oxMonitorTestHealth)
		case "/api/metrics":
			fmt.Fprint(w, "# TYPE oxidns_queries_total counter\noxidns_queries_total 42\n")
		case "/api/plugins/records/records":
			fmt.Fprint(w, oxMonitorTestRecords)
		case "/api/plugins/records/records/8":
			fmt.Fprint(w, oxMonitorTestDetail)
		default:
			t.Errorf("unexpected path %s", r.URL.Path)
			w.WriteHeader(404)
		}
	}))
	defer core.Close()
	cfg := oxMonitorTestConfig(t, core.URL, "- {tag: records, type: query_recorder}\n- {tag: cache, type: cache}\n")
	before, _ := os.ReadFile(cfg.ConfigPath)
	statePath := filepath.Join(filepath.Dir(cfg.ConfigPath), "queries.db")
	if err := os.WriteFile(statePath, []byte("existing-recorder-database"), 0600); err != nil {
		t.Fatal(err)
	}
	sentinel := filepath.Join(filepath.Dir(cfg.ConfigPath), "lifecycle-called")
	cfg.StartCommand, cfg.StopCommand, cfg.RestartCommand = "touch "+sentinel, "touch "+sentinel, "touch "+sentinel
	response := oxMonitorTestRequest(cfg, http.MethodGet, "/api/oxidns/overview")
	var overview struct {
		Success      bool                   `json:"success"`
		Capabilities oxMonitorCapabilities  `json:"capabilities"`
		Health       map[string]interface{} `json:"health"`
		Metrics      string                 `json:"metrics"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &overview); err != nil || response.Code != 200 || !overview.Success || overview.Health["instance_id"] != "test-instance" || !strings.Contains(overview.Metrics, "oxidns_queries_total 42") {
		t.Fatalf("unexpected overview: %d %s (%v)", response.Code, response.Body.String(), err)
	}
	if !reflect.DeepEqual(overview.Capabilities, oxMonitorCapabilities{[]string{"records"}, []string{"cache"}}) {
		t.Fatalf("wrong capabilities: %#v", overview.Capabilities)
	}
	query := url.Values{"recorder": {"records"}, "limit": {"25"}, "cursor": {"1720000000000:7"}, "since_ms": {"1"}, "until_ms": {"1720000000000"}, "qname": {" example.com "}, "qtype": {"a"}, "client_ip": {"192.168."}, "rcode": {" No Error "}, "status": {"has_response"}, "matcher_tag": {"local"}}
	response = oxMonitorTestRequest(cfg, http.MethodGet, "/api/oxidns/queries?"+query.Encode())
	if response.Code != 200 || response.Body.String() != oxMonitorTestRecords {
		t.Fatalf("list changed official response: %d %s", response.Code, response.Body.String())
	}
	callsMu.Lock()
	last, _ := url.Parse(calls[len(calls)-1])
	callsMu.Unlock()
	if last.Query().Has("recorder") || last.Query().Get("qtype") != "A" || last.Query().Get("rcode") != "No Error" || last.Query().Get("qname") != "example.com" {
		t.Fatalf("wrong forwarded query: %s", last)
	}
	response = oxMonitorTestRequest(cfg, http.MethodGet, "/api/oxidns/queries/8")
	if response.Code != 200 || response.Body.String() != oxMonitorTestDetail {
		t.Fatalf("detail changed official response: %d %s", response.Code, response.Body.String())
	}
	if response.Header().Get("Cache-Control") != "no-store" {
		t.Fatal("query data must not be cached")
	}
	after, _ := os.ReadFile(cfg.ConfigPath)
	state, _ := os.ReadFile(statePath)
	entries, _ := os.ReadDir(filepath.Dir(cfg.ConfigPath))
	if string(before) != string(after) || string(state) != "existing-recorder-database" || len(entries) != 2 {
		t.Fatal("monitoring changed local files")
	}
	if _, err := os.Stat(sentinel); !os.IsNotExist(err) {
		t.Fatal("monitoring invoked a lifecycle command")
	}
}

func TestOxiDNSMonitorRejectsInvalidRequestsBeforeCore(t *testing.T) {
	var calls atomic.Int32
	core := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls.Add(1); fmt.Fprint(w, oxMonitorTestRecords) }))
	defer core.Close()
	cfg := oxMonitorTestConfig(t, core.URL, "- {tag: records, type: query_recorder}\n")
	for _, query := range []string{"limit=0", "limit=101", "limit=-1", "limit=1&limit=2", "unknown=value", "recorder=records&recorder=records", "recorder=../flush", "cursor=1", "cursor=1:2:3", "cursor=9223372036854775808:1", "since_ms=-1", "until_ms=abc", "since_ms=9&until_ms=1", "status=done", "qname=%00", "qname=%FF", "qname=%ZZ", "qname=a;limit=1", "qname=" + strings.Repeat("a", 513), "recorder=cache", "url=http://example.com", "qname=" + strings.Repeat("a", 8193)} {
		t.Run(query[:min(len(query), 60)], func(t *testing.T) {
			oxMonitorTestError(t, oxMonitorTestRequest(cfg, http.MethodGet, "/api/oxidns/queries?"+query), 400, "INVALID_QUERY")
		})
	}
	for _, id := range []string{"0", "-1", "01", "+1", "9223372036854775808", "1/flush", "1?limit=2", "1?recorder=records&recorder=records"} {
		oxMonitorTestError(t, oxMonitorTestRequest(cfg, http.MethodGet, "/api/oxidns/queries/"+id), 400, "INVALID_QUERY")
	}
	for _, method := range []string{http.MethodPost, http.MethodDelete, http.MethodPut, http.MethodHead, http.MethodOptions} {
		response := oxMonitorTestRequest(cfg, method, "/api/oxidns/queries")
		oxMonitorTestError(t, response, 405, "METHOD_NOT_ALLOWED")
		if response.Header().Get("Allow") != "GET" {
			t.Fatal("missing Allow header")
		}
	}
	oxMonitorTestError(t, oxMonitorTestRequest(cfg, http.MethodGet, "/api/oxidns/overview?url=http://evil.test"), 400, "INVALID_QUERY")
	for _, path := range []string{"/api/oxidns/queries/%2e%2e/flush", "/api/oxidns/queries/1%2Fflush", "/api/oxidns/queries/1/../../flush", "/api/oxidns/queries/flush", "/api/oxidns/flush"} {
		if response := oxMonitorTestRequest(cfg, http.MethodGet, path); response.Code == 200 {
			t.Fatalf("accepted arbitrary path %s", path)
		}
	}
	if calls.Load() != 0 {
		t.Fatalf("invalid requests contacted core %d times", calls.Load())
	}
}

func TestOxiDNSMonitorAuthentication(t *testing.T) {
	var calls atomic.Int32
	core := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls.Add(1); fmt.Fprint(w, oxMonitorTestRecords) }))
	defer core.Close()
	cfg := oxMonitorTestConfig(t, core.URL, "- {tag: records, type: query_recorder}\n")
	mux := http.NewServeMux()
	RegisterRoutes(mux, cfg)
	for _, headers := range [][]string{nil, {"Bearer wrong"}, {"Basic agent-secret"}, {"Bearer "}, {"Bearer agent-secret", "Bearer agent-secret"}, {"Bearer agent-secret "}} {
		r := httptest.NewRequest(http.MethodGet, "/api/oxidns/queries", nil)
		for _, value := range headers {
			r.Header.Add("Authorization", value)
		}
		w := httptest.NewRecorder()
		mux.ServeHTTP(w, r)
		oxMonitorTestError(t, w, 401, "UNAUTHORIZED")
	}
	if calls.Load() != 0 {
		t.Fatal("unauthorized request contacted core")
	}
}

func TestOxiDNSMonitorConfigurationAndRecorderSelection(t *testing.T) {
	core := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, oxMonitorTestRecords) }))
	defer core.Close()
	cfg := oxMonitorTestConfig(t, core.URL, "- {tag: second, type: query_recorder}\n- {tag: first, type: query_recorder}\n")
	oxMonitorTestError(t, oxMonitorTestRequest(cfg, http.MethodGet, "/api/oxidns/queries"), 400, "INVALID_QUERY")
	if response := oxMonitorTestRequest(cfg, http.MethodGet, "/api/oxidns/queries?recorder=second"); response.Code != 200 {
		t.Fatalf("explicit recorder failed: %s", response.Body.String())
	}
	cfg = oxMonitorTestConfig(t, core.URL, "- {tag: cache, type: cache}\n")
	oxMonitorTestError(t, oxMonitorTestRequest(cfg, http.MethodGet, "/api/oxidns/queries"), 409, "RECORDER_NOT_CONFIGURED")
	cases := []struct{ name, content, code string }{
		{"missing", "plugins: []\n", "API_NOT_CONFIGURED"},
		{"tls", "api: {http: {listen: '127.0.0.1:9191', ssl: {cert: private-cert, key: private-key}}}\n", "TLS_UNSUPPORTED"},
		{"remote", "api: {http: '203.0.113.8:9191'}\n", "CONFIG_UNSUPPORTED"},
		{"hostname", "api: {http: 'evil.test:9191'}\n", "CONFIG_UNSUPPORTED"},
		{"url", "api: {http: 'http://127.0.0.1:9191'}\n", "CONFIG_UNSUPPORTED"},
		{"env", "api: {http: '${API_LISTEN}'}\n", "CONFIG_UNSUPPORTED"},
		{"invalid-auth", "api: {http: {listen: '127.0.0.1:9191', auth: {type: bearer, token: core-secret}}}\n", "CONFIG_UNSUPPORTED"},
		{"env-auth", "api: {http: {listen: '127.0.0.1:9191', auth: {type: basic, username: user, password: '${CORE_PASSWORD}'}}}\n", "CONFIG_UNSUPPORTED"},
		{"malformed", "api: [\n", "CONFIG_UNSUPPORTED"},
		{"includes", "api: {http: '127.0.0.1:9191'}\ninclude: [plugins.yaml]\n", "CONFIG_UNSUPPORTED"},
		{"unsafe-tag", "api: {http: '127.0.0.1:9191'}\nplugins: [{type: query_recorder, tag: '../cache/flush'}]\n", "CONFIG_UNSUPPORTED"},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			if err := os.WriteFile(cfg.ConfigPath, []byte(test.content), 0600); err != nil {
				t.Fatal(err)
			}
			cfg.HealthURL = core.URL + "/api/readyz"
			oxMonitorTestError(t, oxMonitorTestRequest(cfg, http.MethodGet, "/api/oxidns/overview"), 409, test.code)
		})
	}
	cfg.ServiceType = "mosdns"
	oxMonitorTestError(t, oxMonitorTestRequest(cfg, http.MethodGet, "/api/oxidns/overview"), 409, "CONFIG_UNSUPPORTED")
}

func TestOxiDNSMonitorRedirectAndProxyCannotLeakCredentials(t *testing.T) {
	var leaked atomic.Int32
	sink := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { leaked.Add(1) }))
	defer sink.Close()
	core := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Location", sink.URL+"/steal")
		w.Header().Set("Set-Cookie", "secret=core-secret")
		w.Header().Set("WWW-Authenticate", "Basic realm=private-database")
		w.WriteHeader(http.StatusFound)
		fmt.Fprint(w, "core-secret private-database")
	}))
	defer core.Close()
	t.Setenv("HTTP_PROXY", sink.URL)
	t.Setenv("HTTPS_PROXY", sink.URL)
	t.Setenv("ALL_PROXY", sink.URL)
	t.Setenv("NO_PROXY", "")
	cfg := oxMonitorTestConfig(t, core.URL, "- {tag: records, type: query_recorder}\n")
	response := oxMonitorTestRequest(cfg, http.MethodGet, "/api/oxidns/queries")
	oxMonitorTestError(t, response, 502, "INVALID_RESPONSE")
	if leaked.Load() != 0 || response.Header().Get("Location") != "" || response.Header().Get("Set-Cookie") != "" || response.Header().Get("WWW-Authenticate") != "" {
		t.Fatal("upstream redirect or headers escaped bridge")
	}
	if oxMonitorClient().Transport.(*http.Transport).Proxy != nil {
		t.Fatal("transport enables environment proxy")
	}
}

func TestOxiDNSMonitorBoundsAndSafeUpstreamErrors(t *testing.T) {
	for _, test := range []struct {
		name, path, body, code string
		status, want           int
	}{
		{"authentication", "/api/oxidns/queries", "private-database core-secret", "CORE_AUTH_FAILED", 401, 502},
		{"forbidden", "/api/oxidns/queries", "core-secret", "CORE_AUTH_FAILED", 403, 502},
		{"server-error", "/api/oxidns/queries", "private-database", "INVALID_RESPONSE", 500, 502},
		{"missing-record", "/api/oxidns/queries/8", `{"ok":false,"code":"record_not_found","message":"private-database"}`, "RECORD_NOT_FOUND", 404, 404},
		{"missing-recorder-detail", "/api/oxidns/queries/8", "404 Not Found private-database", "RECORDER_NOT_CONFIGURED", 404, 409},
		{"missing-recorder", "/api/oxidns/queries", "private-database", "RECORDER_NOT_CONFIGURED", 404, 409},
		{"invalid-json", "/api/oxidns/queries", "<html>private-database</html>", "INVALID_RESPONSE", 200, 502},
		{"error-json", "/api/oxidns/queries", `{"ok":false,"message":"core-secret private-database"}`, "INVALID_RESPONSE", 200, 502},
		{"large-json", "/api/oxidns/queries", strings.Repeat(" ", oxMonitorJSONLimit+1), "INVALID_RESPONSE", 200, 502},
		{"large-metrics", "/api/oxidns/overview", strings.Repeat("x", oxMonitorMetricsLimit+1), "INVALID_RESPONSE", 200, 502},
	} {
		t.Run(test.name, func(t *testing.T) {
			core := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/api/health" {
					fmt.Fprint(w, oxMonitorTestHealth)
					return
				}
				w.WriteHeader(test.status)
				fmt.Fprint(w, test.body)
			}))
			defer core.Close()
			cfg := oxMonitorTestConfig(t, core.URL, "- {tag: records, type: query_recorder}\n")
			oxMonitorTestError(t, oxMonitorTestRequest(cfg, http.MethodGet, test.path), test.want, test.code)
		})
	}
}

func TestOxiDNSMonitorCancellationAndTimeout(t *testing.T) {
	for _, cancelEarly := range []bool{false, true} {
		t.Run(fmt.Sprint(cancelEarly), func(t *testing.T) {
			started := make(chan struct{})
			canceled := make(chan struct{})
			core := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { close(started); <-r.Context().Done(); close(canceled) }))
			defer core.Close()
			cfg := oxMonitorTestConfig(t, core.URL, "- {tag: records, type: query_recorder}\n")
			ctx, cancel := context.WithTimeout(context.Background(), 150*time.Millisecond)
			defer cancel()
			r := httptest.NewRequest(http.MethodGet, "/api/oxidns/queries", nil).WithContext(ctx)
			w := httptest.NewRecorder()
			done := make(chan struct{})
			go func() { OxiDNSMonitorHandler(cfg)(w, r); close(done) }()
			select {
			case <-started:
			case <-time.After(time.Second):
				t.Fatal("upstream request did not start")
			}
			if cancelEarly {
				cancel()
			}
			select {
			case <-done:
			case <-time.After(time.Second):
				t.Fatal("cancellation did not stop bridge")
			}
			status := 504
			if cancelEarly {
				status = 502
			}
			oxMonitorTestError(t, w, status, "CORE_UNREACHABLE")
			select {
			case <-canceled:
			case <-time.After(time.Second):
				t.Fatal("bridge did not cancel core request")
			}
		})
	}
}

func TestOxiDNSMonitorLocalAddress(t *testing.T) {
	for input, want := range map[string]string{"127.0.0.1:9191": "127.0.0.1:9191", "0.0.0.0:9191": "127.0.0.1:9191", "[::]:9191": "[::1]:9191", "[::1]:9191": "[::1]:9191", ":9191": "[::1]:9191"} {
		got, err := oxMonitorLocalAddress(input)
		if err != nil || got != want {
			t.Errorf("%s = %s, %v", input, got, err)
		}
	}
	for _, input := range []string{"localhost:9191", "127.0.0.1:0", "127.0.0.1:65536", "203.0.113.8:9191", "127.0.0.1:9191/path"} {
		if _, err := oxMonitorLocalAddress(input); err == nil {
			t.Errorf("accepted unsupported address %s", input)
		}
	}
}

func TestOxiDNSMonitorReloadsConfigAndSupportsUnauthenticatedAPI(t *testing.T) {
	var received []string
	var mu sync.Mutex
	core := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "" {
			t.Error("Agent credentials were sent to unauthenticated API")
		}
		mu.Lock()
		received = append(received, r.URL.Path)
		mu.Unlock()
		fmt.Fprint(w, oxMonitorTestRecords)
	}))
	defer core.Close()
	cfg := oxMonitorTestConfig(t, core.URL, "")
	for _, tag := range []string{"first", "next"} {
		content := "api: {http: '" + strings.TrimPrefix(core.URL, "http://") + "'}\nplugins: [{tag: '" + tag + "', type: query_recorder}]\n"
		if err := os.WriteFile(cfg.ConfigPath, []byte(content), 0600); err != nil {
			t.Fatal(err)
		}
		response := oxMonitorTestRequest(cfg, http.MethodGet, "/api/oxidns/queries")
		if response.Code != 200 {
			t.Fatalf("changed config not used: %d %s", response.Code, response.Body.String())
		}
	}
	mu.Lock()
	defer mu.Unlock()
	if !reflect.DeepEqual(received, []string{"/api/plugins/first/records", "/api/plugins/next/records"}) {
		t.Fatalf("stale config: %v", received)
	}
}

func TestOxiDNSMonitorOfficialTagsAndResponseEnvelopes(t *testing.T) {
	for _, tag := range []string{"records", "query_record", "cache-1", "my.records", "a", strings.Repeat("a", 64)} {
		if !oxMonitorSafeTag(tag) {
			t.Errorf("rejected official tag %q", tag)
		}
	}
	for _, tag := range []string{"", ".", "..", "a..b", "_cache", "cache-", ".cache", "查询", "percent%2e", strings.Repeat("a", 65)} {
		if oxMonitorSafeTag(tag) {
			t.Errorf("accepted invalid official tag %q", tag)
		}
	}
	for _, test := range []struct{ path, data string }{
		{"/api/health", `{"error":{"message":"private-database"}}`},
		{"/api/health", `{"status":"ok","version":"1.6.1","instance_id":"id","uptime_ms":null}`},
		{"/api/plugins/records/records", `{"ok":true,"records":null,"next_cursor":null}`},
		{"/api/plugins/records/records", `{"ok":true,"records":[],"next_cursor":45}`},
		{"/api/plugins/records/records/8", `{"ok":true,"record":{"record":{"id":8}}}`},
	} {
		if oxMonitorValidJSON(test.path, []byte(test.data)) {
			t.Errorf("accepted malformed response %s", test.data)
		}
	}
	if !oxMonitorValidJSON("/api/plugins/records/records", []byte(`{"ok":true,"records":[],"next_cursor":null}`)) {
		t.Fatal("rejected empty official list")
	}
}

func TestOxiDNSMonitorRcodeDisplayText(t *testing.T) {
	for _, value := range []string{"No Error", "Server Failure", "Non-Existent Domain", "Bad OPT Version", "RCODE_16", "noerror"} {
		_, query, err := oxMonitorParseQuery(url.Values{"rcode": {" " + value + " "}}.Encode(), false)
		if err != nil || query.Get("rcode") != value {
			t.Errorf("rcode spelling changed: %q => %q, %v", value, query.Get("rcode"), err)
		}
	}
	for _, value := range []string{"No\nError", "No\x00Error", "No\x7fError", "No\tError", "无错误", "NOERROR/flush", strings.Repeat("A", 65)} {
		_, _, err := oxMonitorParseQuery(url.Values{"rcode": {value}}.Encode(), false)
		if err == nil || err.code != "INVALID_QUERY" {
			t.Errorf("accepted invalid rcode %q", value)
		}
	}
}
