package routes

// Migration of return-routing state written by Agent 1.8.2-1.8.4.
//
// Those versions recorded only the spec, so their resources carry no ownership
// evidence (no proto 242 routes/rules, no token-tagged nft objects) and the
// owned model refuses to touch them. Rather than leaving such hosts without a
// boot replay, the old resources are migrated once, but only after proving
// that the host holds exactly what that version created for the recorded spec:
// two routes in the spec's table, one fwmark rule at its priority, and the
// configflow_return table with its fixed set/chains/rule comments. Anything
// else is preserved untouched, as before.
//
// The swap keeps marking and policy routing in place throughout: owned routes
// are added next to the old ones (higher metric, so inactive until the old
// ones go), the rule is replaced back to back, and the nft table is replaced
// in a single atomic transaction.

import (
	"fmt"
	"net"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"
)

var legacyReturnRuleComments = map[string]string{
	"configflow-return-mark":  "prerouting",
	"configflow-return-tun":   "prerouting",
	"configflow-return-local": "output",
}

// legacyReturnResources are the verified objects created by Agent 1.8.2-1.8.4.
type legacyReturnResources struct {
	routes []map[string]interface{}
	rule   map[string]interface{}
	nft    []map[string]interface{}
}

func legacySetCIDRs(raw interface{}) ([]string, error) {
	elements, ok := raw.([]interface{})
	if !ok {
		return nil, fmt.Errorf("集合元素格式无法识别")
	}
	cidrs := []string{}
	for _, element := range elements {
		switch value := element.(type) {
		case string:
			ip := net.ParseIP(value)
			if ip == nil || ip.To4() == nil {
				return nil, fmt.Errorf("集合元素 %q 不是 IPv4 地址", value)
			}
			cidrs = append(cidrs, ip.String()+"/32")
		case map[string]interface{}:
			prefix, ok := value["prefix"].(map[string]interface{})
			if !ok {
				return nil, fmt.Errorf("集合元素格式无法识别")
			}
			addr, _ := prefix["addr"].(string)
			length, ok := resourceNumber(prefix["len"])
			if !ok {
				return nil, fmt.Errorf("集合前缀长度无效")
			}
			_, network, err := net.ParseCIDR(addr + "/" + strconv.FormatUint(length, 10))
			if err != nil {
				return nil, err
			}
			cidrs = append(cidrs, network.String())
		default:
			return nil, fmt.Errorf("集合元素格式无法识别")
		}
	}
	sort.Strings(cidrs)
	return cidrs, nil
}

func normalizedCIDRs(values []string) []string {
	result := []string{}
	for _, value := range values {
		if _, network, err := net.ParseCIDR(value); err == nil {
			result = append(result, network.String())
		}
	}
	sort.Strings(result)
	return result
}

// verifyLegacyReturn proves the host holds exactly the old version's objects
// for spec, and that nothing else would conflict once they are replaced.
func verifyLegacyReturn(spec resolvedSpec, resources *returnResources) (*legacyReturnResources, error) {
	legacy := &legacyReturnResources{}
	others := &returnResources{VRFs: resources.VRFs}

	wantDst := map[string]bool{"default": true, spec.lanCIDR: true}
	for _, route := range resources.Routes {
		table, _ := resourceTable(route)
		if table != uint64(spec.Table) {
			others.Routes = append(others.Routes, route)
			continue
		}
		dst, _ := route["dst"].(string)
		proto, hasProto := resourceNumber(route["protocol"])
		metric, _ := resourceNumber(route["metric"])
		if !wantDst[dst] || route["gateway"] != spec.RouterIP || route["dev"] != spec.LanInterface ||
			(hasProto && proto == returnProtocol) || metric != 0 {
			return nil, fmt.Errorf("路由表 %d 中的路由与旧版回流规格不一致", spec.Table)
		}
		delete(wantDst, dst)
		legacy.routes = append(legacy.routes, route)
	}
	if len(wantDst) != 0 || len(legacy.routes) != 2 {
		return nil, fmt.Errorf("路由表 %d 缺少旧版回流路由", spec.Table)
	}

	for _, rule := range resources.Rules {
		priority, _ := resourceNumber(rule["priority"])
		if priority != uint64(spec.Priority) {
			others.Rules = append(others.Rules, rule)
			continue
		}
		table, tableOK := resourceNumber(rule["table"])
		mark, mask, err := ruleMark(rule)
		proto, hasProto := resourceNumber(rule["protocol"])
		if legacy.rule != nil || err != nil || !tableOK || table != uint64(spec.Table) || rule["src"] != "all" ||
			mark != spec.mark || mask != spec.mark || (hasProto && proto == returnProtocol) || len(rule) > 6 {
			return nil, fmt.Errorf("优先级 %d 的策略规则与旧版回流规格不一致", spec.Priority)
		}
		legacy.rule = rule
	}
	if legacy.rule == nil {
		return nil, fmt.Errorf("缺少旧版回流策略规则")
	}

	chains, comments, tables, sets := map[string]bool{}, map[string]bool{}, 0, 0
	for _, object := range resources.NFT {
		if !nftObjectIsManaged(object) {
			others.NFT = append(others.NFT, object)
			continue
		}
		legacy.nft = append(legacy.nft, object)
		for kind, raw := range object {
			body, _ := raw.(map[string]interface{})
			switch kind {
			case "table":
				if body["comment"] != nil {
					return nil, fmt.Errorf("nft 表 %s 带有归属注释，不是旧版创建的", managedReturnTable)
				}
				tables++
			case "set":
				cidrs, err := legacySetCIDRs(body["elem"])
				if err != nil {
					return nil, err
				}
				if body["name"] != "proxy_dst" || strings.Join(cidrs, ",") != strings.Join(normalizedCIDRs(spec.ProxyDst), ",") {
					return nil, fmt.Errorf("nft 集合与旧版回流规格不一致")
				}
				sets++
			case "chain":
				name, _ := body["name"].(string)
				if (name != "prerouting" && name != "output") || chains[name] {
					return nil, fmt.Errorf("nft 表 %s 中有旧版之外的链", managedReturnTable)
				}
				chains[name] = true
			case "rule":
				comment, _ := body["comment"].(string)
				chain, ok := legacyReturnRuleComments[comment]
				if !ok || body["chain"] != chain || comments[comment] {
					return nil, fmt.Errorf("nft 表 %s 中有旧版之外的规则", managedReturnTable)
				}
				comments[comment] = true
			default:
				return nil, fmt.Errorf("nft 表 %s 中有旧版之外的对象", managedReturnTable)
			}
		}
	}
	if tables != 1 || sets != 1 || len(chains) != 2 || !comments["configflow-return-mark"] || !comments["configflow-return-local"] {
		return nil, fmt.Errorf("nft 表 %s 与旧版回流结构不一致", managedReturnTable)
	}
	// Everything outside the old objects must already satisfy the owned model.
	if err := checkReturnResources(spec, nil, others); err != nil {
		return nil, fmt.Errorf("旧版资源之外存在冲突：%w", err)
	}
	return legacy, nil
}

func legacyRouteArguments(action string, spec resolvedSpec, route map[string]interface{}) []string {
	dst, _ := route["dst"].(string)
	args := []string{"-4", "route", action, dst, "via", spec.RouterIP, "dev", spec.LanInterface,
		"table", strconv.Itoa(spec.Table), "metric", "0"}
	if proto, ok := resourceNumber(route["protocol"]); ok {
		args = append(args, "proto", strconv.FormatUint(proto, 10))
	}
	return args
}

func legacyRuleArguments(action string, spec resolvedSpec) []string {
	mark := fmt.Sprintf("0x%x", spec.mark)
	return []string{"-4", "rule", action, "priority", strconv.Itoa(spec.Priority), "from", "all",
		"fwmark", mark + "/" + mark, "lookup", strconv.Itoa(spec.Table)}
}

// migrateLegacyReturnState converts a verified 1.8.2-1.8.4 state to owned
// resources. On refusal or early failure the host and state file are unchanged.
func migrateLegacyReturnState(cfg *Config, state *returnRoutingState) error {
	unlock, err := lockReturnJournal(cfg)
	if err != nil {
		return err
	}
	defer unlock()
	path, err := returnStatePath(cfg)
	if err != nil {
		return err
	}
	original, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	input := state.Spec
	input.SafetyVersion, input.AdoptLegacy = 1, nil
	spec, err := prepareSpec(cfg, input)
	if err != nil {
		return err
	}
	resources, err := readReturnResources()
	if err != nil {
		return err
	}
	legacy, err := verifyLegacyReturn(spec, resources)
	if err != nil {
		return err
	}

	owner, err := newReturnOwner()
	if err != nil {
		return err
	}
	migrated := &returnRoutingState{Spec: spec.ReturnRoutingSpec, Ownership: owner,
		MigratedLegacy: state.Legacy, AppliedAt: state.AppliedAt}
	if err = saveReturnState(cfg, migrated); err != nil {
		return err
	}

	ruleSwapped := false
	rollback := func(cause error) error {
		// Before the nft swap every old object is still live; undo our additions.
		for i := len(owner.Rules) - 1; i >= 0; i-- {
			if args, argErr := ruleArguments("del", owner.Rules[i]); argErr == nil {
				runCommand("ip", args...)
			}
		}
		if ruleSwapped {
			runCommand("ip", legacyRuleArguments("add", spec)...)
		}
		for i := len(owner.Routes) - 1; i >= 0; i-- {
			if args, argErr := routeArguments("del", owner.Routes[i]); argErr == nil {
				runCommand("ip", args...)
			}
		}
		if writeErr := os.WriteFile(path, original, 0600); writeErr != nil {
			return fmt.Errorf("%v；恢复原状态文件失败：%v", cause, writeErr)
		}
		return cause
	}

	// 1. Owned routes beside the old ones; the old metric-0 routes stay preferred.
	for _, dst := range []string{"default", spec.lanCIDR} {
		expected := map[string]interface{}{"dst": dst, "gateway": spec.RouterIP, "dev": spec.LanInterface,
			"table": spec.Table, "protocol": returnProtocol, "metric": owner.Metric, "flags": []interface{}{}}
		args, argErr := routeArguments("add", expected)
		if argErr != nil {
			return rollback(argErr)
		}
		if _, err = runCommand("ip", args...); err != nil {
			return rollback(fmt.Errorf("添加受管路由失败：%w", err))
		}
		after, readErr := readReturnResources()
		if readErr != nil {
			return rollback(readErr)
		}
		match, matchErr := matchingResource(expected, after.Routes)
		if matchErr != nil || match == nil {
			return rollback(fmt.Errorf("受管路由写入后无法确认"))
		}
		owner.Routes = append(owner.Routes, match)
	}

	// 2. Replace the rule. The old one must go first: it differs from the
	// owned rule only by protocol, so deleting it later could hit ours.
	if _, err = runCommand("ip", legacyRuleArguments("del", spec)...); err != nil {
		return rollback(fmt.Errorf("删除旧版策略规则失败：%w", err))
	}
	ruleSwapped = true
	expectedRule := map[string]interface{}{"priority": spec.Priority, "src": "all", "fwmark": fmt.Sprintf("0x%x", spec.mark),
		"fwmask": fmt.Sprintf("0x%x", spec.mark), "table": spec.Table, "protocol": returnProtocol}
	args, err := ruleArguments("add", expectedRule)
	if err != nil {
		return rollback(err)
	}
	if _, err = runCommand("ip", args...); err != nil {
		return rollback(fmt.Errorf("添加受管策略规则失败：%w", err))
	}
	after, err := readReturnResources()
	if err != nil {
		return rollback(err)
	}
	ruleMatch, err := matchingResource(expectedRule, after.Rules)
	if err != nil || ruleMatch == nil {
		return rollback(fmt.Errorf("受管策略规则写入后无法确认"))
	}
	owner.Rules = append(owner.Rules, ruleMatch)

	// 3. Replace the verified old table atomically with the owned one.
	objects := desiredReturnNFT(spec, owner)
	commands := []map[string]interface{}{{"delete": nftObject("table", map[string]interface{}{
		"family": "inet", "name": managedReturnTable})}}
	for _, object := range objects {
		verb := "create"
		if _, isRule := object["rule"]; isRule {
			verb = "add"
		}
		commands = append(commands, map[string]interface{}{verb: object})
	}
	if err = runNFT(commands); err != nil {
		return rollback(fmt.Errorf("替换 nft 表失败（事务未生效）：%w", err))
	}
	after, err = readReturnResources()
	if err == nil {
		owner.NFT, err = confirmResources(objects, ownedNftObjects(after))
	}
	if err != nil {
		// The swap committed; keep the journal pending so nothing replays blindly.
		saveReturnState(cfg, migrated)
		return fmt.Errorf("nft 已替换但回读失败，保留未完成的迁移记录：%w", err)
	}
	if err = saveReturnState(cfg, migrated); err != nil {
		return err
	}

	// 4. Old routes are now shadowing nothing we need; remove them exactly.
	for _, route := range legacy.routes {
		if _, err = runCommand("ip", legacyRouteArguments("del", spec, route)...); err != nil {
			return fmt.Errorf("删除旧版路由失败，保留未完成的迁移记录：%w", err)
		}
	}

	// 5. The result must pass the owned model's own checks.
	after, err = readReturnResources()
	if err != nil {
		return err
	}
	if err = checkReturnResources(spec, migrated, after); err != nil {
		return fmt.Errorf("迁移后检查未通过，保留未完成的迁移记录：%w", err)
	}
	if !ownedManifestComplete(owner, after) {
		return fmt.Errorf("迁移后资源不完整，保留未完成的迁移记录")
	}
	migrated.Active, owner.Pending = true, false
	migrated.MigratedAt = time.Now().Format(time.RFC3339)
	return saveReturnState(cfg, migrated)
}
