package routes

import (
	"bytes"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const runtimeEnvName = "CONFIGFLOW_TEST_OXIDNS_RUNTIME_PATH"

var runtimeEnvPlugins = []struct{ kind, field string }{
	{"dynamic_domain_set", "path"},
	{"query_recorder", "path"},
	{"cache", "dump_file"},
}

func appendRuntimeExpression(content, kind, field, expression string) string {
	return content + fmt.Sprintf("  - tag: runtime_expression\n    type: %s\n    args: {%s: '%s'}\n", kind, field, expression)
}

func unsetRuntimeTestEnvironment(t *testing.T) {
	t.Helper()
	// Setenv registers restoration of the original value before we unset it.
	t.Setenv(runtimeEnvName, "")
	if err := os.Unsetenv(runtimeEnvName); err != nil {
		t.Fatal(err)
	}
}

func TestOxiDNSMissingRuntimeEnvironmentFailsBeforeStopping(t *testing.T) {
	unsetRuntimeTestEnvironment(t)
	for _, plugin := range runtimeEnvPlugins {
		t.Run(plugin.kind, func(t *testing.T) {
			cfg, content := testOxiDeployment(t)
			root := filepath.Dir(cfg.ConfigPath)
			cfg.StopCommand = "printf 'stop\\n' >> lifecycle-actions; rm -f running"
			cfg.StartCommand = "printf 'start\\n' >> lifecycle-actions; touch running"
			original := []byte(content + "\n# existing live configuration\n")
			writeActivationFixture(t, cfg.ConfigPath, original, 0600)
			writeActivationFixture(t, filepath.Join(root, "running"), nil, 0600)
			// The fixture models the core rejecting an unresolved expression only
			// when that expression reaches it. Dropping it from the shadow would
			// incorrectly allow publication, reproducing the original regression.
			script := `#!/bin/sh
[ "$1" = check ] && [ "$2" = -c ] || exit 91
grep -q '.configflow-check-state/' "$3" || exit 92
if grep -Fq '${CONFIGFLOW_TEST_OXIDNS_RUNTIME_PATH}' "$3" && [ "${CONFIGFLOW_TEST_OXIDNS_RUNTIME_PATH+x}" != x ]; then exit 1; fi
exit 0
`
			writeActivationFixture(t, cfg.ServiceBinary, []byte(script), 0700)
			content = appendRuntimeExpression(content, plugin.kind, plugin.field, "${"+runtimeEnvName+"}")
			state := submitBundle(t, cfg, "missing-runtime-env", map[string][]byte{"config.yaml": []byte(content)}, true)
			if state.Status != "failed" || !strings.Contains(state.Error, "native configuration check failed") {
				t.Fatalf("missing runtime environment accepted: %+v", state)
			}
			if state.BackupComplete || len(state.Backups) != 0 || state.ConfigVersion != "" {
				t.Fatalf("failed core check advanced activation: %+v", state)
			}
			if actual, err := os.ReadFile(cfg.ConfigPath); err != nil || !bytes.Equal(actual, original) {
				t.Fatal("core check failure changed the live configuration")
			}
			if _, err := os.Stat(filepath.Join(root, "running")); err != nil {
				t.Fatal("core check failure stopped the existing service")
			}
			if _, err := os.Stat(filepath.Join(root, "lifecycle-actions")); !os.IsNotExist(err) {
				t.Fatalf("core check failure ran a lifecycle action: %v", err)
			}
		})
	}
}

func TestOxiDNSRuntimeEnvironmentPreservesOriginalConfiguration(t *testing.T) {
	for _, plugin := range runtimeEnvPlugins {
		t.Run(plugin.kind, func(t *testing.T) {
			cfg, content := testOxiDeployment(t)
			liveState := filepath.Join(t.TempDir(), "existing-state")
			writeActivationFixture(t, liveState, []byte("live state untouched"), 0600)
			t.Setenv(runtimeEnvName, liveState)
			// Existing extension metadata must also remain intact in the shadow.
			content = "__configflow_check_runtime_expressions: {original: true}\n" + content
			content = appendRuntimeExpression(content, plugin.kind, plugin.field, "${"+runtimeEnvName+"}")
			script := `#!/bin/sh
[ "$1" = check ] && [ "$2" = -c ] || exit 91
grep -q '.configflow-check-state/' "$3" || exit 92
grep -Fq '${CONFIGFLOW_TEST_OXIDNS_RUNTIME_PATH}' "$3" || exit 93
grep -q 'original: true' "$3" || exit 94
grep -q '^__configflow_check_runtime_expressions_:' "$3" || exit 95
exit 0
`
			writeActivationFixture(t, cfg.ServiceBinary, []byte(script), 0700)
			state := submitBundle(t, cfg, "valid-runtime-env", map[string][]byte{"config.yaml": []byte(content)}, true)
			if state.Status != "succeeded" {
				t.Fatalf("valid runtime environment rejected: %+v", state)
			}
			if actual, err := os.ReadFile(cfg.ConfigPath); err != nil || string(actual) != content {
				t.Fatal("shadow metadata or paths leaked into the installed configuration")
			}
			if actual, err := os.ReadFile(liveState); err != nil || string(actual) != "live state untouched" {
				t.Fatal("native validation modified live runtime state")
			}
		})
	}
}

// Run with OXIDNS_TEST_BINARY to verify the installed official core, including
// its default and escape semantics. No service is started by this regression.
func TestOxiDNSRuntimeEnvironmentWithOfficialCore(t *testing.T) {
	binary := os.Getenv("OXIDNS_TEST_BINARY")
	if binary == "" {
		t.Skip("set OXIDNS_TEST_BINARY to verify official environment expansion")
	}
	absolute, err := filepath.Abs(binary)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(absolute); err != nil {
		t.Fatal(err)
	}
	unsetRuntimeTestEnvironment(t)
	for _, plugin := range runtimeEnvPlugins {
		for _, expression := range []struct {
			name, value string
			valid       bool
		}{
			{"undefined", "${" + runtimeEnvName + "}", false},
			{"default", "${" + runtimeEnvName + ":-state/default.db}", true},
			{"escaped", "$${" + runtimeEnvName + "}", true},
			{"explicit-undefined", "${env:" + runtimeEnvName + "}", false},
		} {
			t.Run(plugin.kind+"/"+expression.name, func(t *testing.T) {
				cfg, content := testOxiDeployment(t)
				cfg.ServiceBinary = absolute
				content = strings.Replace(content, "    args: []\n", "    args: [{exec: accept}]\n", 1)
				content = appendRuntimeExpression(content, plugin.kind, plugin.field, expression.value)
				state := submitBundle(t, cfg, "official-runtime-env", map[string][]byte{"config.yaml": []byte(content)}, false)
				if expression.valid && state.Status != "ready" {
					t.Fatalf("official core rejected valid expression: %+v", state)
				}
				if !expression.valid && (state.Status != "failed" || !strings.Contains(state.Error, "native configuration check failed")) {
					t.Fatalf("official core accepted undefined environment: %+v", state)
				}
			})
		}
	}
}
