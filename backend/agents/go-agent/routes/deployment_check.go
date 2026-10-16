package routes

import (
	"errors"
	"fmt"
	"os"
)

// CheckDeployments reports journals that need attention without taking the
// operation lock, creating directories, changing journals or touching the core.
// A concurrent publish may appear here: this is an advisory snapshot, never a
// reason to interrupt the running service or fail Agent startup.
func CheckDeployments(cfg *Config) error {
	root := deploymentRoot(cfg)
	info, err := os.Lstat(root)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("cannot inspect deployment directory: %w", err)
	}
	if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return fmt.Errorf("invalid deployment directory")
	}
	entries, err := os.ReadDir(root)
	if err != nil {
		return fmt.Errorf("cannot inspect deployment journals: %w", err)
	}
	var warnings []error
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		state, err := loadDeployment(cfg, entry.Name())
		if err != nil {
			warnings = append(warnings, fmt.Errorf("cannot read deployment %s: %w", entry.Name(), err))
			continue
		}
		switch state.Status {
		case "succeeded", "failed", "rolled_back", "ready":
			// Ready is an intentionally staged deployment, not an interruption.
		default:
			warnings = append(warnings, fmt.Errorf("deployment %s has unfinished or unrecognized status %q", state.DeploymentID, state.Status))
		}
	}
	return errors.Join(warnings...)
}
