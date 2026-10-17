package upgrade

import (
	"os"
	"path/filepath"
	"strings"
)

// Every update leaves a worker copy and a backup of the previous binary
// (several MB each) plus its journal and log. Nothing used to remove them, so
// small root filesystems filled up after a handful of updates.

// artifactID returns the update ID an artifact belongs to, or "" for files
// that are not per-update artifacts (current.json, update.lock, unknown).
func artifactID(name string) string {
	switch {
	case strings.HasPrefix(name, "worker-"):
		name = strings.TrimPrefix(name, "worker-")
	case strings.HasPrefix(name, "backup-"):
		name = strings.TrimPrefix(name, "backup-")
	case strings.HasPrefix(name, "job-") && strings.HasSuffix(name, ".json"):
		name = strings.TrimSuffix(strings.TrimPrefix(name, "job-"), ".json")
	case strings.HasSuffix(name, ".log"):
		name = strings.TrimSuffix(name, ".log")
	default:
		return ""
	}
	if !idPattern.MatchString(name) {
		return ""
	}
	return name
}

// PruneCompleted removes artifacts of finished updates. It does nothing while
// an update holds the lock or the current update is unfinished or needs manual
// rollback recovery. The current update keeps its journal, log and backup (the
// previous binary, for manual recovery); its worker copy is no longer needed.
func PruneCompleted(binary string) error {
	root := rootFor(binary)
	if _, err := os.Stat(root); os.IsNotExist(err) {
		return nil
	}
	release, err := lock(filepath.Join(root, "update.lock"))
	if err != nil {
		return nil // an update is running; try again next round
	}
	defer release()
	current, err := loadJob(CurrentPath(binary))
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return err
	}
	if !terminal(current.Status.Status) || current.Status.Status == "rollback_failed" {
		return nil
	}
	entries, err := os.ReadDir(root)
	if err != nil {
		return err
	}
	for _, entry := range entries {
		name := entry.Name()
		id := artifactID(name)
		if id == "" || entry.IsDir() {
			continue
		}
		if id == current.ID && !strings.HasPrefix(name, "worker-") {
			continue
		}
		if err := os.Remove(filepath.Join(root, name)); err != nil && !os.IsNotExist(err) {
			return err
		}
	}
	return nil
}
