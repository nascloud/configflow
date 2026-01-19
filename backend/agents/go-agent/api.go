package main

import (
	"fmt"
	"log"
	"net/http"
	
	// 导入路由模块
	"agent/routes"
)

// StartAPIServer 启动内置的 HTTP 服务器
func StartAPIServer(cfg *Config) {
	mux := http.NewServeMux()
	
	// 使用路由注册函数注册所有路由
	routes.RegisterRoutes(mux, toRoutesConfig(cfg))

	listenAddr := fmt.Sprintf("%s:%d", cfg.AgentHost, cfg.AgentPort)
	log.Printf("Starting API server on %s", listenAddr)

	if err := http.ListenAndServe(listenAddr, mux); err != nil {
		log.Fatalf("Fatal: API server failed to start: %v", err)
	}
}

// toRoutesConfig 将主配置转换为路由模块使用的配置
func toRoutesConfig(cfg *Config) *routes.Config {
	// 转换规则集下载项
	rulesetDownloads := make([]routes.RulesetDownloadItem, len(cfg.RulesetDownloads))
	for i, item := range cfg.ConvertRulesetDownloads() {
		rulesetDownloads[i] = routes.RulesetDownloadItem{
			Name:      item.Name,
			URL:       item.URL,
			LocalPath: item.LocalPath,
		}
	}
	
	return &routes.Config{
		ServerURL:         cfg.ServerURL,
		AgentName:         cfg.AgentName,
		AgentHost:         cfg.AgentHost,
		AgentPort:         cfg.AgentPort,
		AgentIP:           cfg.AgentIP,
		ServiceType:       cfg.ServiceType,
		ServiceName:       cfg.ServiceName,
		ConfigPath:        cfg.ConfigPath,
		RestartCommand:    cfg.RestartCommand,
		HeartbeatInterval: cfg.HeartbeatInterval,
		AgentID:           cfg.AgentID,
		Token:             cfg.Token,
		Directories:       cfg.Directories,
		RulesetDownloads:  rulesetDownloads,
	}
}