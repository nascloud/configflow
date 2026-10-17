package main

import (
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func newMihomoPanelFixture(t *testing.T, core http.HandlerFunc) http.HandlerFunc {
	t.Helper()
	server := httptest.NewServer(core)
	t.Cleanup(server.Close)
	configPath := filepath.Join(t.TempDir(), "config.yaml")
	os.WriteFile(configPath, []byte("external-controller: "+strings.TrimPrefix(server.URL, "http://")+"\nsecret: s3cret\n"), 0600)
	return mihomoPanelHandler(&Config{ServiceType: "mihomo", ConfigPath: configPath})
}

func TestMihomoPanelForwardsAllowedRequests(t *testing.T) {
	var seen []string
	handler := newMihomoPanelFixture(t, func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		seen = append(seen, r.Method+" "+r.URL.RequestURI()+" "+r.Header.Get("Authorization")+" "+string(body))
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"ok":true}`))
	})

	cases := []struct{ method, target, body, want string }{
		{"GET", "/api/mihomo/proxies", "", "GET /proxies Bearer s3cret "},
		{"PUT", "/api/mihomo/proxies/%E8%8A%82%E7%82%B9%2FA", `{"name":"x"}`, `PUT /proxies/%E8%8A%82%E7%82%B9%2FA Bearer s3cret {"name":"x"}`},
		{"GET", "/api/mihomo/group/Auto/delay?url=http%3A%2F%2Fcp.cloudflare.com&timeout=5000&extra=1", "",
			"GET /group/Auto/delay?timeout=5000&url=http%3A%2F%2Fcp.cloudflare.com Bearer s3cret "},
		{"DELETE", "/api/mihomo/connections/abc", "", "DELETE /connections/abc Bearer s3cret "},
	}
	for _, c := range cases {
		recorder := httptest.NewRecorder()
		handler(recorder, httptest.NewRequest(c.method, c.target, strings.NewReader(c.body)))
		if recorder.Code != http.StatusOK || recorder.Body.String() != `{"ok":true}` {
			t.Fatalf("%s %s: got %d %s", c.method, c.target, recorder.Code, recorder.Body.String())
		}
		if got := seen[len(seen)-1]; got != c.want {
			t.Fatalf("forwarded %q, want %q", got, c.want)
		}
	}
}

func TestMihomoPanelRejectsOtherEndpoints(t *testing.T) {
	called := false
	handler := newMihomoPanelFixture(t, func(w http.ResponseWriter, r *http.Request) { called = true })
	cases := []struct {
		method, target string
		status         int
	}{
		{"PUT", "/api/mihomo/rules", http.StatusMethodNotAllowed},
		{"POST", "/api/mihomo/upgrade/ui", http.StatusNotFound},
		{"POST", "/api/mihomo/restart", http.StatusNotFound},
		{"GET", "/api/mihomo/logs", http.StatusNotFound},
		{"DELETE", "/api/mihomo/rules", http.StatusMethodNotAllowed},
		{"GET", "/api/mihomo/proxies/a/b", http.StatusNotFound},
	}
	for _, c := range cases {
		recorder := httptest.NewRecorder()
		handler(recorder, httptest.NewRequest(c.method, c.target, nil))
		if recorder.Code != c.status {
			t.Fatalf("%s %s: got %d, want %d", c.method, c.target, recorder.Code, c.status)
		}
	}
	if called {
		t.Fatal("rejected request reached the controller")
	}
}

func TestMihomoPanelMasksControllerAuthFailure(t *testing.T) {
	handler := newMihomoPanelFixture(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
	})
	recorder := httptest.NewRecorder()
	handler(recorder, httptest.NewRequest("GET", "/api/mihomo/version", nil))
	if recorder.Code != http.StatusBadGateway || !strings.Contains(recorder.Body.String(), "CORE_AUTH_FAILED") {
		t.Fatalf("got %d %s", recorder.Code, recorder.Body.String())
	}
}

func TestMihomoPanelRequiresMihomoAgent(t *testing.T) {
	recorder := httptest.NewRecorder()
	mihomoPanelHandler(&Config{ServiceType: "oxidns"})(recorder, httptest.NewRequest("GET", "/api/mihomo/version", nil))
	if recorder.Code != http.StatusBadRequest {
		t.Fatalf("got %d", recorder.Code)
	}
}

func TestMihomoPanelReloadOnlyUsesCurrentConfigFile(t *testing.T) {
	var seen string
	handler := newMihomoPanelFixture(t, func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		seen = r.Method + " " + r.URL.RequestURI() + " " + string(body)
		w.WriteHeader(http.StatusNoContent)
	})
	recorder := httptest.NewRecorder()
	handler(recorder, httptest.NewRequest("PUT", "/api/mihomo/configs?force=false",
		strings.NewReader(`{"path":"/tmp/evil.yaml","payload":"mode: global"}`)))
	if recorder.Code != http.StatusNoContent {
		t.Fatalf("got %d %s", recorder.Code, recorder.Body.String())
	}
	if want := `PUT /configs?force=true {"path":"","payload":""}`; seen != want {
		t.Fatalf("forwarded %q, want %q", seen, want)
	}
}

func TestMihomoPanelUpgradeDropsQueryAndBody(t *testing.T) {
	var seen string
	handler := newMihomoPanelFixture(t, func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		seen = r.Method + " " + r.URL.RequestURI() + " " + string(body)
		w.Write([]byte(`{"status":"ok"}`))
	})
	recorder := httptest.NewRecorder()
	handler(recorder, httptest.NewRequest("POST", "/api/mihomo/upgrade?channel=alpha&force=true", strings.NewReader(`{"path":"x"}`)))
	if recorder.Code != http.StatusOK || seen != "POST /upgrade " {
		t.Fatalf("got %d, forwarded %q", recorder.Code, seen)
	}
}
