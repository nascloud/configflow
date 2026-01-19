package main

import (
	"flag"
	"log"
	"os"
	"path/filepath"
)

func main() {
	// 使用命令行参数指定配置文件路径
	// Go的flag包支持单横线(-)和双横线(--)两种格式的参数
	configPath := flag.String("config", "", "Path to the configuration file")
	
	// 解析命令行参数
	flag.Parse()
	
	// 如果没有通过命令行参数指定配置文件路径，则使用自动检测
	if *configPath == "" {
		// 获取agent配置目录，优先使用环境变量，否则使用默认路径
		agentDir := "/opt/sublink-agent"
		if envAgentDir := os.Getenv("AGENT_DIR"); envAgentDir != "" {
			agentDir = envAgentDir
		}
		
		// 定义可能的配置文件名
		configFiles := []string{
			"config-mihomo.json",
			"config-mosdns.json",
			"config.json",
		}
		
		// 检查每个配置文件是否存在
		for _, configFile := range configFiles {
			fullPath := filepath.Join(agentDir, configFile)
			if _, err := os.Stat(fullPath); err == nil {
				*configPath = fullPath
				break
			}
		}
		
		// 如果都没找到，使用默认配置文件
		if *configPath == "" {
			*configPath = filepath.Join(agentDir, "config.json")
		}
	}
	
	log.Printf("Using config file: %s", *configPath)

	// 1. 加载配置
	cfg, err := LoadConfig(*configPath)
	if err != nil {
		log.Fatalf("Fatal: Could not load configuration from %s: %v", *configPath, err)
	}
	log.Println("Configuration loaded successfully.")

	// 2. 如果未注册，执行注册流程
	if cfg.AgentID == "" || cfg.Token == "" {
		if err := cfg.RegisterAgent(); err != nil {
			log.Fatalf("Fatal: Agent registration failed: %v", err)
		}
		// 注册成功后，配置已更新并保存
	} else {
		log.Printf("Agent already registered with ID: %s", cfg.AgentID)
	}

	// 3. 在后台启动心跳循环
	StartHeartbeatLoop(cfg)

	// 4. 在前台启动 API 服务器 (这是一个阻塞操作)
	StartAPIServer(cfg)
}