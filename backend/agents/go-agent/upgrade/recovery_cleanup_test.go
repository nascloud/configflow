package upgrade

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writeRecoveryFixture(t *testing.T, path, data string, mode os.FileMode) {
	t.Helper()
	if err := AtomicWrite(path, []byte(data), mode); err != nil {
		t.Fatal(err)
	}
}

func TestRecoveryDependencyCleanupAndRollback(t *testing.T) {
	for _, manager := range []string{"systemd", "openrc"} {
		for _, kind := range []string{"mihomo", "mosdns", "oxidns"} {
			t.Run(manager+"/"+kind, func(t *testing.T) {
				c, root := testConfig(t, manager)
				c.Values["service_type"] = kind
				c.Values["agent_service_unit"] = "custom-agent.service"
				gate := "configflow-recover-" + kind
				var removed, edited []string
				if manager == "systemd" {
					dir := filepath.Join(root, "etc/systemd/system")
					removed = []string{filepath.Join(dir, gate+".service"), filepath.Join(dir, "custom-core.service.d/configflow-recovery.conf")}
					writeRecoveryFixture(t, removed[0], "legacy recovery content", 0644)
					writeRecoveryFixture(t, removed[1], "[Unit]\nRequires="+gate+".service\nAfter="+gate+".service\n", 0644)
					for _, unit := range []string{"custom-agent", "custom-core"} {
						mainPath := filepath.Join(dir, unit+".service")
						dropIn := filepath.Join(dir, unit+".service.d/custom.conf")
						edited = append(edited, mainPath, dropIn)
						writeRecoveryFixture(t, mainPath, "[Unit]\nDescription=My custom service\nRequires=network-online.target "+gate+".service local-fs.target\nAfter=network.target \\\n "+gate+".service custom.service\n[Service]\nEnvironment=KEEP=yes\n", 0640)
						writeRecoveryFixture(t, dropIn, "[Unit]\nRequires="+gate+".service\nAfter=other.service "+gate+".service\n[Service]\nEnvironment=EXTRA=yes\n", 0644)
					}
				} else {
					removed = []string{filepath.Join(root, "etc/init.d", gate)}
					writeRecoveryFixture(t, removed[0], "legacy recovery content", 0755)
					edited = []string{filepath.Join(root, "etc/init.d/custom-agent"), filepath.Join(root, "etc/conf.d/custom-core")}
					writeRecoveryFixture(t, edited[0], "#!/sbin/openrc-run\ncommand_args='keep me'\ndepend() {\n    need net "+gate+" localmount # custom dependencies\n    after firewall\n}\n", 0755)
					writeRecoveryFixture(t, edited[1], "rc_need=custom-dependency\n# BEGIN CONFIGFLOW RECOVERY "+kind+"\nrc_need=\"${rc_need:-} "+gate+"\"\n# END CONFIGFLOW RECOVERY "+kind+"\nCUSTOM=value\n", 0640)
					for _, level := range []string{"boot", "default"} {
						link := filepath.Join(root, "etc/runlevels", level, gate)
						if err := os.MkdirAll(filepath.Dir(link), 0755); err != nil {
							t.Fatal(err)
						}
						if err := os.Symlink("/etc/init.d/"+gate, link); err != nil {
							t.Fatal(err)
						}
						removed = append(removed, link)
					}
				}
				plan, err := MigrationPlan(c, filepath.Join(root, "agent"), root)
				if err != nil {
					t.Fatal(err)
				}
				var snapshots []Snapshot
				for _, change := range plan {
					snapshot, err := captureChange(change)
					if err != nil {
						t.Fatal(err)
					}
					snapshots = append(snapshots, snapshot)
				}
				if err := ApplyChanges(plan); err != nil {
					t.Fatal(err)
				}
				for _, path := range removed {
					if _, err := os.Lstat(path); !os.IsNotExist(err) {
						t.Fatalf("legacy gate survived: %s: %v", path, err)
					}
				}
				for _, path := range edited {
					data, err := os.ReadFile(path)
					if err != nil || strings.Contains(string(data), gate) {
						t.Fatalf("dependency survived in %s: %q, %v", path, data, err)
					}
				}
				if ready, err := migrationReady(c.Path, filepath.Join(root, "agent"), root); err != nil || !ready {
					t.Fatalf("cleaned migration not ready: %v %v", ready, err)
				}
				second, err := MigrationPlan(c, filepath.Join(root, "agent"), root)
				if err != nil {
					t.Fatal(err)
				}
				if err := ApplyChanges(second); err != nil {
					t.Fatal(err)
				}
				// Old regular-file snapshots and new removed runlevel links both
				// survive serialization into a worker's durable journal.
				saved, err := json.Marshal(snapshots)
				if err != nil {
					t.Fatal(err)
				}
				if err := json.Unmarshal(saved, &snapshots); err != nil {
					t.Fatal(err)
				}
				if err := Restore(snapshots); err != nil {
					t.Fatal(err)
				}
				for _, snapshot := range snapshots {
					actual, err := capture(snapshot.Path, snapshot.LinkTarget != "")
					if err != nil || actual.Existed != snapshot.Existed || actual.Mode != snapshot.Mode || actual.LinkTarget != snapshot.LinkTarget || !bytes.Equal(actual.Data, snapshot.Data) {
						t.Fatalf("rollback changed %s: got %+v, want %+v, err=%v", snapshot.Path, actual, snapshot, err)
					}
				}
			})
		}
	}
}

func TestRecoveryDependencyEditingPreservesUnrelatedSettings(t *testing.T) {
	systemd := "# header\n[Unit]\nRequires=unrelated.service configflow-recover-mosdns.service\nAfter=configflow-recover-mihomo.service net.service configflow-recover-oxidns.service\nWants=keep.service\n[Service]\nEnvironment=Requires=configflow-recover-mosdns.service\n"
	wantSystemd := "# header\n[Unit]\nRequires=unrelated.service\nAfter=net.service\nWants=keep.service\n[Service]\nEnvironment=Requires=configflow-recover-mosdns.service\n"
	got, err := stripSystemdRecoveryDependencies([]byte(systemd))
	if err != nil || string(got) != wantSystemd {
		t.Fatalf("systemd cleanup: %q, %v", got, err)
	}
	openrc := "depend() {\n  need net configflow-recover-mosdns localmount # keep\n  need configflow-recover-mihomo configflow-recover-oxidns\n  after firewall\n}\ncommand_args='unchanged'\n"
	wantOpenRC := "depend() {\n  need net localmount # keep\n  after firewall\n}\ncommand_args='unchanged'\n"
	got, err = stripOpenRCRecoveryDependencies([]byte(openrc))
	if err != nil || string(got) != wantOpenRC {
		t.Fatalf("OpenRC cleanup: %q, %v", got, err)
	}
	section := "rc_need=before\n# BEGIN CONFIGFLOW RECOVERY mosdns\nrc_need=\"${rc_need:-} custom-dependency configflow-recover-mosdns\"\nCUSTOM=keep\n# custom comment\n# END CONFIGFLOW RECOVERY mosdns\n"
	wantSection := "rc_need=before\nrc_need=\"${rc_need:-} custom-dependency \"\nCUSTOM=keep\n# custom comment\n"
	got, err = stripOpenRCRecoverySection([]byte(section), "mosdns")
	if err != nil || string(got) != wantSection {
		t.Fatalf("OpenRC section cleanup discarded user settings: %q %v", got, err)
	}
	if _, err := stripOpenRCRecoverySection([]byte("# BEGIN CONFIGFLOW RECOVERY mosdns\nrc_need=custom"), "mosdns"); err == nil {
		t.Fatal("accepted unterminated OpenRC recovery marker")
	}
	c, root := testConfig(t, "systemd")
	path := filepath.Join(root, "etc/systemd/system/custom-core.service.d/configflow-recovery.conf")
	writeRecoveryFixture(t, path, "[Unit]\nRequires=configflow-recover-mihomo.service\nAfter=network.target\n[Service]\nEnvironment=KEEP=yes\n", 0640)
	plan, err := MigrationPlan(c, filepath.Join(root, "agent"), root)
	if err != nil {
		t.Fatal(err)
	}
	if err := ApplyChanges(plan); err != nil {
		t.Fatal(err)
	}
	if data, err := os.ReadFile(path); err != nil || string(data) != "[Unit]\nAfter=network.target\n[Service]\nEnvironment=KEEP=yes\n" {
		t.Fatalf("removed custom recovery drop-in settings: %q %v", data, err)
	}
}

func TestDeletePlanRejectsUnownedSymlinks(t *testing.T) {
	root := t.TempDir()
	target := filepath.Join(root, "target")
	writeRecoveryFixture(t, target, "preserve", 0640)
	link := filepath.Join(root, "link")
	if err := os.Symlink(target, link); err != nil {
		t.Fatal(err)
	}
	if err := ApplyChanges([]FileChange{{Path: link, Delete: true}}); err == nil {
		t.Fatal("ordinary deletion accepted a symlink")
	}
	if actual, err := os.Readlink(link); err != nil || actual != target {
		t.Fatalf("unowned symlink was removed: %q %v", actual, err)
	}
	if data, err := os.ReadFile(target); err != nil || string(data) != "preserve" {
		t.Fatalf("symlink cleanup modified target: %q %v", data, err)
	}
}

func TestLegacyWorkerDefersCleanupUntilCommit(t *testing.T) {
	for _, manager := range []string{"systemd", "openrc"} {
		for _, kind := range []string{"mihomo", "mosdns", "oxidns"} {
			t.Run(manager+"/"+kind, func(t *testing.T) {
				c, root := testConfig(t, manager)
				c.Values["service_type"] = kind
				gate := "configflow-recover-" + kind
				binary := filepath.Join(root, "agent")
				c.Values["upgrade_schema"] = 1
				configData, _ := json.Marshal(c.Values)
				writeRecoveryFixture(t, c.Path, string(configData), 0600)
				gatePath := filepath.Join(root, "etc/init.d", gate)
				if manager == "systemd" {
					gatePath = filepath.Join(root, "etc/systemd/system", gate+".service")
				}
				writeRecoveryFixture(t, gatePath, "legacy gate", 0644)
				commandsDir := filepath.Join(root, "commands")
				if err := os.Mkdir(commandsDir, 0755); err != nil {
					t.Fatal(err)
				}
				for _, name := range []string{"systemctl", "rc-update", "rc-service"} {
					writeBootstrapCommand(t, filepath.Join(commandsDir, name))
				}
				logPath := filepath.Join(root, "manager.log")
				t.Setenv("PATH", commandsDir)
				t.Setenv("CONFIGFLOW_TEST_MANAGER_LOG", logPath)
				job := Job{Status: Status{ID: "1234567890123456", AgentID: c.String("agent_id"), Version: Version}, Binary: binary, Mutating: true}
				if err := job.save(CurrentPath(binary), "checking"); err != nil {
					t.Fatal(err)
				}
				if ready, err := migrationReady(c.Path, binary, root); err != nil || !ready {
					t.Fatalf("old worker cannot finish its ready probe: %v %v", ready, err)
				}
				if ready, err := migrationReadyStrict(c.Path, binary, root); err != nil || ready {
					t.Fatalf("legacy gate unexpectedly satisfied strict readiness: %v %v", ready, err)
				}
				if done, err := finishDeferredMigration(c.Path, binary, root); err != nil || done {
					t.Fatalf("cleanup ran before commit: %v %v", done, err)
				}
				if _, err := os.Stat(gatePath); err != nil {
					t.Fatal("old worker still needs its original gate snapshot")
				}
				release, err := lock(filepath.Join(rootFor(binary), "update.lock"))
				if err != nil {
					t.Fatal(err)
				}
				if err := job.save(CurrentPath(binary), "succeeded"); err != nil {
					t.Fatal(err)
				}
				if ready, err := migrationReady(c.Path, binary, root); err != nil || ready {
					t.Fatalf("completed worker bypassed strict readiness: %v %v", ready, err)
				}
				if done, err := finishDeferredMigration(c.Path, binary, root); err != nil || done {
					t.Fatalf("cleanup raced worker's held lock: %v %v", done, err)
				}
				release()
				if done, err := finishDeferredMigration(c.Path, binary, root); err != nil || !done {
					t.Fatalf("committed worker was not cleaned: %v %v", done, err)
				}
				if _, err := os.Lstat(gatePath); !os.IsNotExist(err) {
					t.Fatalf("gate survived worker commit: %v", err)
				}
				if ready, err := migrationReady(c.Path, binary, root); err != nil || !ready {
					t.Fatalf("post-commit migration not ready: %v %v", ready, err)
				}
				want := "systemctl daemon-reload\n"
				if manager == "openrc" {
					want = "rc-update -u\n"
				}
				if data, err := os.ReadFile(logPath); err != nil || string(data) != want {
					t.Fatalf("cleanup started/stopped a service: %q %v", data, err)
				}
			})
		}
	}
}

func TestLegacyWorkerRollbackDoesNotCleanRecovery(t *testing.T) {
	for _, manager := range []string{"systemd", "openrc"} {
		for _, kind := range []string{"mihomo", "mosdns", "oxidns"} {
			t.Run(manager+"/"+kind, func(t *testing.T) {
				c, root := testConfig(t, manager)
				c.Values["service_type"] = kind
				binary := filepath.Join(root, "agent")
				gate := filepath.Join(root, "etc/init.d", "configflow-recover-"+kind)
				if manager == "systemd" {
					gate = filepath.Join(root, "etc/systemd/system", "configflow-recover-"+kind+".service")
				}
				writeRecoveryFixture(t, gate, "legacy gate", 0644)
				job := Job{Status: Status{ID: "1234567890123456", AgentID: c.String("agent_id"), Version: Version}, Binary: binary, Mutating: true}
				for _, stage := range []string{"failed", "rolled_back", "rollback_failed"} {
					if err := job.save(CurrentPath(binary), stage); err != nil {
						t.Fatal(err)
					}
					if done, err := finishDeferredMigration(c.Path, binary, root); err != nil || !done {
						t.Fatalf("rollback stage %s not respected: %v %v", stage, done, err)
					}
					if data, err := os.ReadFile(gate); err != nil || string(data) != "legacy gate" {
						t.Fatalf("rollback stage %s lost original files: %q %v", stage, data, err)
					}
				}

			})
		}
	}
}

func TestMigrationKeepsUnrelatedLinkedUnits(t *testing.T) {
	c, root := testConfig(t, "systemd")
	vendor := filepath.Join(root, "usr/lib/systemd/system/custom-core.service")
	original := "[Unit]\nAfter=network.target\n[Service]\nExecStart=/usr/bin/custom-core\n"
	writeRecoveryFixture(t, vendor, original, 0644)
	link := filepath.Join(root, "etc/systemd/system/custom-core.service")
	if err := os.MkdirAll(filepath.Dir(link), 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(vendor, link); err != nil {
		t.Fatal(err)
	}
	plan, err := MigrationPlan(c, filepath.Join(root, "agent"), root)
	if err != nil {
		t.Fatal(err)
	}
	if err := ApplyChanges(plan); err != nil {
		t.Fatal(err)
	}
	if target, err := os.Readlink(link); err != nil || target != vendor {
		t.Fatalf("migration replaced vendor unit symlink: %q %v", target, err)
	}
	if data, err := os.ReadFile(vendor); err != nil || string(data) != original {
		t.Fatalf("migration changed vendor unit: %q %v", data, err)
	}
	writeRecoveryFixture(t, vendor, "[Unit]\nRequires=configflow-recover-mihomo.service\n", 0644)
	if _, err := MigrationPlan(c, filepath.Join(root, "agent"), root); err == nil || !strings.Contains(err.Error(), "linked unit") {
		t.Fatalf("unsafe linked dependency mutation was not bounded: %v", err)
	}
}

func TestMigrationRemovesGateAfterDependencies(t *testing.T) {
	for _, manager := range []string{"systemd", "openrc"} {
		for _, kind := range []string{"mihomo", "mosdns", "oxidns"} {
			c, root := testConfig(t, manager)
			c.Values["service_type"] = kind
			gate := "configflow-recover-" + kind
			gatePath := filepath.Join(root, "etc/init.d", gate)
			if manager == "systemd" {
				gatePath = filepath.Join(root, "etc/systemd/system", gate+".service")
				writeRecoveryFixture(t, filepath.Join(root, "etc/systemd/system/custom-core.service.d/configflow-recovery.conf"), "[Unit]\nRequires="+gate+".service\n", 0644)
			} else {
				writeRecoveryFixture(t, filepath.Join(root, "etc/conf.d/custom-core"), "# BEGIN CONFIGFLOW RECOVERY "+kind+"\nrc_need="+gate+"\n# END CONFIGFLOW RECOVERY "+kind+"\n", 0644)
			}
			writeRecoveryFixture(t, gatePath, "legacy gate", 0644)
			plan, err := MigrationPlan(c, filepath.Join(root, "agent"), root)
			if err != nil {
				t.Fatal(err)
			}
			if plan[len(plan)-2].Path != gatePath || !plan[len(plan)-2].Delete || plan[len(plan)-1].Path != c.Path {
				t.Fatalf("gate removal did not follow dependency cleanup: %+v", plan)
			}
		}
	}
}

func TestRecoveryCleanupPreservesPerCoreCustomSettings(t *testing.T) {
	for _, manager := range []string{"systemd", "openrc"} {
		for _, kind := range []string{"mihomo", "mosdns", "oxidns"} {
			t.Run(manager+"/"+kind, func(t *testing.T) {
				c, root := testConfig(t, manager)
				c.Values["service_type"] = kind
				c.Values["service_manager"] = manager
				c.Values["service_unit"] = kind
				gate := "configflow-recover-" + kind
				var path, original, want string
				if manager == "systemd" {
					path = filepath.Join(root, "etc/systemd/system", kind+".service.d/configflow-recovery.conf")
					original = "[Unit]\nRequires=" + gate + ".service local-fs.target\nAfter=" + gate + ".service network.target\n[Service]\nEnvironment=KEEP=yes\n"
					want = "[Unit]\nRequires=local-fs.target\nAfter=network.target\n[Service]\nEnvironment=KEEP=yes\n"
				} else {
					path = filepath.Join(root, "etc/conf.d", kind)
					original = "rc_need=before\n# BEGIN CONFIGFLOW RECOVERY " + kind + "\nrc_need=\"${rc_need:-} custom-dependency " + gate + "\"\nCUSTOM=keep\n# custom comment\n# END CONFIGFLOW RECOVERY " + kind + "\n"
					want = "rc_need=before\nrc_need=\"${rc_need:-} custom-dependency \"\nCUSTOM=keep\n# custom comment\n"
				}
				writeRecoveryFixture(t, path, original, 0640)
				for attempt := 0; attempt < 2; attempt++ {
					plan, err := MigrationPlan(c, filepath.Join(root, "agent"), root)
					if err != nil {
						t.Fatal(err)
					}
					if err := ApplyChanges(plan); err != nil {
						t.Fatal(err)
					}
					if data, err := os.ReadFile(path); err != nil || string(data) != want {
						t.Fatalf("attempt %d changed user settings: %q %v", attempt, data, err)
					}
					if info, err := os.Stat(path); err != nil || info.Mode().Perm() != 0640 {
						t.Fatalf("attempt %d changed user permissions: %v %v", attempt, info, err)
					}
					if ready, err := migrationReady(c.Path, filepath.Join(root, "agent"), root); err != nil || !ready {
						t.Fatalf("attempt %d migration not ready: %v %v", attempt, ready, err)
					}
				}
			})
		}
	}
}

func TestMigrationRemovesAndRestoresMaskedRecoveryGates(t *testing.T) {
	for _, kind := range []string{"mihomo", "mosdns", "oxidns"} {
		t.Run(kind, func(t *testing.T) {
			c, root := testConfig(t, "systemd")
			c.Values["service_type"] = kind
			c.Values["service_unit"] = kind
			c.Values["service_manager"] = "systemd"
			gate := "configflow-recover-" + kind + ".service"
			gatePath := filepath.Join(root, "etc/systemd/system", gate)
			dropIn := filepath.Join(root, "etc/systemd/system", kind+".service.d/configflow-recovery.conf")
			writeRecoveryFixture(t, dropIn, "[Unit]\nRequires="+gate+"\nAfter="+gate+"\n", 0644)
			if err := os.Symlink("/dev/null", gatePath); err != nil {
				t.Fatal(err)
			}
			plan, err := MigrationPlan(c, filepath.Join(root, "agent"), root)
			if err != nil {
				t.Fatal(err)
			}
			var snapshots []Snapshot
			for _, change := range plan {
				snapshot, err := captureChange(change)
				if err != nil {
					t.Fatal(err)
				}
				if change.Path == gatePath && (!change.AllowSymlink || snapshot.LinkTarget != "/dev/null") {
					t.Fatalf("masked gate was not captured as an owned link: %+v %+v", change, snapshot)
				}
				snapshots = append(snapshots, snapshot)
			}
			if err := ApplyChanges(plan); err != nil {
				t.Fatal(err)
			}
			for _, path := range []string{gatePath, dropIn} {
				if _, err := os.Lstat(path); !os.IsNotExist(err) {
					t.Fatalf("masked gate or dependency survived migration: %s %v", path, err)
				}
			}
			if ready, err := migrationReady(c.Path, filepath.Join(root, "agent"), root); err != nil || !ready {
				t.Fatalf("masked gate migration not ready: %v %v", ready, err)
			}
			saved, err := json.Marshal(snapshots)
			if err != nil {
				t.Fatal(err)
			}
			if err := json.Unmarshal(saved, &snapshots); err != nil {
				t.Fatal(err)
			}
			if err := Restore(snapshots); err != nil {
				t.Fatal(err)
			}
			if target, err := os.Readlink(gatePath); err != nil || target != "/dev/null" {
				t.Fatalf("rollback did not restore the gate mask: %q %v", target, err)
			}
			if data, err := os.ReadFile(dropIn); err != nil || string(data) != "[Unit]\nRequires="+gate+"\nAfter="+gate+"\n" {
				t.Fatalf("rollback did not restore the dependency: %q %v", data, err)
			}
		})
	}
}
