package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

func TestSendRegisterRequestAddsExistingBearerToken(t *testing.T) {
	const token = "existing-go-token"
	var gotAuthorization string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAuthorization = r.Header.Get("Authorization")
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"success":true,"id":"agent-1"}`))
	}))
	defer server.Close()

	config := &Config{ServerURL: server.URL, Token: token}
	response, err := config.sendRegisterRequest(RegisterRequest{Name: "probe", Host: "10.0.0.8"})

	if err != nil {
		t.Fatalf("sendRegisterRequest returned error: %v", err)
	}
	if response.ID != "agent-1" {
		t.Fatalf("unexpected response ID: %q", response.ID)
	}
	if gotAuthorization != "Bearer "+token {
		t.Fatalf("Authorization = %q, want configured bearer", gotAuthorization)
	}
}

func TestSendRegisterRequestWithoutTokenDoesNotAddAuthorization(t *testing.T) {
	var gotAuthorization string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAuthorization = r.Header.Get("Authorization")
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"success":true,"id":"agent-new","token":"new-token"}`))
	}))
	defer server.Close()

	config := &Config{ServerURL: server.URL}
	_, err := config.sendRegisterRequest(RegisterRequest{Name: "new", Host: "10.0.0.9"})

	if err != nil {
		t.Fatalf("sendRegisterRequest returned error: %v", err)
	}
	if gotAuthorization != "" {
		t.Fatalf("Authorization = %q, want empty", gotAuthorization)
	}
}

func TestHandleRegisterResponsePreservesExistingTokenWhenResponseOmitsToken(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.json")
	config := &Config{AgentID: "agent-1", Token: "keep-token", filePath: path}

	if err := config.handleRegisterResponse(&RegisterResponse{Success: true, ID: "agent-1"}); err != nil {
		t.Fatalf("handleRegisterResponse returned error: %v", err)
	}
	if config.Token != "keep-token" {
		t.Fatalf("Token = %q, want existing token", config.Token)
	}
	assertSavedToken(t, path, "keep-token")
}

func TestHandleRegisterResponseStoresTokenForFirstRegistration(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.json")
	config := &Config{filePath: path}

	if err := config.handleRegisterResponse(&RegisterResponse{
		Success: true,
		ID:      "agent-new",
		Token:   "issued-token",
	}); err != nil {
		t.Fatalf("handleRegisterResponse returned error: %v", err)
	}
	if config.AgentID != "agent-new" || config.Token != "issued-token" {
		t.Fatalf("unexpected registration state: id=%q token=%q", config.AgentID, config.Token)
	}
	assertSavedToken(t, path, "issued-token")
}

func assertSavedToken(t *testing.T, path, expected string) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read saved config: %v", err)
	}
	var saved Config
	if err := json.Unmarshal(data, &saved); err != nil {
		t.Fatalf("decode saved config: %v", err)
	}
	if saved.Token != expected {
		t.Fatalf("saved token = %q, want %q", saved.Token, expected)
	}
}
