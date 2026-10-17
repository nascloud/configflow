package routes

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func seedSendRedirects(t *testing.T, value string) {
	t.Helper()
	for _, name := range []string{"all", "eth0"} {
		dir := filepath.Join(sysctlRoot, "net", "ipv4", "conf", name)
		if err := os.MkdirAll(dir, 0755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, "send_redirects"), []byte(value+"\n"), 0644); err != nil {
			t.Fatal(err)
		}
	}
}

func sendRedirects(t *testing.T, name string) string {
	t.Helper()
	data, err := os.ReadFile(sendRedirectsPath(name))
	if err != nil {
		t.Fatal(err)
	}
	return strings.TrimSpace(string(data))
}

func TestReturnRoutingDisablesAndRestoresSendRedirects(t *testing.T) {
	host := &fakeHost{}
	installFakeHost(t, host)
	seedSendRedirects(t, "1")
	cfg := testConfig(t, tunConfig)

	if _, err := ApplyReturnRouting(cfg, sampleSpec()); err != nil {
		t.Fatal(err)
	}
	if sendRedirects(t, "all") != "0" || sendRedirects(t, "eth0") != "0" {
		t.Fatal("applying return routing must disable ICMP redirects")
	}
	state, _ := loadReturnState(cfg)
	if state.SendRedirects["all"] != "1" || state.SendRedirects["eth0"] != "1" {
		t.Fatalf("original values must be recorded: %v", state.SendRedirects)
	}

	// A second apply must not overwrite the recorded originals with 0.
	if _, err := ApplyReturnRouting(cfg, sampleSpec()); err != nil {
		t.Fatal(err)
	}
	if state, _ = loadReturnState(cfg); state.SendRedirects["all"] != "1" {
		t.Fatalf("re-apply must keep the original record: %v", state.SendRedirects)
	}

	if _, err := RemoveReturnRouting(cfg, false); err != nil {
		t.Fatal(err)
	}
	if sendRedirects(t, "all") != "1" || sendRedirects(t, "eth0") != "1" {
		t.Fatal("removing return routing must restore the original values")
	}
	if state, _ = loadReturnState(cfg); state.SendRedirects != nil {
		t.Fatalf("record must be cleared after restore: %v", state.SendRedirects)
	}
}

func TestSkippedReplayReenforcesSendRedirects(t *testing.T) {
	host := &fakeHost{}
	installFakeHost(t, host)
	seedSendRedirects(t, "1")
	cfg := testConfig(t, tunConfig)
	if _, err := ApplyReturnRouting(cfg, sampleSpec()); err != nil {
		t.Fatal(err)
	}
	seedSendRedirects(t, "1") // e.g. a reboot reset it while resources were recreated elsewhere
	host.mutations = nil

	reapplyReturnRouting(cfg)

	assertNoWrites(t, host) // replay itself is still skipped
	if sendRedirects(t, "all") != "0" || sendRedirects(t, "eth0") != "0" {
		t.Fatal("a skipped replay must still re-apply send_redirects=0")
	}
	if state, _ := loadReturnState(cfg); state.SendRedirects["all"] != "1" {
		t.Fatalf("original record must survive: %v", state.SendRedirects)
	}
}
