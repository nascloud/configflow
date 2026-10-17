package routes

import (
	"bytes"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type fakeHost struct {
	noNeigh  bool
	commands []string
	inputs   []string
	tables   map[string]string // "inet name" -> dump
	failNft  bool
}

func installFakeHost(t *testing.T, host *fakeHost) {
	t.Helper()
	oldRun, oldInput, oldSysctl, oldLocalD := runCommand, runCommandInput, writeSysctl, localDDir
	t.Cleanup(func() { runCommand, runCommandInput, writeSysctl, localDDir = oldRun, oldInput, oldSysctl, oldLocalD })
	writeSysctl = func(string, string) error { return nil }
	runCommand = func(name string, args ...string) (string, error) {
		line := strings.Join(append([]string{name}, args...), " ")
		host.commands = append(host.commands, line)
		switch {
		case line == "nft --version" || line == "ip -V":
			return "ok", nil
		case line == "ip -4 route show default":
			return "default via 192.168.1.1 dev eth0 \n", nil
		case line == "ip -4 -o addr show dev eth0":
			return "2: eth0    inet 192.168.1.4/24 brd 192.168.1.255 scope global eth0\n", nil
		case line == "ip neigh show 192.168.1.1 dev eth0":
			if host.noNeigh {
				return "", nil
			}
			return "192.168.1.1 lladdr 00:E0:B4:66:D5:F9 REACHABLE\n", nil
		case strings.HasPrefix(line, "nft list table "):
			if dump, ok := host.tables[strings.TrimPrefix(line, "nft list table ")]; ok {
				return dump, nil
			}
			return "", errors.New("no such table")
		case strings.HasPrefix(line, "nft delete table "):
			delete(host.tables, strings.TrimPrefix(line, "nft delete table "))
			return "", nil
		case strings.HasPrefix(line, "ip rule del"):
			return "", errors.New("no such rule")
		}
		return "", nil
	}
	runCommandInput = func(input string, name string, args ...string) (string, error) {
		host.inputs = append(host.inputs, input)
		if host.failNft {
			return "", errors.New("nft: syntax error")
		}
		if strings.Contains(input, "table inet "+managedReturnTable+" {\n") {
			host.tables["inet "+managedReturnTable] = input
		}
		if strings.Contains(input, "table inet mihomo_return") {
			host.tables["inet mihomo_return"] = input
		}
		return "", nil
	}
}

func testConfig(t *testing.T, mihomo string) *Config {
	t.Helper()
	dir := t.TempDir()
	configPath := filepath.Join(dir, "config.yaml")
	if err := os.WriteFile(configPath, []byte(mihomo), 0644); err != nil {
		t.Fatal(err)
	}
	return &Config{AgentConfigFile: filepath.Join(dir, "config-mihomo.json"), ConfigPath: configPath}
}

const tunConfig = "tun:\n  enable: true\n  device: Meta\n  auto-route: true\n  auto-redirect: true\n"

func sampleSpec() ReturnRoutingSpec {
	return ReturnRoutingSpec{RouterIP: "192.168.1.1", ProxyDst: []string{"198.18.0.0/16", "91.108.0.0/16"},
		Mark: "0x400000", Table: 104, Priority: 104}
}

func TestValidateSpec(t *testing.T) {
	cases := map[string]func(*ReturnRoutingSpec){
		"router":   func(s *ReturnRoutingSpec) { s.RouterIP = "router" },
		"mark":     func(s *ReturnRoutingSpec) { s.Mark = "0" },
		"table":    func(s *ReturnRoutingSpec) { s.Table = 254 },
		"dst":      func(s *ReturnRoutingSpec) { s.ProxyDst = []string{"2001:db8::/32"} },
		"empty":    func(s *ReturnRoutingSpec) { s.ProxyDst = nil },
		"iface":    func(s *ReturnRoutingSpec) { s.LanInterface = "eth0; reboot" },
		"legacy":   func(s *ReturnRoutingSpec) { s.AdoptLegacy = []string{"inet x; flush ruleset"} },
		"self":     func(s *ReturnRoutingSpec) { s.AdoptLegacy = []string{"inet " + managedReturnTable} },
		"priority": func(s *ReturnRoutingSpec) { s.Priority = 0 },
	}
	for name, mutate := range cases {
		spec := sampleSpec()
		mutate(&spec)
		if _, err := validateSpec(spec); err == nil {
			t.Errorf("%s: expected validation error", name)
		}
	}
	if _, err := validateSpec(sampleSpec()); err != nil {
		t.Fatal(err)
	}
}

func TestMihomoConflicts(t *testing.T) {
	cfg := testConfig(t, tunConfig+"routing-mark: 6666\n")
	routing := readMihomoRouting(cfg.ConfigPath)
	if !routing.tunEnabled || routing.tunDevice != "Meta" {
		t.Fatalf("routing = %+v", routing)
	}
	spec, _ := validateSpec(sampleSpec())
	if err := checkMihomoConflicts(spec, routing); err != nil {
		t.Fatalf("0x400000 must not collide: %v", err)
	}
	overlap := sampleSpec()
	overlap.Mark = "0x4" // auto-redirect 0x2024 contains this bit
	spec, _ = validateSpec(overlap)
	if err := checkMihomoConflicts(spec, routing); err == nil || !strings.Contains(err.Error(), "auto-redirect") {
		t.Fatalf("expected auto-redirect overlap, got %v", err)
	}
	spec, _ = validateSpec(sampleSpec())
	spec.Table = 2022
	if err := checkMihomoConflicts(spec, routing); err == nil {
		t.Fatal("expected table conflict")
	}
}

func TestRenderReturnTable(t *testing.T) {
	spec, _ := validateSpec(sampleSpec())
	spec.LanInterface, spec.TunDevice, spec.routerMAC = "eth0", "Meta", "00:e0:b4:66:d5:f9"
	script := renderReturnTable(spec)
	for _, want := range []string{
		"table inet configflow_return {}\ndelete table inet configflow_return\n",
		"elements = { 198.18.0.0/16, 91.108.0.0/16 }",
		// Only router-forwarded connections; redirected/direct clients reply directly.
		`iifname "eth0" ether saddr 00:e0:b4:66:d5:f9 ip daddr @proxy_dst ct state new ct mark set ct mark | 0x400000`,
		`iifname "Meta" ct mark & 0x400000 == 0x400000 meta mark set meta mark | 0x400000`,
		"type route hook output priority mangle",
	} {
		if !strings.Contains(script, want) {
			t.Errorf("script missing %q:\n%s", want, script)
		}
	}
	spec.TunDevice = ""
	if strings.Contains(renderReturnTable(spec), "configflow-return-tun") {
		t.Error("TUN restore rule must be omitted without a TUN device")
	}
}

func TestApplyAdoptsLegacyAndRemoveRestores(t *testing.T) {
	host := &fakeHost{tables: map[string]string{"inet mihomo_return": "table inet mihomo_return {\n}\n"}}
	installFakeHost(t, host)
	localD := t.TempDir()
	localDDir = localD
	nftFile := filepath.Join(localD, "mihomo-return.nft")
	os.WriteFile(nftFile, []byte("table inet mihomo_return {}\n"), 0644)
	os.WriteFile(filepath.Join(localD, "mihomo-return.start"), []byte("nft -f "+nftFile+"\n"), 0755)
	os.WriteFile(filepath.Join(localD, "mihomo-return.stop"), []byte("nft destroy table inet mihomo_return\n"), 0755)
	os.WriteFile(filepath.Join(localD, "other.start"), []byte("echo hi\n"), 0755)
	cfg := testConfig(t, tunConfig)

	spec := sampleSpec()
	spec.AdoptLegacy = []string{"inet mihomo_return"}
	result, err := ApplyReturnRouting(cfg, spec)
	if err != nil {
		t.Fatal(err)
	}
	if result["lan_interface"] != "eth0" || result["lan_cidr"] != "192.168.1.0/24" || result["tun_device"] != "Meta" || result["router_mac"] != "00:e0:b4:66:d5:f9" {
		t.Fatalf("result = %v", result)
	}
	joined := strings.Join(host.commands, "\n")
	for _, want := range []string{
		"ip route replace default via 192.168.1.1 dev eth0 table 104",
		"ip route replace 192.168.1.0/24 via 192.168.1.1 dev eth0 table 104",
		"ip rule add fwmark 0x400000/0x400000 lookup 104 priority 104",
		"nft delete table inet mihomo_return",
	} {
		if !strings.Contains(joined, want) {
			t.Errorf("missing command %q", want)
		}
	}
	// The managed table must exist before the manual one is dropped.
	if strings.Index(joined, "ip rule add") > strings.Index(joined, "nft delete table inet mihomo_return") {
		t.Error("legacy table removed before managed rules were applied")
	}
	if _, ok := host.tables["inet mihomo_return"]; ok {
		t.Error("legacy table still present")
	}
	for _, name := range []string{"mihomo-return.start", "mihomo-return.stop"} {
		if _, err := os.Stat(filepath.Join(localD, name+disabledScriptSufix)); err != nil {
			t.Errorf("%s not disabled: %v", name, err)
		}
	}
	if _, err := os.Stat(filepath.Join(localD, "other.start")); err != nil {
		t.Error("unrelated boot script must stay untouched")
	}
	state, err := loadReturnState(cfg)
	if err != nil || state == nil || state.Legacy == nil || len(state.Legacy.Scripts) != 2 || state.Spec.AdoptLegacy != nil {
		t.Fatalf("state = %+v, %v", state, err)
	}
	status := managedReturnStatus(cfg)
	if status["table_present"] != true {
		t.Fatalf("status = %v", status)
	}

	result, err = RemoveReturnRouting(cfg, true)
	if err != nil || result["success"] != true {
		t.Fatalf("remove = %v, %v", result, err)
	}
	if _, ok := host.tables["inet "+managedReturnTable]; ok {
		t.Error("managed table still present after remove")
	}
	if _, ok := host.tables["inet mihomo_return"]; !ok {
		t.Error("legacy table not restored")
	}
	if _, err := os.Stat(filepath.Join(localD, "mihomo-return.start")); err != nil {
		t.Error("legacy boot script not restored")
	}
	if !strings.Contains(strings.Join(host.commands, "\n"), "sh "+filepath.Join(localD, "mihomo-return.start")) {
		t.Error("legacy start script not re-run")
	}
	if state, _ := loadReturnState(cfg); state != nil {
		t.Error("state file not removed")
	}
}

func TestApplyFailureCleansUp(t *testing.T) {
	host := &fakeHost{tables: map[string]string{}, failNft: true}
	installFakeHost(t, host)
	localDDir = t.TempDir()
	cfg := testConfig(t, tunConfig)
	if _, err := ApplyReturnRouting(cfg, sampleSpec()); err == nil {
		t.Fatal("expected failure")
	}
	if !strings.Contains(strings.Join(host.commands, "\n"), "ip route flush table 104") {
		t.Error("partial apply not cleaned up")
	}
	if state, _ := loadReturnState(cfg); state != nil {
		t.Error("failed apply must not persist state")
	}
}

func TestReturnRoutingHandlerPutAndDelete(t *testing.T) {
	host := &fakeHost{tables: map[string]string{}}
	installFakeHost(t, host)
	localDDir = t.TempDir()
	cfg := testConfig(t, tunConfig)
	body, _ := json.Marshal(sampleSpec())
	recorder := httptest.NewRecorder()
	ReturnRoutingHandler(cfg)(recorder, httptest.NewRequest(http.MethodPut, "/api/return-routing", bytes.NewReader(body)))
	if recorder.Code != http.StatusOK {
		t.Fatalf("put code = %d body = %s", recorder.Code, recorder.Body.String())
	}
	recorder = httptest.NewRecorder()
	ReturnRoutingHandler(cfg)(recorder, httptest.NewRequest(http.MethodPut, "/api/return-routing", strings.NewReader(`{"bogus":1}`)))
	if recorder.Code != http.StatusBadRequest {
		t.Fatalf("unknown fields must be rejected, code = %d", recorder.Code)
	}
	recorder = httptest.NewRecorder()
	ReturnRoutingHandler(cfg)(recorder, httptest.NewRequest(http.MethodDelete, "/api/return-routing", nil))
	if recorder.Code != http.StatusOK || !strings.Contains(recorder.Body.String(), `"removed":true`) {
		t.Fatalf("delete code = %d body = %s", recorder.Code, recorder.Body.String())
	}
}

func TestApplyRequiresRouterMAC(t *testing.T) {
	host := &fakeHost{tables: map[string]string{}, noNeigh: true}
	installFakeHost(t, host)
	localDDir = t.TempDir()
	cfg := testConfig(t, tunConfig)
	if _, err := ApplyReturnRouting(cfg, sampleSpec()); err == nil || !strings.Contains(err.Error(), "MAC") {
		t.Fatalf("expected MAC resolution error, got %v", err)
	}
	joined := strings.Join(host.commands, "\n")
	if !strings.Contains(joined, "ping -c 1 -W 1 192.168.1.1") {
		t.Error("router MAC lookup should ping once to populate the neighbor table")
	}
	if strings.Contains(joined, "ip rule add") || len(host.inputs) != 0 {
		t.Error("nothing may be applied without the router MAC")
	}
}
