package main

import (
	"encoding/json"
	"os"
	"sync"
)

// Config 结构体完整定义了 agent 的所有配置项
type Config struct {
	ServerURL          string `json:"server_url"`
	AgentName          string `json:"agent_name"`
	AgentHost          string `json:"agent_host"`
	AgentPort          int    `json:"agent_port"`
	AgentIP            string `json:"agent_ip,omitempty"` // omitempty: 如果为空则不在JSON中显示
	ServiceType        string `json:"service_type"`
	DeploymentMethod   string `json:"deployment_method,omitempty"`
	ServiceName        string `json:"service_name"`
	ConfigPath         string `json:"config_path"`
	RestartCommand     string `json:"restart_command"`
	HeartbeatInterval  int    `json:"heartbeat_interval"`
	AgentID            string `json:"agent_id,omitempty"`
	Token              string `json:"token,omitempty"`

	// 监控功能配置（使用指针以区分未设置和 false）
	EnableMetrics      *bool  `json:"enable_metrics,omitempty"` // 是否启用系统监控，默认 true

	// MosDNS 特殊功能字段
	Directories       []string               `json:"directories,omitempty"`
	RulesetDownloads  []RulesetDownloadItem  `json:"ruleset_downloads,omitempty"`

	// 用于保护配置文件读写的互斥锁
	mu sync.Mutex `json:"-"` // - 表示不参与JSON序列化
	filePath string `json:"-"`
}

// RulesetDownloadItem 定义规则集下载项的结构
type RulesetDownloadItem struct {
	Name      string `json:"name"`
	URL       string `json:"url"`
	LocalPath string `json:"local_path"`
}

// LoadConfig 从指定路径加载配置
func LoadConfig(path string) (*Config, error) {
	var cfg Config
	
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	
	if err := json.Unmarshal(data, &cfg); err != nil {
		return nil, err
	}
	
	if cfg.HeartbeatInterval == 0 {
		cfg.HeartbeatInterval = 30 // 设置默认心跳间隔
	}
	
	cfg.filePath = path
	return &cfg, nil
}

// Save 将当前配置写回到文件
func (c *Config) Save() error {
	c.mu.Lock()
	defer c.mu.Unlock()

	// 使用 MarshalIndent 美化输出格式
	data, err := json.MarshalIndent(c, "", "  ")
	if err != nil {
		return err
	}

	return os.WriteFile(c.filePath, data, 0644)
}

// IsMetricsEnabled 检查是否启用系统监控
// 如果未设置（nil），默认返回 true（启用）
func (c *Config) IsMetricsEnabled() bool {
	if c.EnableMetrics == nil {
		return true // 默认启用监控
	}
	return *c.EnableMetrics
}

// ConvertRulesetDownloads 转换规则集下载项
func (c *Config) ConvertRulesetDownloads() []RulesetDownloadItem {
	return c.RulesetDownloads
}