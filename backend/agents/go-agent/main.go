package main

import (
	"agent/routes"
	"agent/upgrade"
	"flag"
	"fmt"
	"log"
	"os"
	"path/filepath"
)

func main() {
	// 使用命令行参数指定配置文件路径
	// Go的flag包支持单横线(-)和双横线(--)两种格式的参数
	configPath := flag.String("config", "", "Path to the configuration file")
	printVersion := flag.Bool("version", false, "Print Agent version and exit")
	upgradeWorker := flag.String("upgrade-worker", "", "Run a durable Agent update outside the Agent service")
	recoverOnly := flag.Bool("recover-only", false, "Restore interrupted deployments before managed services start; do not register or listen")
	recoverAndExit := flag.Bool("recover-and-exit", false, "Recover interrupted deployments with service lifecycle, then exit without registration")

	// 解析命令行参数
	flag.Parse()
	if *printVersion {
		fmt.Println(upgrade.Version)
		return
	}
	if *upgradeWorker != "" {
		if err := upgrade.Run(*upgradeWorker); err != nil {
			log.Fatalf("Agent update: %v", err)
		}
		return
	}

	// 如果没有通过命令行参数指定配置文件路径，则使用自动检测
	if *configPath == "" {
		// 获取agent配置目录，优先使用环境变量，否则使用默认路径
		agentDirs := []string{"/opt/configflow-agent", "/opt/sublink-agent"}
		if envAgentDir := os.Getenv("AGENT_DIR"); envAgentDir != "" {
			agentDirs = []string{envAgentDir}
		}
		configFiles := []string{"config-mihomo.json", "config-mosdns.json", "config.json"}
		for _, agentDir := range agentDirs {
			for _, configFile := range configFiles {
				fullPath := filepath.Join(agentDir, configFile)
				if _, err := os.Stat(fullPath); err == nil {
					*configPath = fullPath
					break
				}
			}
			if *configPath != "" {
				break
			}
		}
		if *configPath == "" {
			*configPath = filepath.Join(agentDirs[0], "config.json")
		}
	}

	log.Printf("Using config file: %s", *configPath)
	if !*recoverOnly && !*recoverAndExit {
		binary, err := os.Executable()
		if err != nil {
			log.Fatal(err)
		}
		if err = upgrade.EnsureInstalled(*configPath, binary); err != nil {
			log.Fatalf("Agent migration: %v", err)
		}
	}

	// 1. 加载配置
	cfg, err := LoadConfig(*configPath)
	if err != nil {
		log.Fatalf("Fatal: Could not load configuration from %s: %v", *configPath, err)
	}
	log.Println("Configuration loaded successfully.")

	// Recovery must precede registration, heartbeat and API traffic. Boot gates call
	// recover-only before the managed services or container defaults can run.
	if err := routes.RecoverDeployments(toRoutesConfig(cfg), !*recoverOnly); err != nil {
		if *recoverOnly || *recoverAndExit {
			log.Fatalf("Fatal: Deployment recovery failed; refusing startup: %v", err)
		}
		// Keep authenticated status/log APIs available for repair. All mutating
		// service routes reject the unresolved durable recovery journal.
		log.Printf("Deployment recovery requires attention; service mutations remain blocked: %v", err)
	}
	if *recoverOnly || *recoverAndExit {
		log.Println("Deployment recovery completed.")
		return
	}

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
