package upgrade

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"testing"
)

const (
	oldID     = "1305003c08664c52a68c8d3613f0058c"
	currentID = "b56304567a6d4ab8bc650a70b3d7c33e"
)

func seedUpdates(t *testing.T, status string) (string, string) {
	t.Helper()
	dir := t.TempDir()
	binary := filepath.Join(dir, "configflow-agent")
	root := rootFor(binary)
	if err := os.MkdirAll(root, 0700); err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{oldID, currentID} {
		for _, name := range []string{"worker-" + id, "backup-" + id, id + ".log"} {
			os.WriteFile(filepath.Join(root, name), []byte("x"), 0600)
		}
		job := Job{Status: Status{ID: id, Status: "succeeded"}, Binary: binary}
		data, _ := json.Marshal(job)
		os.WriteFile(filepath.Join(root, "job-"+id+".json"), data, 0600)
	}
	current := Job{Status: Status{ID: currentID, Status: status}, Binary: binary}
	data, _ := json.Marshal(current)
	os.WriteFile(filepath.Join(root, "job-"+currentID+".json"), data, 0600)
	os.WriteFile(CurrentPath(binary), data, 0600)
	os.WriteFile(filepath.Join(root, "update.lock"), nil, 0600)
	os.WriteFile(filepath.Join(root, "notes.txt"), []byte("unrelated"), 0600)
	return binary, root
}

func listing(t *testing.T, root string) []string {
	t.Helper()
	entries, err := os.ReadDir(root)
	if err != nil {
		t.Fatal(err)
	}
	names := []string{}
	for _, entry := range entries {
		names = append(names, entry.Name())
	}
	sort.Strings(names)
	return names
}

func TestPruneCompletedKeepsOnlyCurrentRecoveryArtifacts(t *testing.T) {
	binary, root := seedUpdates(t, "succeeded")
	if err := PruneCompleted(binary); err != nil {
		t.Fatal(err)
	}
	want := []string{currentID + ".log", "backup-" + currentID, "current.json", "job-" + currentID + ".json", "notes.txt", "update.lock"}
	if got := listing(t, root); !sameNames(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
	if job, err := loadJob(CurrentPath(binary)); err != nil || job.ID != currentID {
		t.Fatalf("current journal must stay readable: %v %v", job, err)
	}
}

func TestPruneCompletedLeavesUnfinishedOrFailedRollbackAlone(t *testing.T) {
	for _, status := range []string{"downloading", "restarting", "rollback_failed"} {
		t.Run(status, func(t *testing.T) {
			binary, root := seedUpdates(t, status)
			before := listing(t, root)
			if err := PruneCompleted(binary); err != nil {
				t.Fatal(err)
			}
			if got := listing(t, root); !sameNames(got, before) {
				t.Fatalf("%s update must keep every artifact: %v", status, got)
			}
		})
	}
}

func TestPruneCompletedSkipsWhileUpdateHoldsLock(t *testing.T) {
	binary, root := seedUpdates(t, "succeeded")
	release, err := lock(filepath.Join(root, "update.lock"))
	if err != nil {
		t.Fatal(err)
	}
	defer release()
	before := listing(t, root)
	if err := PruneCompleted(binary); err != nil {
		t.Fatal(err)
	}
	if got := listing(t, root); !sameNames(got, before) {
		t.Fatal("pruning must not run while an update holds the lock")
	}
}

func TestPruneCompletedWithoutUpdatesIsNoop(t *testing.T) {
	binary := filepath.Join(t.TempDir(), "configflow-agent")
	if err := PruneCompleted(binary); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(rootFor(binary)); !os.IsNotExist(err) {
		t.Fatal("pruning must not create the updates directory")
	}
}

func sameNames(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
