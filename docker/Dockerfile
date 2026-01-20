# ==============================================================================
# Stage 1: 构建 Go Agent 二进制文件
# ==============================================================================
FROM golang:1.21-alpine AS go-builder

WORKDIR /build
ENV GOPROXY=https://goproxy.cn,direct

RUN --mount=type=bind,source=backend/agents/go-agent,target=/src \
    --mount=type=cache,target=/go/pkg/mod \
    cp /src/go.mod /src/go.sum ./ && go mod download && cp -r /src/* ./ && \
    mkdir -p /dist && \
    CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -ldflags="-s -w" -o /dist/configflow-agent-linux-amd64 . && \
    CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -ldflags="-s -w" -o /dist/configflow-agent-linux-arm64 . && \
    CGO_ENABLED=0 GOOS=linux GOARCH=arm GOARM=7 go build -ldflags="-s -w" -o /dist/configflow-agent-linux-armv7 .

# ==============================================================================
# Stage 2: 构建前端
# ==============================================================================
FROM node:18-alpine AS frontend-builder

WORKDIR /app/frontend

RUN --mount=type=bind,source=frontend,target=/src \
    --mount=type=cache,target=/root/.npm \
    cp /src/package*.json ./ && npm ci && cp -r /src/* ./ && npm run build

# ==============================================================================
# Stage 3: 最终镜像
# ==============================================================================
FROM python:3.11-slim

WORKDIR /app

# 复制依赖文件、Go Agent、前端构建产物
COPY backend/requirements.txt ./
COPY --from=go-builder /dist/ /opt/configflow/static/agents/
COPY --from=frontend-builder /app/frontend/dist /usr/share/nginx/html

# 安装依赖、配置 nginx/supervisor
RUN apt-get update && \
    apt-get install -y --no-install-recommends nginx supervisor && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir -r requirements.txt && \
    (groupadd -r nginx || true) && (useradd -r -g nginx -s /sbin/nologin -d /nonexistent nginx || true) && \
    mkdir -p /data /var/log/supervisor /var/cache/nginx /var/log/nginx /opt/configflow/static/agents /etc/supervisor/conf.d && \
    chown -R nginx:nginx /var/cache/nginx /var/log/nginx /usr/share/nginx/html

# 复制后端代码和配置文件
COPY backend ./backend
COPY main.py app.py ./
COPY docker/nginx.conf /etc/nginx/nginx.conf
COPY docker/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

ENV FLASK_APP=app.py PYTHONUNBUFFERED=1 DATA_DIR=/data TZ=Asia/Shanghai

EXPOSE 80

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
