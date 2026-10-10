#!/bin/sh
# ConfigFlow Go Agent: native Shell installation, systemd / OpenRC.
# Service: {service_type}; Agent: {agent_name}
set -eu

AGENT_DIR="/opt/configflow-agent"
CONFIG_FILE="$AGENT_DIR/config-{service_type}.json"
RECOVERY_SERVICE="configflow-recover-{service_type}"
SERVICE_UNIT="{service_unit}"

if [ "$(id -u)" -ne 0 ]; then
    echo "请使用 root 用户运行此脚本"
    exit 1
fi
if command -v rc-service >/dev/null 2>&1 && command -v rc-update >/dev/null 2>&1; then
    INIT_SYSTEM="openrc"
elif command -v systemctl >/dev/null 2>&1; then
    INIT_SYSTEM="systemd"
else
    echo "需要 systemd 或 OpenRC；自定义运行环境请显式配置停止、启动、状态命令及开机恢复。"
    exit 1
fi

remove_recovery_gate() {
    if [ "$INIT_SYSTEM" = "systemd" ]; then
        rm -f "/etc/systemd/system/${SERVICE_UNIT}.service.d/configflow-recovery.conf"
        systemctl stop "$RECOVERY_SERVICE" 2>/dev/null || true
        rm -f "/etc/systemd/system/${RECOVERY_SERVICE}.service"
        systemctl daemon-reload
    else
        if [ -f "/etc/conf.d/$SERVICE_UNIT" ]; then
            sed -i '/# BEGIN CONFIGFLOW RECOVERY {service_type}/,/# END CONFIGFLOW RECOVERY {service_type}/d' "/etc/conf.d/$SERVICE_UNIT"
        fi
        rc-service "$RECOVERY_SERVICE" stop 2>/dev/null || true
        rc-update del "$RECOVERY_SERVICE" boot 2>/dev/null || true
        rm -f "/etc/init.d/$RECOVERY_SERVICE"
        rc-update -u
    fi
}

echo "1) 安装或升级 Go Agent（包含事务发布与失败回滚）"
echo "2) 卸载 Agent"
echo "3) 取消"
printf '请输入选项 [1-3]: '
read -r choice < /dev/tty
case "$choice" in
    1) ;;
    2)
        # Restore before removing the only binary able to recover the journal.
        if [ -f "$CONFIG_FILE" ] && [ -x /usr/local/bin/configflow-agent ]; then
            /usr/local/bin/configflow-agent -config "$CONFIG_FILE" -recover-and-exit
        fi
        remove_recovery_gate
        if [ "$INIT_SYSTEM" = "systemd" ]; then
            systemctl stop configflow-agent 2>/dev/null || true
            systemctl disable configflow-agent 2>/dev/null || true
            rm -f /etc/systemd/system/configflow-agent.service
            systemctl daemon-reload
        else
            rc-service configflow-agent stop 2>/dev/null || true
            rc-update del configflow-agent default 2>/dev/null || true
            rm -f /etc/init.d/configflow-agent
            rc-update -u
        fi
        rm -f /usr/local/bin/configflow-agent
        rm -rf "$AGENT_DIR"
        rm -f /var/log/configflow-agent.log /var/log/configflow-agent.log.* /etc/logrotate.d/configflow-agent
        echo "Agent 已卸载，服务配置与发布备份已保留。"
        exit 0
        ;;
    *) exit 0 ;;
esac

case "$(uname -m)" in
    x86_64|amd64) AGENT_ARCH="linux-amd64" ;;
    aarch64|arm64) AGENT_ARCH="linux-arm64" ;;
    armv7l|armhf) AGENT_ARCH="linux-armv7" ;;
    *) echo "不支持的 CPU 架构"; exit 1 ;;
esac
BINARY_BASE={binary_download_url_shell}
BINARY_URL="$BINARY_BASE/configflow-agent-$AGENT_ARCH"
# /tmp is often mounted noexec. Stage beside the final executable so validation
# works and replacement is an atomic rename on the same filesystem.
mkdir -p /usr/local/bin
BINARY_TMP=$(mktemp /usr/local/bin/.configflow-agent.install.XXXXXX)
trap 'rm -f "$BINARY_TMP"' EXIT HUP INT TERM
echo "下载 Go Agent 二进制..."
# The download URL may carry credentials; do not print it.
if command -v wget >/dev/null 2>&1; then
    if ! wget -q -O "$BINARY_TMP" "$BINARY_URL"; then
        echo "Agent 二进制下载失败" >&2; exit 1
    fi
elif command -v curl >/dev/null 2>&1; then
    if ! curl -fsL -o "$BINARY_TMP" "$BINARY_URL"; then
        echo "Agent 二进制下载失败" >&2; exit 1
    fi
else
    echo "安装阶段需要 wget 或 curl 下载二进制"; exit 1
fi
chmod 755 "$BINARY_TMP"
"$BINARY_TMP" -h >/dev/null 2>&1

# Stop the previous Go or legacy Shell Agent before replacing its binary/config.
if [ "$INIT_SYSTEM" = "systemd" ]; then
    systemctl stop configflow-agent 2>/dev/null || true
else
    rc-service configflow-agent stop 2>/dev/null || true
fi
# Existing journals belong to the old config root, which may differ from the
# newly requested config_path. Recover that root before losing its reference.
if [ -f "$CONFIG_FILE" ]; then
    "$BINARY_TMP" -config "$CONFIG_FILE" -recover-and-exit
fi
mkdir -p "$AGENT_DIR"
chmod 700 "$AGENT_DIR"
EXISTING_ID=""
EXISTING_TOKEN=""
for old_config in "$CONFIG_FILE" "$AGENT_DIR/config.json"; do
    if [ -f "$old_config" ]; then
        old_name=$(sed -n 's/.*"agent_name"[[:space:]]*:[[:space:]]*"\([A-Za-z0-9_-]*\)".*/\1/p' "$old_config" | head -n 1)
        old_type=$(sed -n 's/.*"service_type"[[:space:]]*:[[:space:]]*"\([A-Za-z0-9_-]*\)".*/\1/p' "$old_config" | head -n 1)
        if [ "$old_name" = "{agent_name}" ] && [ "$old_type" = "{service_type}" ]; then
            EXISTING_ID=$(sed -n 's/.*"agent_id"[[:space:]]*:[[:space:]]*"\([A-Za-z0-9_.-]*\)".*/\1/p' "$old_config" | head -n 1)
            EXISTING_TOKEN=$(sed -n 's/.*"token"[[:space:]]*:[[:space:]]*"\([A-Za-z0-9_-]*\)".*/\1/p' "$old_config" | head -n 1)
            break
        fi
    fi
done
umask 077
cat > "$CONFIG_FILE.new" <<'CONFIGFLOW_JSON_EOF'
{config_json}
CONFIGFLOW_JSON_EOF
if [ "$INIT_SYSTEM" = "systemd" ]; then
    RESTART="systemctl restart $SERVICE_UNIT"
else
    RESTART="rc-service $SERVICE_UNIT restart"
fi
sed -i "s/__CONFIGFLOW_INIT__/$INIT_SYSTEM/g; s/__CONFIGFLOW_RESTART__/$RESTART/g; s/__CONFIGFLOW_AGENT_ID__/$EXISTING_ID/g; s/__CONFIGFLOW_TOKEN__/$EXISTING_TOKEN/g" "$CONFIG_FILE.new"
# Verify/restore with the new binary before committing the installation config.
"$BINARY_TMP" -config "$CONFIG_FILE.new" -recover-and-exit
mv "$BINARY_TMP" /usr/local/bin/configflow-agent
mv "$CONFIG_FILE.new" "$CONFIG_FILE"
chmod 600 "$CONFIG_FILE"

# Reuse an existing logrotate installation; publishing requires no new package.
if command -v logrotate >/dev/null 2>&1; then
    mkdir -p /etc/logrotate.d
    cat > /etc/logrotate.d/configflow-agent <<'LOGEOF'
/var/log/configflow-agent.log {
    rotate 3
    compress
    delaycompress
    missingok
    notifempty
    size 10M
    copytruncate
}
LOGEOF
fi

if [ "$INIT_SYSTEM" = "systemd" ]; then
    # RemainAfterExit makes this a boot gate, NOT a hook on every core restart.
    cat > "/etc/systemd/system/${RECOVERY_SERVICE}.service" <<'RECOVERYEOF'
[Unit]
Description=Recover ConfigFlow {service_type} deployment before core startup
After=local-fs.target
Before={service_unit}.service configflow-agent.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/configflow-agent -config /opt/configflow-agent/config-{service_type}.json -recover-only
RemainAfterExit=yes
RECOVERYEOF
    mkdir -p "/etc/systemd/system/${SERVICE_UNIT}.service.d"
    cat > "/etc/systemd/system/${SERVICE_UNIT}.service.d/configflow-recovery.conf" <<'GATEEOF'
[Unit]
Requires=configflow-recover-{service_type}.service
After=configflow-recover-{service_type}.service
GATEEOF
    cat > /etc/systemd/system/configflow-agent.service <<'SERVICEEOF'
[Unit]
Description=ConfigFlow Agent (Go)
Requires=configflow-recover-{service_type}.service
After=network.target configflow-recover-{service_type}.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/configflow-agent
ExecStart=/usr/local/bin/configflow-agent -config /opt/configflow-agent/config-{service_type}.json
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SERVICEEOF
    systemctl daemon-reload
    systemctl enable configflow-agent
    systemctl start configflow-agent
    systemctl is-active --quiet configflow-agent
else
    cat > "/etc/init.d/$RECOVERY_SERVICE" <<'RECOVERYEOF'
#!/sbin/openrc-run
name="ConfigFlow deployment recovery ({service_type})"
depend() {
    need localmount
    before {service_unit} configflow-agent
}
start() {
    ebegin "Recovering ConfigFlow {service_type} deployment"
    /usr/local/bin/configflow-agent -config /opt/configflow-agent/config-{service_type}.json -recover-only
    eend $?
}
RECOVERYEOF
    chmod 755 "/etc/init.d/$RECOVERY_SERVICE"
    mkdir -p /etc/conf.d
    touch "/etc/conf.d/$SERVICE_UNIT"
    sed -i '/# BEGIN CONFIGFLOW RECOVERY {service_type}/,/# END CONFIGFLOW RECOVERY {service_type}/d' "/etc/conf.d/$SERVICE_UNIT"
    cat >> "/etc/conf.d/$SERVICE_UNIT" <<'GATEEOF'
# BEGIN CONFIGFLOW RECOVERY {service_type}
rc_need="${rc_need:-} configflow-recover-{service_type}"
# END CONFIGFLOW RECOVERY {service_type}
GATEEOF
    cat > /etc/init.d/configflow-agent <<'SERVICEEOF'
#!/sbin/openrc-run
name="ConfigFlow Agent (Go)"
command="/usr/local/bin/configflow-agent"
command_args="-config /opt/configflow-agent/config-{service_type}.json"
pidfile="/run/configflow-agent.pid"
output_log="/var/log/configflow-agent.log"
error_log="/var/log/configflow-agent.log"
directory="/opt/configflow-agent"
command_background="yes"
depend() {
    need net configflow-recover-{service_type}
    after firewall
}
start_pre() {
    checkpath --directory --mode 0755 /var/log
    checkpath --file --mode 0644 --owner root:root /var/log/configflow-agent.log
    if [ -f /var/log/configflow-agent.log ]; then
        log_size=$(wc -c < /var/log/configflow-agent.log 2>/dev/null || echo 0)
        if [ "$log_size" -gt 10485760 ]; then
            mv -f /var/log/configflow-agent.log /var/log/configflow-agent.log.1
            : > /var/log/configflow-agent.log
            chmod 0644 /var/log/configflow-agent.log
        fi
    fi
}
SERVICEEOF
    chmod 755 /etc/init.d/configflow-agent
    rc-update add "$RECOVERY_SERVICE" boot
    rc-update add configflow-agent default
    rc-update -u
    rc-service configflow-agent start
    rc-service configflow-agent status
fi
echo "Go Agent 安装成功；Mihomo/MosDNS 核心继续由原服务管理器管理。"
