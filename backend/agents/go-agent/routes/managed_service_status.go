package routes

// ManagedServiceStatus uses the same manager/unit as activation and rollback.
// Legacy process-name discovery can report an unrelated core as running.
func ManagedServiceStatus(cfg *Config) string {
	running, err := serviceRunning(cfg)
	if err != nil {
		return "unknown"
	}
	if running {
		return "active"
	}
	return "inactive"
}
