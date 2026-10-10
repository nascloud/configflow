package main

import "agent/routes"

func (c *Config) managedServiceStatus() string {
	if c.ServiceManager == "" {
		return getServiceStatus(c.ServiceName)
	}
	return routes.ManagedServiceStatus(toRoutesConfig(c))
}
