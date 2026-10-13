package upgrade

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestBootstrapWithoutInstalledCore(t *testing.T) {
	for _, manager := range []string{"systemd", "openrc"} {
		for _, kind := range []string{"mihomo", "mosdns"} {
			for _, binaryCase := range []string{"omitted", "bare-name", "missing-path", "on-path"} {
				t.Run(manager+"/"+kind+"/"+binaryCase, func(t *testing.T) {
					c, root := testConfig(t, manager)
					binary := filepath.Join(root, "agent")
					c.Values["service_type"] = kind
					wantCore := kind
					switch binaryCase {
					case "omitted":
						delete(c.Values, "service_binary")
					case "missing-path":
						wantCore = filepath.Join(root, "not-installed", kind)
						c.Values["service_binary"] = wantCore
					default:
						c.Values["service_binary"] = wantCore
					}
					data, err := json.Marshal(c.Values)
					if err != nil {
						t.Fatal(err)
					}
					if err := os.WriteFile(c.Path, data, 0600); err != nil {
						t.Fatal(err)
					}

					commandsDir := filepath.Join(root, "commands")
					if err := os.Mkdir(commandsDir, 0755); err != nil {
						t.Fatal(err)
					}
					logPath := filepath.Join(root, "manager.log")
					for _, name := range []string{"systemctl", "rc-update", "rc-service"} {
						writeBootstrapCommand(t, filepath.Join(commandsDir, name))
					}
					coreOnPath := filepath.Join(commandsDir, kind)
					coreScript := []byte("#!/bin/sh\nexit 42\n")
					if binaryCase == "on-path" {
						// Lookup is allowed to succeed, but the configured bare
						// name must survive instead of being replaced by this path.
						if err := os.WriteFile(coreOnPath, coreScript, 0755); err != nil {
							t.Fatal(err)
						}
					}
					t.Setenv("PATH", commandsDir)
					t.Setenv("CONFIGFLOW_TEST_MANAGER_LOG", logPath)

					ready, err := migrationReady(c.Path, binary, root)
					if err != nil || ready {
						t.Fatalf("unmigrated installation was ready: ready=%v err=%v", ready, err)
					}
					if err := ensureInstalled(c.Path, binary, root); err != nil {
						t.Fatalf("startup migration failed for %s core: %v", binaryCase, err)
					}
					migrated, err := ReadConfig(c.Path)
					if err != nil {
						t.Fatal(err)
					}
					if migrated.Values["upgrade_schema"] != float64(1) {
						t.Fatalf("schema was not persisted: %v", migrated.Values["upgrade_schema"])
					}
					if migrated.String("service_binary") != wantCore {
						t.Fatalf("core name/path changed: got %q, want %q", migrated.String("service_binary"), wantCore)
					}
					if migrated.String("service_manager") != manager || migrated.String("service_unit") != "custom-core" {
						t.Fatalf("inferred lifecycle was not persisted: %v", migrated.Values)
					}
					if migrated.String("agent_id") != "retained-id" || migrated.String("token") != "retained-token" {
						t.Fatal("startup migration lost Agent identity")
					}
					if binaryCase == "on-path" {
						coreData, err := os.ReadFile(coreOnPath)
						if err != nil || !bytes.Equal(coreData, coreScript) {
							t.Fatalf("migration modified PATH core: data=%q err=%v", coreData, err)
						}
					} else if _, err := os.Stat(coreOnPath); !os.IsNotExist(err) {
						t.Fatalf("migration created a core binary: %v", err)
					}
					if binaryCase == "missing-path" {
						if _, err := os.Stat(wantCore); !os.IsNotExist(err) {
							t.Fatalf("migration created the missing configured core: %v", err)
						}
					}
					if _, err := os.Stat(c.String("config_path")); !os.IsNotExist(err) {
						t.Fatalf("migration created a core configuration: %v", err)
					}

					gate := "configflow-recover-" + kind
					gatePath := filepath.Join(root, "etc/init.d", gate)
					dependencyPath := filepath.Join(root, "etc/conf.d/custom-core")
					wantMode := os.FileMode(0755)
					wantCommands := "rc-update -u\nrc-service " + gate + " start\n"
					if manager == "systemd" {
						gatePath = filepath.Join(root, "etc/systemd/system", gate+".service")
						dependencyPath = filepath.Join(root, "etc/systemd/system/custom-core.service.d/configflow-recovery.conf")
						wantMode = 0644
						wantCommands = "systemctl daemon-reload\nsystemctl start " + gate + "\n"
					}
					gateData, err := os.ReadFile(gatePath)
					if err != nil {
						t.Fatal(err)
					}
					if !strings.Contains(string(gateData), binary) || !strings.Contains(string(gateData), c.Path) || !strings.Contains(string(gateData), "-recover-only") {
						t.Fatalf("recovery gate does not launch Agent recovery: %s", gateData)
					}
					gateInfo, err := os.Stat(gatePath)
					if err != nil || gateInfo.Mode().Perm() != wantMode {
						t.Fatalf("unexpected recovery gate permissions: info=%v err=%v", gateInfo, err)
					}
					dependencyData, err := os.ReadFile(dependencyPath)
					if err != nil {
						t.Fatal(err)
					}
					if !strings.Contains(string(dependencyData), gate) {
						t.Fatalf("core startup has no recovery dependency: %s", dependencyData)
					}
					commandLog, err := os.ReadFile(logPath)
					if err != nil || string(commandLog) != wantCommands {
						t.Fatalf("unexpected service manager calls: got %q, want %q; err=%v", commandLog, wantCommands, err)
					}
					ready, err = migrationReady(c.Path, binary, root)
					if err != nil || !ready {
						t.Fatalf("installed recovery files were not ready: ready=%v err=%v", ready, err)
					}
					journalData, err := os.ReadFile(filepath.Join(root, ".agent-migration.json"))
					if err != nil {
						t.Fatal(err)
					}
					var journal bootstrapJournal
					if err := json.Unmarshal(journalData, &journal); err != nil || !journal.Complete {
						t.Fatalf("migration journal was not completed: journal=%+v err=%v", journal, err)
					}

					if err := ensureInstalled(c.Path, binary, root); err != nil {
						t.Fatalf("repeat startup failed: %v", err)
					}
					secondLog, err := os.ReadFile(logPath)
					if err != nil || !bytes.Equal(secondLog, commandLog) {
						t.Fatalf("repeat startup reran the service manager: %q; err=%v", secondLog, err)
					}

					if err := os.Remove(gatePath); err != nil {
						t.Fatal(err)
					}
					ready, err = migrationReady(c.Path, binary, root)
					if err != nil || ready {
						t.Fatalf("missing recovery gate was ready: ready=%v err=%v", ready, err)
					}
				})
			}
		}
	}
}

func writeBootstrapCommand(t *testing.T, path string) {
	t.Helper()
	// Shell builtins keep PATH fully isolated from host service managers and
	// host core binaries, while recording the exact manager arguments.
	script := "#!/bin/sh\n" +
		"printf '%s' \"${0##*/}\" >> \"$CONFIGFLOW_TEST_MANAGER_LOG\"\n" +
		"for arg in \"$@\"; do printf ' %s' \"$arg\" >> \"$CONFIGFLOW_TEST_MANAGER_LOG\"; done\n" +
		"printf '\\n' >> \"$CONFIGFLOW_TEST_MANAGER_LOG\"\n"
	if err := os.WriteFile(path, []byte(script), 0755); err != nil {
		t.Fatal(err)
	}
}
