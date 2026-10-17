package routes

import (
	"crypto/rand"
	"encoding/binary"
	"fmt"
	"log"
	"net"
	"os"
	"time"

	"gopkg.in/yaml.v3"
)

// DNS watchdog. The service manager (supervise-daemon, systemd) restarts a
// crashed core, but a hung one keeps its process alive while DNS goes silent.
// The watchdog probes the local listener and restarts the service when it
// stops answering, without fighting deployments or deliberate stops.

const watchdogProbeName = "configflow-watchdog.invalid."

type dnsWatchdog struct {
	cfg *Config

	interval    time.Duration
	failures    int           // consecutive failed probes before restarting
	grace       time.Duration // after a restart, time for the core to start
	maxRestarts int           // per window; beyond it only log
	window      time.Duration

	probe   func(address string) error
	running func(cfg *Config) (bool, error)
	lock    func(cfg *Config) (func(), error)
	restart func(cfg *Config) ([]byte, error)
	now     func() time.Time

	consecutive int
	restarts    []time.Time
	pausedUntil time.Time
	gaveUp      bool
}

func newDNSWatchdog(cfg *Config) *dnsWatchdog {
	return &dnsWatchdog{
		cfg: cfg, interval: 10 * time.Second, failures: 3, grace: 60 * time.Second,
		maxRestarts: 3, window: time.Hour,
		probe: dnsAnswers, running: serviceRunning, lock: AcquireServiceOperation,
		restart: RestartService, now: time.Now,
	}
}

// StartDNSWatchdog runs the watchdog for DNS services unless disabled.
func StartDNSWatchdog(cfg *Config) {
	if cfg.DNSWatchdogDisabled || (cfg.ServiceType != "oxidns" && cfg.ServiceType != "mosdns") {
		return
	}
	w := newDNSWatchdog(cfg)
	log.Printf("DNS watchdog enabled: probe every %s, restart after %d silent probes, at most %d restarts per %s",
		w.interval, w.failures, w.maxRestarts, w.window)
	go func() {
		ticker := time.NewTicker(w.interval)
		defer ticker.Stop()
		for range ticker.C {
			w.tick()
		}
	}()
}

// tick runs one probe round; it returns what it did, for tests and logs.
func (w *dnsWatchdog) tick() string {
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
	address, err := watchdogDNSAddress(w.cfg)
	if err != nil {
		return "no-address"
	}
	if err = w.probe(address); err == nil {
		if w.consecutive > 0 {
			log.Printf("DNS watchdog: %s answering again", address)
		}
		w.consecutive, w.gaveUp = 0, false
		return "ok"
	}
	w.consecutive++
	log.Printf("DNS watchdog: probe %d/%d to %s failed: %v", w.consecutive, w.failures, address, err)
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
			log.Printf("DNS watchdog: %d restarts within %s, not restarting again until the window passes", len(w.restarts), w.window)
			w.gaveUp = true
		}
		return "budget-exhausted"
	}
	log.Printf("DNS watchdog: %s is running but not answering, restarting %s", address, w.cfg.ServiceType)
	w.restarts = append(w.restarts, now)
	w.consecutive = 0
	w.pausedUntil = now.Add(w.grace)
	if _, err = w.restart(w.cfg); err != nil {
		log.Printf("DNS watchdog: restart failed: %v", err)
		return "restart-failed"
	}
	return "restarted"
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
