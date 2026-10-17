package routes

import "testing"

func TestOxSourceClassification(t *testing.T) {
	step := func(tag, outcome, kind string) oxSourceStep {
		return oxSourceStep{Sequence: "main", Tag: tag, Outcome: outcome, Kind: kind}
	}
	run := func(tag string) []oxSourceStep {
		return []oxSourceStep{step(tag, "entered", "executor"), step(tag, "next", "executor")}
	}
	cases := []struct {
		name, want string
		steps      []oxSourceStep
	}{
		{"local", "local", run("forward_local")},
		{"remote", "remote", run("forward_remote")},
		{"fallback", "fallback", run("forward_fallback")},
		{"probe_then_remote", "remote", append(run("forward_local"), run("forward_remote")...)},
		{"cache_hit", "cache", []oxSourceStep{step("lazy_cache", "entered", "executor"), step("lazy_cache", "stop", "executor")}},
		{"cache_miss", "local", append(append([]oxSourceStep{step("lazy_cache", "entered", "executor")}, run("forward_local")...), step("lazy_cache", "stop", "executor"))},
		{"failed_forward", "unknown", []oxSourceStep{step("forward_remote", "entered", "executor"), step("forward_remote", "error", "executor")}},
		{"unmatched_completion", "unknown", []oxSourceStep{step("forward_remote", "next", "executor")}},
		{"discard", "unknown", append(run("forward_local"), run("qs.exec.main.2.drop_resp")...)},
		{"reject", "rejected", append(run("forward_local"), step("reject", "stop", "builtin"))},
		{"hosts", "hosts", append(run("custom_hosts"), step("qs.match.main.1.0.has_resp", "matched", "matcher"))},
		{"block", "blocked", run("adguard_block")},
		{"custom", "unknown", run("forward_custom")},
		{"empty", "unrecorded", nil},
	}
	probe := []oxSourceStep{step("local_probe_cache", "entered", "executor"), step("qs.match.local_probe.1.0.has_resp", "not_matched", "matcher"), step("return", "return", "builtin"), step("local_probe_cache", "return", "executor")}
	for i := range probe {
		probe[i].Sequence = "local_probe"
	}
	cases = append(cases, struct {
		name, want string
		steps      []oxSourceStep
	}{"probe_cache", "cache", probe})
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := oxQuerySource(oxSourceRecord{HasResponse: true, Steps: tc.steps}); got != tc.want {
				t.Fatalf("got %s want %s", got, tc.want)
			}
		})
	}
}
