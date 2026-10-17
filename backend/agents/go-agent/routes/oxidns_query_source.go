package routes

import (
	"encoding/json"
	"strings"
)

type oxSourceStep struct {
	Sequence string `json:"sequence_tag"`
	Node     *int   `json:"node_index"`
	Kind     string `json:"kind"`
	Tag      string `json:"tag"`
	Outcome  string `json:"outcome"`
}

type oxSourceRecord struct {
	ID          int64           `json:"id"`
	Created     int64           `json:"created_at_ms"`
	RequestID   uint16          `json:"request_id"`
	Client      string          `json:"client_ip"`
	Questions   json.RawMessage `json:"questions_json"`
	HasResponse bool            `json:"has_response"`
	Error       *string         `json:"error"`
	Steps       []oxSourceStep  `json:"steps"`
}

func oxSourceQuick(tag, kind string) bool {
	return strings.HasPrefix(tag, "qs.") && strings.HasSuffix(tag, "."+kind)
}

// These are ConfigFlow's standard path labels, not the server selected inside a
// forward group. Keep the fallback classifier in oxidnsQuerySource.ts in sync.
func oxQuerySource(record oxSourceRecord) string {
	if record.Error != nil && *record.Error != "" {
		return "error"
	}
	if !record.HasResponse {
		return "no_response"
	}
	if len(record.Steps) == 0 {
		return "unrecorded"
	}
	source := "unknown"
	calls := []int{}
	for i, step := range record.Steps {
		if step.Kind == "builtin" && step.Tag == "reject" && step.Outcome == "stop" {
			source = "rejected"
		}
		if step.Kind != "executor" {
			continue
		}
		if step.Outcome == "entered" {
			calls = append(calls, i)
			continue
		}
		callIndex := len(calls) - 1
		for callIndex >= 0 {
			entered := record.Steps[calls[callIndex]]
			sameNode := entered.Node == nil && step.Node == nil || entered.Node != nil && step.Node != nil && *entered.Node == *step.Node
			if entered.Tag == step.Tag && entered.Sequence == step.Sequence && sameNode {
				break
			}
			callIndex--
		}
		if callIndex < 0 {
			continue
		}
		start := calls[callIndex]
		calls = append(calls[:callIndex], calls[callIndex+1:]...)
		if step.Outcome != "next" && step.Outcome != "stop" && step.Outcome != "return" {
			continue
		}
		switch step.Tag {
		case "forward_local":
			source = "local"
		case "forward_remote":
			source = "remote"
		case "forward_fallback":
			source = "fallback"
		case "adguard_block":
			source = "blocked"
		case "custom_hosts":
			if i+1 < len(record.Steps) {
				check := record.Steps[i+1]
				if check.Kind == "matcher" && oxSourceQuick(check.Tag, "has_resp") && check.Outcome == "matched" {
					source = "hosts"
				}
			}
		case "lazy_cache":
			// A miss wraps downstream execution; its completion is not a hit.
			if step.Outcome == "stop" && i == start+1 {
				source = "cache"
			}
		case "local_probe_cache":
			if step.Sequence == "local_probe" && step.Outcome == "return" && i == start+3 {
				check, end := record.Steps[start+1], record.Steps[start+2]
				if check.Kind == "matcher" && oxSourceQuick(check.Tag, "has_resp") && check.Outcome == "not_matched" && end.Kind == "builtin" && end.Tag == "return" && end.Outcome == "return" {
					source = "cache"
				}
			}
		default:
			if step.Tag == "drop_resp" || oxSourceQuick(step.Tag, "drop_resp") {
				source = "unknown"
			}
		}
	}
	return source
}
