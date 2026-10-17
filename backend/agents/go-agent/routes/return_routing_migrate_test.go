package routes

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// Fixtures mirror real `ip -j -N` / `nft -j` output from a host running the
// 1.8.2-1.8.4 return routing (routes/rule without protocol, table as string).
func legacyHost() *fakeHost {
	var host fakeHost
	_ = json.Unmarshal([]byte(`{}`), &host)
	host.routes = jsonMaps([]map[string]interface{}{
		{"dst": "default", "gateway": "192.168.1.1", "dev": "eth0", "flags": []interface{}{}},
		{"dst": "default", "gateway": "192.168.1.1", "dev": "eth0", "table": "104", "flags": []interface{}{}},
		{"dst": "192.168.1.0/24", "gateway": "192.168.1.1", "dev": "eth0", "table": "104", "flags": []interface{}{}},
	})
	host.rules = jsonMaps([]map[string]interface{}{
		{"priority": 0, "src": "all", "table": "255"},
		{"priority": 104, "src": "all", "fwmark": "0x400000", "fwmask": "0x400000", "table": "104"},
		{"priority": 32766, "src": "all", "table": "254"},
	})
	var nft struct {
		NFTables []map[string]interface{} `json:"nftables"`
	}
	_ = json.Unmarshal([]byte(`{"nftables":[
	 {"table":{"family":"inet","name":"configflow_return","handle":39}},
	 {"chain":{"family":"inet","table":"configflow_return","name":"prerouting","handle":1,"type":"filter","hook":"prerouting","prio":-150,"policy":"accept"}},
	 {"chain":{"family":"inet","table":"configflow_return","name":"output","handle":2,"type":"route","hook":"output","prio":-150,"policy":"accept"}},
	 {"set":{"family":"inet","name":"proxy_dst","table":"configflow_return","type":"ipv4_addr","handle":3,"flags":["interval"],"elem":[{"prefix":{"addr":"91.108.0.0","len":16}},{"prefix":{"addr":"198.18.0.0","len":16}}]}},
	 {"rule":{"family":"inet","table":"configflow_return","chain":"prerouting","handle":4,"comment":"configflow-return-mark","expr":[{"match":{"op":"==","left":{"meta":{"key":"iifname"}},"right":"eth0"}},{"match":{"op":"==","left":{"payload":{"protocol":"ether","field":"saddr"}},"right":"00:e0:b4:66:d5:f9"}},{"match":{"op":"==","left":{"payload":{"protocol":"ip","field":"daddr"}},"right":"@proxy_dst"}},{"match":{"op":"in","left":{"ct":{"key":"state"}},"right":"new"}},{"mangle":{"key":{"ct":{"key":"mark"}},"value":{"|":[{"ct":{"key":"mark"}},4194304]}}}]}},
	 {"rule":{"family":"inet","table":"configflow_return","chain":"prerouting","handle":5,"comment":"configflow-return-tun","expr":[{"match":{"op":"==","left":{"meta":{"key":"iifname"}},"right":"Meta"}},{"match":{"op":"==","left":{"&":[{"ct":{"key":"mark"}},4194304]},"right":4194304}},{"mangle":{"key":{"meta":{"key":"mark"}},"value":{"|":[{"meta":{"key":"mark"}},4194304]}}}]}},
	 {"rule":{"family":"inet","table":"configflow_return","chain":"output","handle":6,"comment":"configflow-return-local","expr":[{"match":{"op":"==","left":{"&":[{"ct":{"key":"mark"}},4194304]},"right":4194304}},{"mangle":{"key":{"meta":{"key":"mark"}},"value":{"|":[{"meta":{"key":"mark"}},4194304]}}}]}}
	]}`), &nft)
	host.nft = nft.NFTables
	host.nextHandle = 100
	return &host
}

// installLegacyFakeHost layers the three operations only a migration performs
// (exact delete of the old metric-0 routes, swapping the old protocol-less
// rule, deleting the verified old table) over the strict fake host.
func installLegacyFakeHost(t *testing.T, host *fakeHost) {
	t.Helper()
	installFakeHost(t, host)
	strictRun, strictInput := runCommand, runCommandInput
	runCommand = func(name string, args ...string) (string, error) {
		line := strings.Join(append([]string{name}, args...), " ")
		if name == "ip" && len(args) > 3 && args[1] == "route" && args[2] == "del" && argValue(args, "metric") == "0" {
			host.commands, host.mutations = append(host.commands, line), append(host.mutations, line)
			for i, row := range host.routes {
				if number(row["table"]) == argValue(args, "table") && row["dst"] == args[3] && row["metric"] == nil {
					host.routes = append(host.routes[:i], host.routes[i+1:]...)
					return "", nil
				}
			}
			return "", errors.New("No such process")
		}
		if name == "ip" && len(args) > 3 && args[1] == "rule" && argValue(args, "protocol") == "" {
			host.commands, host.mutations = append(host.commands, line), append(host.mutations, line)
			mark := strings.Split(argValue(args, "fwmark"), "/")
			legacy := map[string]interface{}{"priority": float64(argNumber(args, "priority")), "src": "all",
				"fwmark": mark[0], "fwmask": mark[1], "table": argValue(args, "lookup")}
			if args[2] == "add" {
				host.rules = append(host.rules, legacy)
				return "", nil
			}
			for i, row := range host.rules {
				// Without a protocol the kernel deletes the first match of any protocol.
				if number(row["priority"]) == argValue(args, "priority") && row["fwmark"] == mark[0] {
					host.rules = append(host.rules[:i], host.rules[i+1:]...)
					return "", nil
				}
			}
			return "", errors.New("No such file or directory")
		}
		return strictRun(name, args...)
	}
	runCommandInput = func(input, name string, args ...string) (string, error) {
		var document struct {
			NFTables []map[string]interface{} `json:"nftables"`
		}
		if json.Unmarshal([]byte(input), &document) == nil && len(document.NFTables) > 0 {
			if deletion, ok := document.NFTables[0]["delete"].(map[string]interface{}); ok {
				if _, isTable := deletion["table"]; isTable {
					if host.failNft {
						return strictInput(input, name, args...)
					}
					saved := jsonMaps(host.nft)
					kept := []map[string]interface{}{}
					for _, object := range host.nft {
						if !nftObjectIsManaged(object) {
							kept = append(kept, object)
						}
					}
					host.nft = kept
					rest, _ := json.Marshal(map[string]interface{}{"nftables": document.NFTables[1:]})
					if _, err := strictInput(string(rest), name, args...); err != nil {
						host.nft = saved // atomic: the whole transaction fails
						return "", err
					}
					host.inputs[len(host.inputs)-1] = input
					return "", nil
				}
			}
		}
		return strictInput(input, name, args...)
	}
}

func writeLegacyState(t *testing.T, cfg *Config) []byte {
	t.Helper()
	data := []byte(`{"spec":{"router_ip":"192.168.1.1","proxy_dst":["198.18.0.0/16","91.108.0.0/16"],"mark":"0x400000","table":104,"priority":104},` +
		`"legacy":{"tables":["inet mihomo_return"],"dump":"/opt/configflow-agent/return-routing-legacy.nft","scripts":["/etc/local.d/mihomo-return.start"]},` +
		`"applied_at":"2026-10-17T10:52:23+08:00"}`)
	if err := os.WriteFile(filepath.Join(filepath.Dir(cfg.AgentConfigFile), "return-routing.json"), data, 0600); err != nil {
		t.Fatal(err)
	}
	return data
}

func indexOf(items []string, prefix string) int {
	for i, item := range items {
		if strings.HasPrefix(item, prefix) {
			return i
		}
	}
	return -1
}

func TestLegacyReturnStateMigratesToOwnedResources(t *testing.T) {
	host := legacyHost()
	installLegacyFakeHost(t, host)
	cfg := testConfig(t, tunConfig)
	writeLegacyState(t, cfg)

	reapplyReturnRouting(cfg)

	state, err := loadReturnState(cfg)
	if err != nil || state == nil {
		t.Fatalf("state = %v, %v", state, err)
	}
	if !validReturnOwner(state) || !state.Active || state.Ownership.Pending || state.Legacy != nil {
		t.Fatalf("migrated state must be owned, active and complete: %+v", state)
	}
	if state.MigratedAt == "" || state.MigratedLegacy == nil || state.MigratedLegacy.Tables[0] != "inet mihomo_return" {
		t.Fatalf("migration must keep an audit record of the old backup: %+v", state)
	}
	if state.Spec.SafetyVersion != 1 {
		t.Fatalf("migrated spec must use safety protocol 1: %+v", state.Spec)
	}
	if status := managedReturnStatus(cfg); status["table_present"] != true {
		t.Fatalf("migrated resources must read back complete: %v", status)
	}
	for _, route := range host.routes {
		if number(route["table"]) == "104" && route["metric"] == nil {
			t.Fatalf("old route left behind: %v", route)
		}
	}
	for _, object := range host.nft {
		if set, ok := object["set"]; ok {
			t.Fatalf("old named set left behind: %v", set)
		}
	}

	m := host.mutations
	routeAdd := indexOf(m, "ip -4 route add default")
	ruleDel := indexOf(m, "ip -4 rule del priority 104 from all fwmark 0x400000/0x400000 lookup 104")
	ruleAdd := indexOf(m, "ip -4 rule add priority 104")
	nftSwap := indexOf(m, "nft -j -f -")
	routeDel := indexOf(m, "ip -4 route del default")
	if routeAdd < 0 || ruleDel < routeAdd || ruleAdd != ruleDel+1 || nftSwap < ruleAdd || routeDel < nftSwap {
		t.Fatalf("migration order must keep marking and routing in place: %v", m)
	}
	if !strings.Contains(host.inputs[0], `"delete"`) || !strings.Contains(host.inputs[0], `"create"`) {
		t.Fatal("old table must be replaced in one nft transaction")
	}

	// Owned resources can now be removed precisely, proving ownership.
	if _, err := RemoveReturnRouting(cfg, false); err != nil {
		t.Fatalf("migrated resources must be removable: %v", err)
	}
}

func TestLegacyMigrationRefusedWhenHostDiffers(t *testing.T) {
	cases := map[string]func(*fakeHost){
		"extra_route": func(h *fakeHost) {
			h.routes = append(h.routes, map[string]interface{}{"dst": "203.0.113.0/24", "gateway": "192.168.1.9", "dev": "eth0", "table": "104", "flags": []interface{}{}})
		},
		"other_gateway": func(h *fakeHost) { h.routes[1]["gateway"] = "192.168.1.9" },
		"set_differs": func(h *fakeHost) {
			h.nft[3]["set"].(map[string]interface{})["elem"] = []interface{}{map[string]interface{}{"prefix": map[string]interface{}{"addr": "198.18.0.0", "len": 16}}}
		},
		"extra_rule": func(h *fakeHost) {
			h.nft = append(h.nft, map[string]interface{}{"rule": map[string]interface{}{"family": "inet", "table": "configflow_return", "chain": "output", "handle": 7, "comment": "someone-else", "expr": []interface{}{}}})
		},
		"rule_mark": func(h *fakeHost) { h.rules[1]["fwmark"], h.rules[1]["fwmask"] = "0x4", "0x4" },
		"tagged_table": func(h *fakeHost) {
			h.nft[0]["table"].(map[string]interface{})["comment"] = "configflow-return:abc"
		},
		"missing_rule": func(h *fakeHost) { h.rules = append(h.rules[:1], h.rules[2:]...) },
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			host := legacyHost()
			mutate(host)
			installLegacyFakeHost(t, host)
			cfg := testConfig(t, tunConfig)
			original := writeLegacyState(t, cfg)
			before := jsonText([]interface{}{host.routes, host.rules, host.nft})

			reapplyReturnRouting(cfg)

			assertNoWrites(t, host)
			if before != jsonText([]interface{}{host.routes, host.rules, host.nft}) {
				t.Fatal("refused migration changed host resources")
			}
			if data, _ := os.ReadFile(filepath.Join(filepath.Dir(cfg.AgentConfigFile), "return-routing.json")); string(data) != string(original) {
				t.Fatal("refused migration changed the state file")
			}
		})
	}
}

func TestLegacyMigrationRollsBackWhenNFTSwapFails(t *testing.T) {
	host := legacyHost()
	host.failNft = true
	installLegacyFakeHost(t, host)
	cfg := testConfig(t, tunConfig)
	original := writeLegacyState(t, cfg)
	beforeRoutes, beforeNFT := jsonText(host.routes), jsonText(host.nft)

	reapplyReturnRouting(cfg)

	if jsonText(host.routes) != beforeRoutes || jsonText(host.nft) != beforeNFT {
		t.Fatalf("failed migration must remove its routes and keep the old table:\nroutes %v\nnft %v", host.routes, host.nft)
	}
	legacyRules := 0
	for _, rule := range host.rules {
		if number(rule["priority"]) == "104" {
			if rule["protocol"] != nil {
				t.Fatalf("owned rule left behind: %v", rule)
			}
			legacyRules++
		}
	}
	if legacyRules != 1 {
		t.Fatalf("old rule must be restored exactly once, got %d", legacyRules)
	}
	if data, _ := os.ReadFile(filepath.Join(filepath.Dir(cfg.AgentConfigFile), "return-routing.json")); string(data) != string(original) {
		t.Fatal("failed migration must restore the original state file")
	}
}
