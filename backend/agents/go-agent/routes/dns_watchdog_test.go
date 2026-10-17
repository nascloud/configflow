package routes

import (
	"errors"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// udpResponder answers each query with reply(query) (nil = stay silent).
func udpResponder(t *testing.T, reply func(query []byte) []byte) string {
	t.Helper()
	conn, err := net.ListenPacket("udp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	go func() {
		buf := make([]byte, 1500)
		for {
			n, addr, err := conn.ReadFrom(buf)
			if err != nil {
				return
			}
			if out := reply(append([]byte(nil), buf[:n]...)); out != nil {
				conn.WriteTo(out, addr)
			}
		}
	}()
	return conn.LocalAddr().String()
}

func nxdomain(query []byte) []byte {
	out := append([]byte(nil), query...)
	out[2] |= 0x80 // QR
	out[3] = 0x83  // RA + RCODE NXDOMAIN
	return out
}

func TestDNSAnswersAcceptsAnyReplyWithMatchingID(t *testing.T) {
	if err := dnsAnswers(udpResponder(t, nxdomain)); err != nil {
		t.Fatalf("NXDOMAIN must count as answering: %v", err)
	}
	var seen []byte
	addr := udpResponder(t, func(q []byte) []byte { seen = q; return nil })
	if err := dnsAnswers(addr); err == nil {
		t.Fatal("silent server must fail")
	}
	if len(seen) < 12 || string(seen[12:]) != "\x13configflow-watchdog\x07invalid\x00\x00\x01\x00\x01" {
		t.Fatalf("unexpected query encoding: %q", seen)
	}
	wrongID := udpResponder(t, func(q []byte) []byte { out := nxdomain(q); out[0] ^= 0xff; return out })
	if err := dnsAnswers(wrongID); err == nil {
		t.Fatal("reply with another ID must not count")
	}
}

func TestWatchdogDNSAddress(t *testing.T) {
	dir := t.TempDir()
	cfg := &Config{ConfigPath: filepath.Join(dir, "config.yaml")}
	os.WriteFile(cfg.ConfigPath, []byte("plugins:\n  - tag: tcp\n    type: tcp_server\n    args: {listen: 0.0.0.0:5300}\n  - tag: udp\n    type: udp_server\n    args: {listen: 0.0.0.0:53}\n"), 0600)
	if got, err := watchdogDNSAddress(cfg); err != nil || got != "127.0.0.1:53" {
		t.Fatalf("got %q %v", got, err)
	}
	cfg.HealthDNSAddress = "10.0.0.6:53"
	if got, _ := watchdogDNSAddress(cfg); got != "10.0.0.6:53" {
		t.Fatalf("health_dns_address must win, got %q", got)
	}
}

type watchdogFakes struct {
	now      time.Time
	healthy  bool
	running  bool
	busy     bool
	restarts int
}

func newTestWatchdog(t *testing.T, f *watchdogFakes) *dnsWatchdog {
	t.Helper()
	cfg := &Config{ServiceType: "oxidns", HealthDNSAddress: "127.0.0.1:53"}
	w := newDNSWatchdog(cfg)
	w.now = func() time.Time { return f.now }
	w.probe = func(string) error {
		if f.healthy {
			return nil
		}
		return errors.New("timeout")
	}
	w.running = func(*Config) (bool, error) { return f.running, nil }
	w.lock = func(*Config) (func(), error) {
		if f.busy {
			return nil, errDeploymentBusy
		}
		return func() {}, nil
	}
	w.restart = func(*Config) ([]byte, error) { f.restarts++; return nil, nil }
	return w
}

func TestWatchdogRestartsHungCoreAfterConsecutiveFailures(t *testing.T) {
	f := &watchdogFakes{now: time.Unix(1000, 0), running: true}
	w := newTestWatchdog(t, f)
	steps := []string{}
	for i := 0; i < 3; i++ {
		steps = append(steps, w.tick())
		f.now = f.now.Add(10 * time.Second)
	}
	if want := []string{"failing", "failing", "restarted"}; !equalStrings(steps, want) || f.restarts != 1 {
		t.Fatalf("steps=%v restarts=%d", steps, f.restarts)
	}
	if got := w.tick(); got != "grace" {
		t.Fatalf("restart must be followed by a grace period, got %s", got)
	}
	f.now = f.now.Add(61 * time.Second)
	f.healthy = true
	if got := w.tick(); got != "ok" {
		t.Fatalf("got %s", got)
	}
}

func TestWatchdogRecoveryResetsFailureCount(t *testing.T) {
	f := &watchdogFakes{now: time.Unix(1000, 0), running: true}
	w := newTestWatchdog(t, f)
	w.tick()
	w.tick()
	f.healthy = true
	w.tick()
	f.healthy = false
	w.tick()
	w.tick()
	if f.restarts != 0 {
		t.Fatal("a successful probe must reset the consecutive count")
	}
}

func TestWatchdogLeavesStoppedOrBusyServiceAlone(t *testing.T) {
	f := &watchdogFakes{now: time.Unix(1000, 0), running: false}
	w := newTestWatchdog(t, f)
	for i := 0; i < 5; i++ {
		if got := w.tick(); got != "not-running" {
			t.Fatalf("stopped service: got %s", got)
		}
	}
	f.running, f.busy = true, true
	for i := 0; i < 5; i++ {
		if got := w.tick(); got != "busy" {
			t.Fatalf("deployment in progress: got %s", got)
		}
	}
	if f.restarts != 0 {
		t.Fatal("watchdog must not restart a stopped service or during a deployment")
	}
}

func TestWatchdogRestartBudget(t *testing.T) {
	f := &watchdogFakes{now: time.Unix(1000, 0), running: true}
	w := newTestWatchdog(t, f)
	results := map[string]int{}
	for i := 0; i < 40; i++ { // 40 min of a permanently hung core
		results[w.tick()]++
		f.now = f.now.Add(time.Minute)
	}
	if f.restarts != 3 || results["budget-exhausted"] == 0 {
		t.Fatalf("restarts=%d results=%v", f.restarts, results)
	}
	f.now = f.now.Add(time.Hour) // window passes
	for i := 0; i < 3; i++ {
		w.tick()
	}
	if f.restarts != 4 {
		t.Fatalf("budget must refill after the window, restarts=%d", f.restarts)
	}
}

func equalStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
