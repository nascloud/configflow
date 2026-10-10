"""Generate the canonical Shell installer for the Go Agent."""
import json
import re
import shlex
from pathlib import Path


INSTALL_GO_SCRIPT_TEMPLATE = (Path(__file__).parent / "scripts" / "install-go.sh").read_text(encoding="utf-8")


def generate_go_agent_install_script(
    server_url: str,
    agent_name: str,
    service_type: str,
    agent_port: int = 8080,
    agent_ip: str = "",
    config_path: str = None,
    restart_command: str = None,
    binary_download_url: str = "",
    service_manager: str = "",
    service_unit: str = "",
    service_binary: str = "",
    stop_command: str = "",
    start_command: str = "",
    status_command: str = "",
    health_url: str = "",
    health_dns_address: str = "",
    health_dns_name: str = "",
    deployment_health_timeout: int = 30,
) -> str:
    """Generate an installer with explicit service lifecycle and boot recovery.

    Standard native installs detect systemd/OpenRC on the target. A custom
    restart command is not parsed to invent stop/start/status operations.
    """
    if service_type not in ("mihomo", "mosdns"):
        raise ValueError("Go Agent transactional deployment supports mihomo and mosdns")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", agent_name):
        raise ValueError("Invalid agent name: use letters, numbers, hyphens or underscores")
    if not 1 <= int(agent_port) <= 65535:
        raise ValueError("Invalid Agent port")
    unit = service_unit or service_type
    if not re.fullmatch(r"[A-Za-z0-9_.@-]+", unit):
        raise ValueError("Invalid service unit")
    if unit.endswith(".service"):
        unit = unit[:-8]
    if service_manager not in ("", "systemd", "openrc", "command"):
        raise ValueError("Shell installs require systemd, openrc or explicit command lifecycle")
    standard_restart = not restart_command or restart_command in (
        f"systemctl restart {unit}", f"systemctl restart {unit}.service", f"rc-service {unit} restart",
    )
    if not service_manager:
        service_manager = "__CONFIGFLOW_INIT__" if standard_restart else "command"
    if service_manager == "command" and not all((stop_command, start_command, status_command)):
        raise ValueError("Custom lifecycle requires stop_command, start_command and status_command; a restart command alone cannot support rollback")
    cfg = {
        "server_url": server_url,
        "agent_name": agent_name,
        "agent_host": "0.0.0.0",
        "agent_port": int(agent_port),
        "agent_ip": agent_ip,
        "service_type": service_type,
        "deployment_method": "shell",
        "service_name": service_type,
        "config_path": config_path or f"/etc/{service_type}/config.yaml",
        "restart_command": "__CONFIGFLOW_RESTART__" if standard_restart else restart_command,
        "service_manager": service_manager,
        "service_unit": unit,
        "service_binary": service_binary or service_type,
        "stop_command": stop_command,
        "start_command": start_command,
        "status_command": status_command,
        "health_url": health_url,
        "health_dns_address": health_dns_address,
        "health_dns_name": health_dns_name,
        "deployment_health_timeout": int(deployment_health_timeout),
        "heartbeat_interval": 30,
        "agent_id": "__CONFIGFLOW_AGENT_ID__",
        "token": "__CONFIGFLOW_TOKEN__",
    }
    replacements = {
        "service_type": service_type,
        "service_unit": unit,
        "agent_name": agent_name,
        "config_json": json.dumps(cfg, ensure_ascii=True, indent=2),
        "binary_download_url_shell": shlex.quote(binary_download_url.rstrip("/")),
    }
    # Single pass: values containing braces must remain literal, especially JSON
    # commands and URLs. The config heredoc is quoted to disable shell expansion.
    return re.sub(r"\{(" + "|".join(replacements) + r")\}", lambda match: replacements[match.group(1)], INSTALL_GO_SCRIPT_TEMPLATE)
