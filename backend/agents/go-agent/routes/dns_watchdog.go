package routes

import (
	"context"
	"crypto/rand"
	"encoding/binary"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"time"

	"gopkg.in/yaml.v3"
)

// Service watchdog. The service manager (supervise-daemon, systemd) restarts a
// crashed core, but a hung one keeps its process alive while it stops serving.
// The watchdog probes the core locally (DNS listener for OxiDNS/MosDNS, the
// external controller for mihomo) and restarts the service when it stops
// answering, without fighting deployments or deliberate stops.

const watchdogProbeName = "configflow-watchdog.invalid."

// watchdogTarget is what gets probed; String never includes the secret.
type watchdogTarget struct {
	kind    string // "dns" or "http"
	address string
	secret  string
}

func (t watchdogTarget) String() string { return t.kind + "://" + t.address }

type serviceWatchdog struct {
	cfg *Config

	interval    time.Duration
	failures    int           // consecutive failed probes before restarting
	grace       time.Duration // after a restart, time for the core to start
	maxRestarts int           // per window; beyond it only log
	window      time.Duration

	probe   func(target watchdogTarget) error
	running func(cfg *Config) (bool, error)
	lock    func(cfg *Config) (func(), error)
	restart func(cfg *Config) ([]byte, error)
	now     func() time.Time

	consecutive int
	restarts    []time.Time
	pausedUntil time.Time
	gaveUp      bool
}

func newDNSWatchdog(cfg *Config) *serviceWatchdog {
	return &serviceWatchdog{
		cfg: cfg, interval: 10 * time.Second, failures: 3, grace: 60 * time.Second,
		maxRestarts: 3, window: time.Hour,
		probe: probeWatchdogTarget, running: serviceRunning, lock: AcquireServiceOperation,
		restart: RestartService, now: time.Now,
	}
}

// StartDNSWatchdog runs the watchdog for OxiDNS, MosDNS and mihomo unless disabled.
func StartDNSWatchdog(cfg *Config) {
	switch cfg.ServiceType {
	case "oxidns", "mosdns", "mihomo":
	default:
		return
	}
	if cfg.DNSWatchdogDisabled {
		return
	}
	w := newDNSWatchdog(cfg)
	log.Printf("Watchdog enabled for %s: probe every %s, restart after %d silent probes, at most %d restarts per %s",
		cfg.ServiceType, w.interval, w.failures, w.maxRestarts, w.window)
	go func() {
		ticker := time.NewTicker(w.interval)
		defer ticker.Stop()
		for range ticker.C {
			w.tick()
		}
	}()
}

// tick runs one probe round; it returns what it did, for tests and logs.
func (w *serviceWatchdog) tick() string {
	now := w.now()
	if now.Before(w.pausedUntil) {
		return "grace"
	}
	release, err := w.lock(w.cfg)
	if err != nil {
		// A deployment, restart or upgrade owns the service right now.
		w.consecutive = 0
		return "busy"
	}
	defer release()
	if running, err := w.running(w.cfg); err != nil || !running {
		// Crashes are the service manager's job; a stopped service stays stopped.
		w.consecutive = 0
		return "not-running"
	}
	target, err := watchdogTargetFor(w.cfg)
	if err != nil {
		return "no-address"
	}
	if err = w.probe(target); err == nil {
		if w.consecutive > 0 {
			log.Printf("Watchdog: %s answering again", target)
		}
		w.consecutive, w.gaveUp = 0, false
		return "ok"
	}
	w.consecutive++
	log.Printf("Watchdog: probe %d/%d to %s failed: %v", w.consecutive, w.failures, target, err)
	if w.consecutive < w.failures {
		return "failing"
	}
	recent := w.restarts[:0]
	for _, at := range w.restarts {
		if now.Sub(at) < w.window {
			recent = append(recent, at)
		}
	}
	w.restarts = recent
	if len(w.restarts) >= w.maxRestarts {
		if !w.gaveUp {
			log.Printf("Watchdog: %d restarts within %s, not restarting again until the window passes", len(w.restarts), w.window)
			w.gaveUp = true
		}
		return "budget-exhausted"
	}
	log.Printf("Watchdog: %s is running but not answering, restarting %s", target, w.cfg.ServiceType)
	w.restarts = append(w.restarts, now)
	w.consecutive = 0
	w.pausedUntil = now.Add(w.grace)
	if _, err = w.restart(w.cfg); err != nil {
		log.Printf("Watchdog: restart failed: %v", err)
		return "restart-failed"
	}
	return "restarted"
}

// watchdogTargetFor picks what to probe: mihomo's external controller (or its
// DNS listener when no controller is configured), otherwise the DNS listener.
func watchdogTargetFor(cfg *Config) (watchdogTarget, error) {
	if cfg.ServiceType != "mihomo" {
		address, err := watchdogDNSAddress(cfg)
		return watchdogTarget{kind: "dns", address: address}, err
	}
	data, err := os.ReadFile(cfg.ConfigPath)
	if err != nil {
		return watchdogTarget{}, err
	}
	var doc map[string]interface{}
	if err = yaml.Unmarshal(data, &doc); err != nil {
		return watchdogTarget{}, err
	}
	if controller, _ := doc["external-controller"].(string); controller != "" {
		secret, _ := doc["secret"].(string)
		return watchdogTarget{kind: "http", address: loopbackAddress(controller), secret: secret}, nil
	}
	if dns, _ := doc["dns"].(map[string]interface{}); dns != nil {
		if enabled, _ := dns["enable"].(bool); enabled {
			if listen, _ := dns["listen"].(string); listen != "" {
				return watchdogTarget{kind: "dns", address: loopbackAddress(listen)}, nil
			}
		}
	}
	return watchdogTarget{}, fmt.Errorf("no external-controller or dns.listen")
}

func probeWatchdogTarget(t watchdogTarget) error {
	if t.kind == "http" {
		return controllerAnswers(t.address, t.secret)
	}
	return dnsAnswers(t.address)
}

// controllerAnswers treats any HTTP response from mihomo's controller as alive:
// a 401 still proves the core is serving, and liveness is all that matters here.
func controllerAnswers(address, secret string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, "http://"+address+"/version", nil)
	if err != nil {
		return err
	}
	if secret != "" {
		req.Header.Set("Authorization", "Bearer "+secret)
	}
	client := http.Client{Transport: &http.Transport{Proxy: nil, DisableKeepAlives: true},
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("controller unreachable")
	}
	resp.Body.Close()
	return nil
}

// watchdogDNSAddress is the configured health address or the first UDP listener.
func watchdogDNSAddress(cfg *Config) (string, error) {
	if cfg.HealthDNSAddress != "" {
		return loopbackAddress(cfg.HealthDNSAddress), nil
	}
	data, err := os.ReadFile(cfg.ConfigPath)
	if err != nil {
		return "", err
	}
	var doc map[string]interface{}
	if err = yaml.Unmarshal(data, &doc); err != nil {
		return "", err
	}
	plugins, _ := doc["plugins"].([]interface{})
	for _, value := range plugins {
		plugin, _ := value.(map[string]interface{})
		if plugin["type"] != "udp_server" {
			continue
		}
		args, _ := plugin["args"].(map[string]interface{})
		if listen, _ := args["listen"].(string); listen != "" {
			return loopbackAddress(listen), nil
		}
	}
	return "", fmt.Errorf("no udp_server listener")
}

// dnsAnswers sends one UDP query and accepts any well-formed reply with the
// same ID: NXDOMAIN or SERVFAIL still prove the core is serving, and upstream
// outages must not look like a hung core.
func dnsAnswers(address string) error {
	conn, err := net.DialTimeout("udp", address, 2*time.Second)
	if err != nil {
		return err
	}
	defer conn.Close()
	var idBytes [2]byte
	if _, err = rand.Read(idBytes[:]); err != nil {
		return err
	}
	query := buildDNSQuery(binary.BigEndian.Uint16(idBytes[:]), watchdogProbeName)
	if err = conn.SetDeadline(time.Now().Add(2 * time.Second)); err != nil {
		return err
	}
	if _, err = conn.Write(query); err != nil {
		return err
	}
	reply := make([]byte, 1500)
	for {
		n, err := conn.Read(reply)
		if err != nil {
			return fmt.Errorf("no reply: %w", err)
		}
		// Header: ID, flags with QR set.
		if n >= 12 && reply[0] == query[0] && reply[1] == query[1] && reply[2]&0x80 != 0 {
			return nil
		}
	}
}

func buildDNSQuery(id uint16, name string) []byte {
	msg := make([]byte, 12, 64)
	binary.BigEndian.PutUint16(msg[0:], id)
	msg[2] = 0x01                          // RD
	binary.BigEndian.PutUint16(msg[4:], 1) // QDCOUNT
	start := 0
	for i := 0; i <= len(name); i++ {
		if i == len(name) || name[i] == '.' {
			if label := name[start:i]; label != "" {
				msg = append(msg, byte(len(label)))
				msg = append(msg, label...)
			}
			start = i + 1
		}
	}
	msg = append(msg, 0, 0, 1, 0, 1) // root, QTYPE A, QCLASS IN
	return msg
}
