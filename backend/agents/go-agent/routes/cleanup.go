package routes

import (
	"fmt"
	"os"
	"path/filepath"
)

// CleanupDisk only discards reproducible payloads of terminal transactions.
// Keep journals, manifests and backups for diagnosis; never touch live data.
func CleanupDisk(cfg *Config) error {
	release, err := AcquireServiceOperation(cfg)
	if err != nil {
		return err
	}
	defer release()
	return cleanupDeploymentPayloads(cfg)
}

func cleanupDeploymentPayloads(cfg *Config) error {
	entries, err := os.ReadDir(deploymentRoot(cfg))
	if err != nil {
		return err
	}
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		s, err := loadDeployment(cfg, entry.Name())
		if err != nil {
			continue // Unknown or damaged journals may still need recovery.
		}
		if s.Status != "succeeded" && s.Status != "failed" && s.Status != "rolled_back" {
			continue
		}
		for _, name := range []string{"stage", "bundle.tar.gz"} {
			if err := os.RemoveAll(filepath.Join(deploymentPath(cfg, entry.Name()), name)); err != nil {
				return fmt.Errorf("cleanup deployment %s: %w", entry.Name(), err)
			}
		}
		if err := syncDirectory(deploymentPath(cfg, entry.Name())); err != nil {
			return err
		}
	}
	return nil
}
