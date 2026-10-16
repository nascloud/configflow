package routes

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestDeploymentCheckDoesNotCreateState(t *testing.T) {
	cfg := &Config{ConfigPath: filepath.Join(t.TempDir(), "config.yaml")}
	if err := CheckDeployments(cfg); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(deploymentRoot(cfg)); !os.IsNotExist(err) {
		t.Fatalf("inspection created deployment state: %v", err)
	}
}

func TestDeploymentCheckReportsWithoutChangingJournalsOrTakingLock(t *testing.T) {
	for _, kind := range []string{"mihomo", "mosdns", "oxidns"} {
		for _, status := range []string{"receiving", "verifying", "ready", "stopping", "backing_up", "replacing", "starting", "checking", "rolling_back", "recovery_pending", "rollback_failed", "succeeded", "failed", "rolled_back", "unknown"} {
			t.Run(kind+"/"+status, func(t *testing.T) {
				cfg := &Config{ConfigPath: filepath.Join(t.TempDir(), "config.yaml"), ServiceType: kind}
				release, err := AcquireServiceOperation(cfg)
				if err != nil {
					t.Fatal(err)
				}
				defer release()
				state := &DeploymentState{DeploymentID: "inspect", WasRunning: true}
				if err := saveDeployment(cfg, state, status); err != nil {
					t.Fatal(err)
				}
				path := filepath.Join(deploymentPath(cfg, state.DeploymentID), "state.json")
				before, err := os.ReadFile(path)
				if err != nil {
					t.Fatal(err)
				}
				err = CheckDeployments(cfg)
				wantWarning := status != "ready" && status != "succeeded" && status != "failed" && status != "rolled_back"
				if (err != nil) != wantWarning {
					t.Fatalf("status %s: %v", status, err)
				}
				if err != nil && (!strings.Contains(err.Error(), "inspect") || !strings.Contains(err.Error(), status)) {
					t.Fatalf("warning must identify deployment and state: %v", err)
				}
				after, err := os.ReadFile(path)
				if err != nil || !bytes.Equal(before, after) {
					t.Fatalf("inspection modified journal: %v", err)
				}
			})
		}
	}
}

func TestDeploymentCheckPreservesUnjournaledAndDamagedTransactions(t *testing.T) {
	cfg := &Config{ConfigPath: filepath.Join(t.TempDir(), "config.yaml")}
	for _, id := range []string{"empty", "corrupt"} {
		if err := os.MkdirAll(deploymentPath(cfg, id), 0700); err != nil {
			t.Fatal(err)
		}
	}
	path := filepath.Join(deploymentPath(cfg, "corrupt"), "state.json")
	if err := os.WriteFile(path, []byte("broken journal"), 0600); err != nil {
		t.Fatal(err)
	}
	err := CheckDeployments(cfg)
	if err == nil || !strings.Contains(err.Error(), "empty") || !strings.Contains(err.Error(), "corrupt") {
		t.Fatalf("missing diagnostic: %v", err)
	}
	if _, err := os.Stat(deploymentPath(cfg, "empty")); err != nil {
		t.Fatal("inspection removed unjournaled transaction")
	}
	if got, err := os.ReadFile(path); err != nil || string(got) != "broken journal" {
		t.Fatal("inspection changed damaged journal")
	}
	if _, err := os.Stat(filepath.Join(deploymentRoot(cfg), "operation.lock")); !os.IsNotExist(err) {
		t.Fatal("inspection created operation lock")
	}
}
