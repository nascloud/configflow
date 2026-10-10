package routes

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
)

// RemoveRecoveryInstallation removes only installer-owned boot dependencies.
// It must run before uninstall removes the recovery binary or Agent config.
func RemoveRecoveryInstallation(cfg *Config) error {
	if cfg.ServiceType != "mihomo" && cfg.ServiceType != "mosdns" {
		return nil
	}
	unit := strings.TrimSuffix(cfg.ServiceUnit, ".service")
	if unit == "" {
		unit = cfg.ServiceType
	}
	if !regexp.MustCompile(`^[A-Za-z0-9_.@-]+$`).MatchString(unit) {
		return fmt.Errorf("invalid service unit for recovery cleanup")
	}
	gate := "configflow-recover-" + cfg.ServiceType
	dropin := filepath.Join("/etc/systemd/system", unit+".service.d", "configflow-recovery.conf")
	gateUnit := filepath.Join("/etc/systemd/system", gate+".service")
	systemdChanged := false
	for _, path := range []string{dropin, gateUnit} {
		err := os.Remove(path)
		if err == nil {
			systemdChanged = true
		} else if !os.IsNotExist(err) {
			return err
		}
	}
	if systemdChanged {
		if err := exec.Command("systemctl", "daemon-reload").Run(); err != nil {
			return err
		}
	}
	conf := filepath.Join("/etc/conf.d", unit)
	content, err := os.ReadFile(conf)
	if err == nil {
		clean := removeRecoverySection(string(content), cfg.ServiceType)
		if clean == string(content) && strings.Contains(string(content), "# BEGIN CONFIGFLOW RECOVERY "+cfg.ServiceType) {
			return fmt.Errorf("repair malformed OpenRC recovery marker before uninstalling")
		}
		if clean != string(content) {
			stat, statErr := os.Stat(conf)
			if statErr != nil {
				return statErr
			}
			if err := os.WriteFile(conf, []byte(clean), stat.Mode().Perm()); err != nil {
				return err
			}
		}
	} else if !os.IsNotExist(err) {
		return err
	}
	script := filepath.Join("/etc/init.d", gate)
	if _, err := os.Stat(script); err == nil {
		// A gate can be installed but not added to boot if installation failed.
		_ = exec.Command("rc-update", "del", gate, "boot").Run()
		if err := os.Remove(script); err != nil {
			return err
		}
		if err := exec.Command("rc-update", "-u").Run(); err != nil {
			return err
		}
	} else if !os.IsNotExist(err) {
		return err
	}
	return nil
}

func removeRecoverySection(content, service string) string {
	begin := "# BEGIN CONFIGFLOW RECOVERY " + service
	end := "# END CONFIGFLOW RECOVERY " + service
	lines := strings.SplitAfter(content, "\n")
	var output strings.Builder
	skipping := false
	for _, line := range lines {
		if strings.TrimSpace(line) == begin {
			skipping = true
			continue
		}
		if skipping && strings.TrimSpace(line) == end {
			skipping = false
			continue
		}
		if !skipping {
			output.WriteString(line)
		}
	}
	// A malformed marker should not eat unrelated user configuration.
	if skipping {
		return content
	}
	return output.String()
}
