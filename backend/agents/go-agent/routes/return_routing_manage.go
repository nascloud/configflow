package routes

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"gopkg.in/yaml.v3"
)

// Managed return routing: ConfigFlow publishes the desired state, the Agent
// renders it into its own nft table plus a policy routing table and keeps it
// applied across restarts. Manual setups using the same mark/table can be
// adopted: their nft table is removed and their boot scripts are disabled, with
// enough saved to restore them on rollback.

const (
	managedReturnTable  = "configflow_return"
	returnStateFile     = "return-routing.json"
	returnLegacyDump    = "return-routing-legacy.nft"
	disabledScriptSufix = ".configflow-disabled"
)

// runCommandInput runs a command with stdin; replaceable in tests.
var runCommandInput = func(input string, name string, args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, name, args...)
	cmd.Stdin = strings.NewReader(input)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return string(out), fmt.Errorf("%s %s: %v: %s", name, strings.Join(args, " "), err, strings.TrimSpace(string(out)))
	}
	return string(out), nil
}

// writeSysctl and the local.d directory are replaceable in tests.
var (
	writeSysctl = func(path, value string) error { return os.WriteFile(path, []byte(value), 0644) }
	localDDir   = "/etc/local.d"
)

type ReturnRoutingSpec struct {
	RouterIP     string   `json:"router_ip"`
	ProxyDst     []string `json:"proxy_dst"`
	Mark         string   `json:"mark"`
	Table        int      `json:"table"`
	Priority     int      `json:"priority"`
	LanInterface string   `json:"lan_interface,omitempty"`
	TunDevice    string   `json:"tun_device,omitempty"`
	// AdoptLegacy lists manual nft tables ("inet mihomo_return") replaced by the managed one.
	AdoptLegacy []string `json:"adopt_legacy,omitempty"`
}

type legacyBackup struct {
	Tables  []string `json:"tables"`
	Dump    string   `json:"dump,omitempty"`
	Scripts []string `json:"scripts,omitempty"`
}

type returnRoutingState struct {
	Spec      ReturnRoutingSpec `json:"spec"`
	Legacy    *legacyBackup     `json:"legacy,omitempty"`
	AppliedAt string            `json:"applied_at"`
}

type resolvedSpec struct {
	ReturnRoutingSpec
	mark    uint32
	lanCIDR string
}

var tableNamePattern = regexp.MustCompile(`^(ip|ip6|inet) [A-Za-z0-9_-]+$`)

func parseMark(text string) (uint32, error) {
	value, err := strconv.ParseUint(strings.TrimSpace(text), 0, 32)
	if err != nil || value == 0 {
		return 0, fmt.Errorf("mark 无效：%q", text)
	}
	return uint32(value), nil
}

func validateSpec(spec ReturnRoutingSpec) (resolvedSpec, error) {
	resolved := resolvedSpec{ReturnRoutingSpec: spec}
	if ip := net.ParseIP(spec.RouterIP); ip == nil || ip.To4() == nil {
		return resolved, fmt.Errorf("router_ip 必须是 IPv4 地址")
	}
	mark, err := parseMark(spec.Mark)
	if err != nil {
		return resolved, err
	}
	resolved.mark = mark
	if spec.Table < 1 || spec.Table > 252 {
		return resolved, fmt.Errorf("table 必须在 1-252 之间")
	}
	if spec.Priority < 1 || spec.Priority > 32765 {
		return resolved, fmt.Errorf("priority 必须在 1-32765 之间")
	}
	if len(spec.ProxyDst) == 0 {
		return resolved, fmt.Errorf("proxy_dst 不能为空")
	}
	for _, cidr := range spec.ProxyDst {
		if _, network, err := net.ParseCIDR(cidr); err != nil || network.IP.To4() == nil {
			return resolved, fmt.Errorf("proxy_dst 只接受 IPv4 CIDR：%q", cidr)
		}
	}
	for _, name := range []string{spec.LanInterface, spec.TunDevice} {
		if name != "" && !regexp.MustCompile(`^[A-Za-z0-9_.-]{1,15}$`).MatchString(name) {
			return resolved, fmt.Errorf("网卡名无效：%q", name)
		}
	}
	for _, table := range spec.AdoptLegacy {
		if !tableNamePattern.MatchString(table) || strings.HasSuffix(table, " "+managedReturnTable) {
			return resolved, fmt.Errorf("adopt_legacy 表名无效：%q", table)
		}
	}
	return resolved, nil
}

// mihomoMarks reads marks and routing indexes from the mihomo config that must not collide.
type mihomoRouting struct {
	marks      map[string]uint32
	tunEnabled bool
	tunDevice  string
	tableIndex int
	ruleIndex  int
}

func readMihomoRouting(configPath string) mihomoRouting {
	result := mihomoRouting{marks: map[string]uint32{}, tunDevice: "Meta", tableIndex: 2022, ruleIndex: 9000}
	data, err := os.ReadFile(configPath)
	if err != nil {
		return result
	}
	var doc map[string]interface{}
	if yaml.Unmarshal(data, &doc) != nil {
		return result
	}
	if mark, ok := yamlUint(doc["routing-mark"]); ok {
		result.marks["routing-mark"] = mark
	}
	tun, _ := doc["tun"].(map[string]interface{})
	if tun == nil {
		return result
	}
	result.tunEnabled, _ = tun["enable"].(bool)
	if device, ok := tun["device"].(string); ok && device != "" {
		result.tunDevice = device
	}
	if value, ok := yamlUint(tun["iproute2-table-index"]); ok {
		result.tableIndex = int(value)
	}
	if value, ok := yamlUint(tun["iproute2-rule-index"]); ok {
		result.ruleIndex = int(value)
	}
	if redirect, _ := tun["auto-redirect"].(bool); redirect {
		result.marks["auto-redirect-input-mark"] = 0x2023
		result.marks["auto-redirect-output-mark"] = 0x2024
		for _, key := range []string{"auto-redirect-input-mark", "auto-redirect-output-mark"} {
			if mark, ok := yamlUint(tun[key]); ok {
				result.marks[key] = mark
			}
		}
	}
	return result
}

func yamlUint(value interface{}) (uint32, bool) {
	switch v := value.(type) {
	case int:
		return uint32(v), v > 0
	case string:
		mark, err := parseMark(v)
		return mark, err == nil
	}
	return 0, false
}

func checkMihomoConflicts(spec resolvedSpec, routing mihomoRouting) error {
	for key, mark := range routing.marks {
		if mark&spec.mark != 0 {
			return fmt.Errorf("mark 0x%x 与 mihomo %s 0x%x 有位重叠", spec.mark, key, mark)
		}
	}
	if spec.Table == routing.tableIndex {
		return fmt.Errorf("路由表 %d 已被 mihomo TUN 使用", spec.Table)
	}
	if spec.Priority >= routing.ruleIndex {
		return fmt.Errorf("策略路由优先级 %d 必须小于 mihomo 的 iproute2-rule-index %d", spec.Priority, routing.ruleIndex)
	}
	return nil
}

var (
	defaultRouteDev = regexp.MustCompile(`\bdev\s+(\S+)`)
	inetAddr        = regexp.MustCompile(`\binet\s+(\d+\.\d+\.\d+\.\d+/\d+)`)
)

// resolveInterfaces fills the LAN interface (the default route's device) and its CIDR.
func resolveInterfaces(spec *resolvedSpec, routing mihomoRouting) error {
	if spec.LanInterface == "" {
		out, err := runCommand("ip", "-4", "route", "show", "default")
		if err != nil {
			return fmt.Errorf("读取默认路由失败：%v", err)
		}
		for _, line := range strings.Split(out, "\n") {
			if m := defaultRouteDev.FindStringSubmatch(line); m != nil && !tunDeviceName.MatchString(m[1]) {
				spec.LanInterface = m[1]
				break
			}
		}
		if spec.LanInterface == "" {
			return fmt.Errorf("无法确定 LAN 网卡，请在期望状态中指定 lan_interface")
		}
	}
	out, err := runCommand("ip", "-4", "-o", "addr", "show", "dev", spec.LanInterface)
	if err != nil {
		return fmt.Errorf("读取网卡 %s 地址失败：%v", spec.LanInterface, err)
	}
	m := inetAddr.FindStringSubmatch(out)
	if m == nil {
		return fmt.Errorf("网卡 %s 没有 IPv4 地址", spec.LanInterface)
	}
	_, network, _ := net.ParseCIDR(m[1])
	spec.lanCIDR = network.String()
	if !network.Contains(net.ParseIP(spec.RouterIP)) {
		return fmt.Errorf("主路由 %s 不在 %s 的网段 %s 内", spec.RouterIP, spec.LanInterface, spec.lanCIDR)
	}
	if spec.TunDevice == "" && routing.tunEnabled {
		spec.TunDevice = routing.tunDevice
	}
	return nil
}

// renderReturnTable builds an atomic nft script that replaces the managed table.
func renderReturnTable(spec resolvedSpec) string {
	mark := fmt.Sprintf("0x%x", spec.mark)
	var b strings.Builder
	fmt.Fprintf(&b, "table inet %s {}\ndelete table inet %s\n", managedReturnTable, managedReturnTable)
	fmt.Fprintf(&b, "table inet %s {\n", managedReturnTable)
	fmt.Fprintf(&b, "\tset proxy_dst {\n\t\ttype ipv4_addr\n\t\tflags interval\n\t\telements = { %s }\n\t}\n",
		strings.Join(spec.ProxyDst, ", "))
	b.WriteString("\tchain prerouting {\n\t\ttype filter hook prerouting priority mangle; policy accept;\n")
	fmt.Fprintf(&b, "\t\tiifname %q ip daddr @proxy_dst ct state new ct mark set ct mark | %s comment \"configflow-return-mark\"\n",
		spec.LanInterface, mark)
	if spec.TunDevice != "" {
		fmt.Fprintf(&b, "\t\tiifname %q ct mark & %s == %s meta mark set meta mark | %s comment \"configflow-return-tun\"\n",
			spec.TunDevice, mark, mark, mark)
	}
	b.WriteString("\t}\n")
	b.WriteString("\tchain output {\n\t\ttype route hook output priority mangle; policy accept;\n")
	fmt.Fprintf(&b, "\t\tct mark & %s == %s meta mark set meta mark | %s comment \"configflow-return-local\"\n", mark, mark, mark)
	b.WriteString("\t}\n}\n")
	return b.String()
}

func deleteRulesAtPriority(priority int) {
	for i := 0; i < 16; i++ {
		if _, err := runCommand("ip", "rule", "del", "priority", strconv.Itoa(priority)); err != nil {
			return
		}
	}
}

func applyResolved(spec resolvedSpec) error {
	if _, err := runCommandInput(renderReturnTable(spec), "nft", "-f", "-"); err != nil {
		return err
	}
	table := strconv.Itoa(spec.Table)
	for _, dst := range []string{"default", spec.lanCIDR} {
		if _, err := runCommand("ip", "route", "replace", dst, "via", spec.RouterIP, "dev", spec.LanInterface, "table", table); err != nil {
			return fmt.Errorf("写入路由表 %s 失败：%v", table, err)
		}
	}
	deleteRulesAtPriority(spec.Priority)
	mark := fmt.Sprintf("0x%x", spec.mark)
	if _, err := runCommand("ip", "rule", "add", "fwmark", mark+"/"+mark, "lookup", table, "priority", strconv.Itoa(spec.Priority)); err != nil {
		return fmt.Errorf("添加策略路由失败：%v", err)
	}
	// Without this the gateway answers clients with ICMP redirects to the router.
	for _, iface := range []string{"all", spec.LanInterface} {
		if err := writeSysctl("/proc/sys/net/ipv4/conf/"+iface+"/send_redirects", "0"); err != nil {
			log.Printf("return routing: disable send_redirects on %s: %v", iface, err)
		}
	}
	return nil
}

func removeManaged(spec ReturnRoutingSpec) {
	runCommand("nft", "delete", "table", "inet", managedReturnTable)
	if spec.Priority > 0 {
		deleteRulesAtPriority(spec.Priority)
	}
	if spec.Table > 0 {
		runCommand("ip", "route", "flush", "table", strconv.Itoa(spec.Table))
	}
}

// adoptLegacy saves and removes manual tables, and disables boot scripts that recreate them.
func adoptLegacy(dir string, tables []string, previous *legacyBackup) (*legacyBackup, error) {
	if previous != nil && len(tables) == 0 {
		return previous, nil
	}
	backup := &legacyBackup{}
	if previous != nil {
		backup = previous
	}
	var dumps []string
	for _, table := range tables {
		parts := strings.Fields(table)
		dump, err := runCommand("nft", "list", "table", parts[0], parts[1])
		if err != nil {
			continue // already gone
		}
		dumps = append(dumps, dump)
		backup.Tables = append(backup.Tables, table)
	}
	if len(dumps) > 0 {
		backup.Dump = filepath.Join(dir, returnLegacyDump)
		if err := os.WriteFile(backup.Dump, []byte(strings.Join(dumps, "\n")), 0600); err != nil {
			return nil, fmt.Errorf("保存原有回流规则失败：%v", err)
		}
	}
	starts, _ := filepath.Glob(filepath.Join(localDDir, "*.start"))
	stops, _ := filepath.Glob(filepath.Join(localDDir, "*.stop"))
	for _, script := range append(starts, stops...) {
		if scriptManagesTables(script, backup.Tables) {
			if err := os.Rename(script, script+disabledScriptSufix); err == nil {
				backup.Scripts = append(backup.Scripts, script)
			}
		}
	}
	for _, table := range tables {
		parts := strings.Fields(table)
		runCommand("nft", "delete", "table", parts[0], parts[1])
	}
	if len(backup.Tables) == 0 && len(backup.Scripts) == 0 {
		return previous, nil
	}
	return backup, nil
}

var nftFileRef = regexp.MustCompile(`\S+\.nft\b`)

// scriptManagesTables reports whether a boot script names one of the tables,
// directly or through an nft file it loads.
func scriptManagesTables(script string, tables []string) bool {
	data, err := os.ReadFile(script)
	if err != nil {
		return false
	}
	texts := []string{string(data)}
	for _, ref := range nftFileRef.FindAllString(string(data), -1) {
		if content, err := os.ReadFile(ref); err == nil {
			texts = append(texts, string(content))
		}
	}
	for _, table := range tables {
		name := strings.Fields(table)[1]
		pattern := regexp.MustCompile(`\b` + regexp.QuoteMeta(name) + `\b`)
		for _, text := range texts {
			if pattern.MatchString(text) {
				return true
			}
		}
	}
	return false
}

func restoreLegacy(backup *legacyBackup) []string {
	var problems []string
	if backup == nil {
		return problems
	}
	if backup.Dump != "" {
		if data, err := os.ReadFile(backup.Dump); err == nil {
			if _, err := runCommandInput(string(data), "nft", "-f", "-"); err != nil {
				problems = append(problems, err.Error())
			}
		}
	}
	for _, script := range backup.Scripts {
		if err := os.Rename(script+disabledScriptSufix, script); err != nil {
			problems = append(problems, err.Error())
			continue
		}
		if !strings.HasSuffix(script, ".start") {
			continue
		}
		if _, err := runCommand("sh", script); err != nil {
			problems = append(problems, err.Error())
		}
	}
	return problems
}

func returnStatePath(cfg *Config) (string, error) {
	if cfg == nil || cfg.AgentConfigFile == "" {
		return "", fmt.Errorf("Agent 配置文件路径未知，无法保存回流规则")
	}
	return filepath.Join(filepath.Dir(cfg.AgentConfigFile), returnStateFile), nil
}

func loadReturnState(cfg *Config) (*returnRoutingState, error) {
	path, err := returnStatePath(cfg)
	if err != nil {
		return nil, err
	}
	data, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var state returnRoutingState
	if err := json.Unmarshal(data, &state); err != nil {
		return nil, fmt.Errorf("回流规则状态文件损坏：%v", err)
	}
	return &state, nil
}

func saveReturnState(cfg *Config, state *returnRoutingState) error {
	path, err := returnStatePath(cfg)
	if err != nil {
		return err
	}
	data, _ := json.MarshalIndent(state, "", "  ")
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0600); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func prepareSpec(cfg *Config, spec ReturnRoutingSpec) (resolvedSpec, error) {
	resolved, err := validateSpec(spec)
	if err != nil {
		return resolved, err
	}
	if _, err := runCommand("nft", "--version"); err != nil {
		return resolved, fmt.Errorf("缺少 nft（nftables），无法管理回流规则")
	}
	if _, err := runCommand("ip", "-V"); err != nil {
		return resolved, fmt.Errorf("缺少 ip（iproute2），无法管理回流规则")
	}
	routing := readMihomoRouting(cfg.ConfigPath)
	if err := checkMihomoConflicts(resolved, routing); err != nil {
		return resolved, err
	}
	if err := resolveInterfaces(&resolved, routing); err != nil {
		return resolved, err
	}
	return resolved, nil
}

// ApplyReturnRouting applies the spec, adopts legacy tables and persists the state.
func ApplyReturnRouting(cfg *Config, spec ReturnRoutingSpec) (map[string]interface{}, error) {
	resolved, err := prepareSpec(cfg, spec)
	if err != nil {
		return nil, err
	}
	previous, err := loadReturnState(cfg)
	if err != nil {
		return nil, err
	}
	statePath, _ := returnStatePath(cfg)
	if err := applyResolved(resolved); err != nil {
		removeManaged(resolved.ReturnRoutingSpec)
		if previous != nil {
			if prev, perr := prepareSpec(cfg, previous.Spec); perr == nil {
				applyResolved(prev)
			}
		}
		return nil, err
	}
	// The managed table now marks the same traffic, so dropping the manual one is lossless.
	var legacy *legacyBackup
	if previous != nil {
		legacy = previous.Legacy
	}
	legacy, err = adoptLegacy(filepath.Dir(statePath), spec.AdoptLegacy, legacy)
	if err != nil {
		return nil, err
	}
	if previous != nil && previous.Spec.Priority != spec.Priority {
		deleteRulesAtPriority(previous.Spec.Priority)
	}
	if previous != nil && previous.Spec.Table != spec.Table {
		runCommand("ip", "route", "flush", "table", strconv.Itoa(previous.Spec.Table))
	}
	saved := spec
	saved.AdoptLegacy = nil
	state := &returnRoutingState{Spec: saved, Legacy: legacy, AppliedAt: time.Now().Format(time.RFC3339)}
	if err := saveReturnState(cfg, state); err != nil {
		return nil, fmt.Errorf("回流规则已生效，但保存状态失败（重启后不会自动恢复）：%v", err)
	}
	return map[string]interface{}{
		"success": true, "lan_interface": resolved.LanInterface, "lan_cidr": resolved.lanCIDR,
		"tun_device": resolved.TunDevice, "adopted": legacy,
	}, nil
}

// RemoveReturnRouting deletes the managed rules; restoreLegacy brings back adopted manual rules.
func RemoveReturnRouting(cfg *Config, restore bool) (map[string]interface{}, error) {
	state, err := loadReturnState(cfg)
	if err != nil {
		return nil, err
	}
	if state == nil {
		return map[string]interface{}{"success": true, "removed": false}, nil
	}
	removeManaged(state.Spec)
	var problems []string
	if restore {
		problems = restoreLegacy(state.Legacy)
	}
	path, _ := returnStatePath(cfg)
	if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
		problems = append(problems, err.Error())
	}
	if state.Legacy != nil && state.Legacy.Dump != "" && restore {
		os.Remove(state.Legacy.Dump)
	}
	return map[string]interface{}{"success": len(problems) == 0, "removed": true, "problems": problems,
		"message": strings.Join(problems, "; ")}, nil
}

// ReapplyReturnRouting restores persisted rules at Agent start; retries while the network settles.
func ReapplyReturnRouting(cfg *Config) {
	state, err := loadReturnState(cfg)
	if err != nil {
		log.Printf("return routing: %v", err)
		return
	}
	if state == nil {
		return
	}
	go func() {
		for attempt := 1; attempt <= 6; attempt++ {
			resolved, err := prepareSpec(cfg, state.Spec)
			if err == nil {
				err = applyResolved(resolved)
			}
			if err == nil {
				log.Printf("return routing: re-applied (table %d, mark %s)", state.Spec.Table, state.Spec.Mark)
				return
			}
			log.Printf("return routing: re-apply attempt %d failed: %v", attempt, err)
			time.Sleep(10 * time.Second)
		}
	}()
}

func managedReturnStatus(cfg *Config) map[string]interface{} {
	state, err := loadReturnState(cfg)
	if err != nil {
		return map[string]interface{}{"error": err.Error()}
	}
	if state == nil {
		return nil
	}
	_, tableErr := runCommand("nft", "list", "table", "inet", managedReturnTable)
	return map[string]interface{}{"spec": state.Spec, "applied_at": state.AppliedAt,
		"table_present": tableErr == nil, "legacy": state.Legacy}
}

func returnRoutingWrite(cfg *Config, w http.ResponseWriter, r *http.Request) {
	var (
		result map[string]interface{}
		err    error
	)
	if r.Method == http.MethodPut {
		var spec ReturnRoutingSpec
		decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20))
		decoder.DisallowUnknownFields()
		if err = decoder.Decode(&spec); err != nil {
			JsonResponse(w, http.StatusBadRequest, map[string]interface{}{"success": false, "message": "请求格式错误：" + err.Error()})
			return
		}
		result, err = ApplyReturnRouting(cfg, spec)
	} else {
		result, err = RemoveReturnRouting(cfg, r.URL.Query().Get("restore_legacy") == "1")
	}
	if err != nil {
		JsonResponse(w, http.StatusUnprocessableEntity, map[string]interface{}{"success": false, "message": err.Error()})
		return
	}
	JsonResponse(w, http.StatusOK, result)
}
