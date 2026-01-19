# Makefile for Sublink Agent

# 默认目标
.DEFAULT_GOAL := help

# 获取当前目录
PWD := $(shell pwd)

# 应用名称
APP_NAME := configflow-agent

# Go 参数
GO_BUILD_FLAGS := -ldflags="-s -w"

# Docker 镜像名称
DOCKER_IMAGE := configflow/agent

# Docker 容器名称
DOCKER_CONTAINER := configflow-agent

# 帮助信息
.PHONY: help
help: ## 显示帮助信息
	@echo "Usage: make [target]"
	@echo ""
	@echo "General targets:"
	@awk 'BEGIN {FS = ":.*##"; printf "  \033[36m%-15s\033[0m %s\n", "help", "显示帮助信息"} /^[$$()% a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

##@ 开发相关

# 格式化代码
.PHONY: fmt
fmt: ## 格式化 Go 代码
	go fmt ./...

# 检查代码错误
.PHONY: vet
vet: ## 检查 Go 代码错误
	go vet ./...

# 运行测试
.PHONY: test
test: ## 运行测试
	go test -v ./...

##@ 构建相关

# 构建应用
.PHONY: build
build: ## 构建应用
	go build $(GO_BUILD_FLAGS) -o $(APP_NAME) .

# 构建所有平台的二进制
.PHONY: build-all
build-all: ## 构建所有平台的二进制文件 (Linux amd64/arm64/armv7)
	@echo "Building for multiple platforms..."
	@echo "Ensuring dependencies are available..."
	@go mod download || true
	@mkdir -p dist
	CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build $(GO_BUILD_FLAGS) -o dist/$(APP_NAME)-linux-amd64 .
	CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build $(GO_BUILD_FLAGS) -o dist/$(APP_NAME)-linux-arm64 .
	CGO_ENABLED=0 GOOS=linux GOARCH=arm GOARM=7 go build $(GO_BUILD_FLAGS) -o dist/$(APP_NAME)-linux-armv7 .
	@echo "Build complete! Binaries are in dist/"
	@ls -lh dist/

# 清理构建产物
.PHONY: clean
clean: ## 清理构建产物
	rm -f $(APP_NAME)
	rm -rf dist/

# 构建 Docker 镜像
.PHONY: docker-build
docker-build: ## 构建 Docker 镜像
	docker build -t $(DOCKER_IMAGE) .

# 推送 Docker 镜像
.PHONY: docker-push
docker-push: ## 推送 Docker 镜像
	docker push $(DOCKER_IMAGE)

##@ 运行相关

# 运行应用
.PHONY: run
run: ## 运行应用
	go run main.go

# 启动 Docker 容器
.PHONY: docker-run
docker-run: ## 启动 Docker 容器
	docker run --rm -it $(DOCKER_IMAGE)

# 启动 Docker Compose
.PHONY: compose-up
compose-up: ## 启动 Docker Compose
	docker-compose up -d

# 停止 Docker Compose
.PHONY: compose-down
compose-down: ## 停止 Docker Compose
	docker-compose down

# 查看 Docker Compose 日志
.PHONY: compose-logs
compose-logs: ## 查看 Docker Compose 日志
	docker-compose logs -f

##@ 安装相关

# 安装到系统
.PHONY: install
install: build ## 安装应用到系统
	sudo cp $(APP_NAME) /usr/local/bin/
	sudo mkdir -p /etc/configflow-agent
	sudo chown $$(whoami) /etc/configflow-agent

# 卸载应用
.PHONY: uninstall
uninstall: ## 卸载应用
	sudo rm -f /usr/local/bin/$(APP_NAME)
	sudo rm -rf /etc/configflow-agent