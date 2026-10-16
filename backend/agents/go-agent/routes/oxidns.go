package routes

import (
	"context"
	"fmt"
	"gopkg.in/yaml.v3"
	"net"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

func prepareOxiDNSRuntimeDirectories(cfg *Config) error {
	data, err := os.ReadFile(cfg.ConfigPath)
	if err != nil {
		return err
	}
	var doc map[string]interface{}
	if err := yaml.Unmarshal(data, &doc); err != nil {
		return err
	}
	plugins, _ := doc["plugins"].([]interface{})
	uid, gid := -1, -1
	if info, err := os.Stat(cfg.ConfigPath); err == nil {
		uid, gid = fileOwnership(info)
	}
	for _, value := range plugins {
		plugin, _ := value.(map[string]interface{})
		args, _ := plugin["args"].(map[string]interface{})
		var key string
		switch plugin["type"] {
		case "dynamic_domain_set", "query_recorder":
			key = "path"
		case "cache":
			key = "dump_file"
		}
		path, _ := args[key].(string)
		if path == "" {
			continue
		}
		rel := filepath.ToSlash(filepath.Clean(path))
		if !safeRelative(rel) {
			return fmt.Errorf("OxiDNS runtime path escapes service directory")
		}
		root := filepath.Dir(cfg.ConfigPath)
		if err := rejectSymlinkPath(root, rel); err != nil {
			return err
		}
		if err := ensureManagedParent(root, rel, uid, gid); err != nil {
			return err
		}
	}
	return nil
}

// The transaction root is the config directory. Reject a systemd core whose
// runtime resolves rule/state paths elsewhere before any configuration changes.
func ValidateOxiDNSServiceDirectory(cfg *Config) error {
	if cfg.ServiceType != "oxidns" || cfg.ServiceManager != "systemd" {
		return nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	data, err := exec.CommandContext(ctx, "systemctl", "show", cfg.ServiceUnit, "--property=ExecStart", "--property=WorkingDirectory").Output()
	if err != nil {
		return fmt.Errorf("cannot inspect OxiDNS service working directory")
	}
	return validateOxiDNSSystemdDirectory(string(data), cfg.ConfigPath)
}

func validateOxiDNSSystemdDirectory(properties, expectedConfig string) error {
	expected := filepath.Dir(expectedConfig)
	working, command, config := "", "", "config.yaml"
	for _, line := range strings.Split(properties, "\n") {
		if strings.HasPrefix(line, "WorkingDirectory=") {
			working = strings.TrimPrefix(line, "WorkingDirectory=")
		}
		if strings.HasPrefix(line, "ExecStart=") {
			command = strings.TrimPrefix(line, "ExecStart=")
		}
	}
	if index := strings.Index(command, "argv[]="); index >= 0 {
		command = strings.SplitN(command[index+7:], ";", 2)[0]
	}
	args := strings.Fields(command)
	for i, arg := range args {
		if (arg == "-d" || arg == "--dir" || arg == "--working-dir") && i+1 < len(args) {
			working = args[i+1]
		}
		for _, prefix := range []string{"-d=", "--dir=", "--working-dir="} {
			if strings.HasPrefix(arg, prefix) {
				working = strings.TrimPrefix(arg, prefix)
			}
		}
		if (arg == "-c" || arg == "--config") && i+1 < len(args) {
			config = args[i+1]
		}
		for _, prefix := range []string{"-c=", "--config="} {
			if strings.HasPrefix(arg, prefix) {
				config = strings.TrimPrefix(arg, prefix)
			}
		}
		// Clap also accepts attached short-option values (e.g. -cother.yaml).
		if strings.HasPrefix(arg, "-c") && !strings.HasPrefix(arg, "--") && len(arg) > 2 {
			config = strings.TrimPrefix(arg[2:], "=")
		}
		if strings.HasPrefix(arg, "-d") && !strings.HasPrefix(arg, "--") && len(arg) > 2 {
			working = strings.TrimPrefix(arg[2:], "=")
		}
	}
	decodePath := func(value string) string {
		value = strings.Trim(value, "\"'")
		if decoded, err := strconv.Unquote("\"" + value + "\""); err == nil {
			return decoded
		}
		return value
	}
	working, config = decodePath(working), decodePath(config)
	if command == "" || working == "" || !filepath.IsAbs(working) || filepath.Clean(working) != filepath.Clean(expected) {
		return fmt.Errorf("OxiDNS service -d/WorkingDirectory must equal the Agent config directory %s; update the core service working directory or select its actual same-directory configuration before installing/pushing", expected)
	}
	// OxiDNS changes cwd to -d before loading -c; without -c it loads config.yaml.
	if !filepath.IsAbs(config) {
		config = filepath.Join(working, config)
	}
	if !filepath.IsAbs(expectedConfig) || filepath.Clean(config) != filepath.Clean(expectedConfig) {
		return fmt.Errorf("OxiDNS service -c/--config must select the Agent config_path %s; update the Agent path or core service before installing/pushing", expectedConfig)
	}
	return nil
}

// OxiDNS state belongs to the running service, not a configuration revision.
// Native checking uses an isolated state directory and never opens the live DB.
func validateOxiDNS(cfg *Config, doc map[string]interface{}, stage, config string, manifest *DeploymentManifest) error {
	if err := validateMosDNS(doc, stage, manifest); err != nil {
		return err
	}
	for _, f := range manifest.Files {
		if f.Path == "state" || strings.HasPrefix(f.Path, "state/") {
			return fmt.Errorf("OxiDNS runtime state cannot be replaced by a deployment")
		}
	}
	if includes, ok := doc["include"].([]interface{}); ok && len(includes) > 0 {
		return fmt.Errorf("OxiDNS deployment must inline included plugin definitions")
	}
	plugins, _ := doc["plugins"].([]interface{})
	listed := map[string]bool{}
	for _, file := range manifest.Files {
		listed[file.Path] = true
	}
	for _, value := range plugins {
		p, _ := value.(map[string]interface{})
		args, _ := p["args"].(map[string]interface{})
		kind, _ := p["type"].(string)
		references := []interface{}{}
		if file, ok := args["file"]; ok {
			references = append(references, file)
		}
		if persistent, ok := args["persistent"].(map[string]interface{}); ok {
			if files, ok := persistent["files"].([]interface{}); ok {
				references = append(references, files...)
			}
		}
		for _, value := range references {
			raw, ok := value.(string)
			if !ok {
				return fmt.Errorf("invalid OxiDNS managed file reference")
			}
			rel := filepath.ToSlash(filepath.Clean(raw))
			if !safeRelative(rel) || !listed[rel] {
				return fmt.Errorf("OxiDNS rule files must be included in the deployment")
			}
			if _, err := resolveStagedReference(cfg, stage, raw); err != nil {
				return err
			}
		}
		// Keep all generated persistent outputs outside the managed manifest.
		key := ""
		switch kind {
		case "dynamic_domain_set", "query_recorder":
			key = "path"
		case "cache":
			key = "dump_file"
		}
		if key != "" {
			raw, _ := args[key].(string)
			if raw != "" {
				rel := filepath.ToSlash(filepath.Clean(raw))
				if !safeRelative(rel) {
					return fmt.Errorf("OxiDNS runtime paths must be relative to the service directory")
				}
				for _, f := range manifest.Files {
					if f.Path == rel {
						return fmt.Errorf("OxiDNS runtime file collides with managed deployment file")
					}
				}
				if err := os.MkdirAll(filepath.Join(stage, filepath.Dir(rel)), 0700); err != nil {
					return err
				}
			}
		}
	}
	binary := cfg.ServiceBinary
	if binary == "" {
		binary = "oxidns"
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, binary, "check", "-c", config, "-d", stage)
	command.Dir = stage
	// OxiDNS diagnostics can contain upstream/API credentials. Return only status.
	if err := command.Run(); err != nil {
		if ctx.Err() != nil {
			return fmt.Errorf("OxiDNS native configuration check timed out")
		}
		return fmt.Errorf("OxiDNS native configuration check failed; check the configuration and installed core features")
	}
	return verifyManifestFiles(stage, manifest)
}

// Derive credentials only for the configured local management endpoint. An
// arbitrary health_url must never receive the core's management credentials.
func oxidnsHealthEndpoint(doc map[string]interface{}, override string) (string, string, string, error) {
	api, _ := doc["api"].(map[string]interface{})
	endpoint, _ := api["http"].(string)
	httpConfig, _ := api["http"].(map[string]interface{})
	if httpConfig != nil {
		endpoint, _ = httpConfig["listen"].(string)
	}
	if endpoint == "" {
		return override, "", "", nil
	}
	scheme := "http"
	if ssl, ok := httpConfig["ssl"].(map[string]interface{}); ok && len(ssl) > 0 {
		// A loopback URL cannot authenticate a hostname certificate or client-cert
		// endpoint. Keep actual DNS + service probes; never disable TLS validation.
		if override == "" {
			return "", "", "", nil
		}
		scheme = "https"
	}
	expected := scheme + "://" + loopbackAddress(endpoint) + "/api/readyz"
	target := expected
	if override != "" {
		target = override
	}
	parsed, err := url.Parse(target)
	if err != nil || parsed.Host == "" {
		return "", "", "", fmt.Errorf("invalid OxiDNS health URL")
	}
	// Only the endpoint defined in the active config may receive Basic auth.
	configured, _ := url.Parse(expected)
	sameHost := parsed.Host == configured.Host
	if !sameHost {
		targetHost, targetPort, _ := net.SplitHostPort(parsed.Host)
		configHost, configPort, _ := net.SplitHostPort(configured.Host)
		sameHost = targetPort == configPort && net.ParseIP(targetHost) != nil && net.ParseIP(configHost) != nil && net.ParseIP(targetHost).Equal(net.ParseIP(configHost))
	}
	username, password := "", ""
	if sameHost && parsed.Scheme == configured.Scheme {
		auth, _ := httpConfig["auth"].(map[string]interface{})
		if auth["type"] == "basic" {
			username, _ = auth["username"].(string)
			password, _ = auth["password"].(string)
		}
	}
	return target, username, password, nil
}
