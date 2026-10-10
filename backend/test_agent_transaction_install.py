"""Installation/bootstrap regression tests for transactional Go Agent deployment."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from backend.agents.go_install_script import generate_go_agent_install_script
from backend.agents.install_script import generate_docker_agent_compose, generate_docker_agent_run

ROOT = Path(__file__).parents[1]


def generated(**kwargs):
    return generate_go_agent_install_script(
        server_url="https://config.test", agent_name="native-test", service_type="mihomo",
        binary_download_url="https://config.test/api/agents/download", **kwargs,
    )


def generated_config(script):
    return json.loads(script.split("<<'CONFIGFLOW_JSON_EOF'\n", 1)[1].split("\nCONFIGFLOW_JSON_EOF", 1)[0])


def test_native_install_has_explicit_lifecycle_and_boot_gate(tmp_path):
    script = generated()
    cfg = generated_config(script)
    assert cfg["service_manager"] == "__CONFIGFLOW_INIT__"
    assert cfg["service_unit"] == "mihomo"
    assert cfg["service_binary"] == "mihomo"
    assert cfg["deployment_method"] == "shell"
    # Recovery is a boot dependency; repeating it in ExecStartPre would undo an
    # active transaction each time the Agent deliberately starts the new core.
    assert "RemainAfterExit=yes" in script
    assert "Requires=configflow-recover-mihomo.service" in script
    assert 'rc_need="${rc_need:-} configflow-recover-mihomo"' in script
    assert "ExecStartPre=" not in script
    assert '-config "$CONFIG_FILE.new" -recover-and-exit' in script
    assert '"$AGENT_DIR/config.json"' in script  # legacy Shell identity migration
    assert 'mktemp /usr/local/bin/.configflow-agent.install.XXXXXX' in script
    assert 'mv "$BINARY_TMP" /usr/local/bin/configflow-agent' in script
    assert '/tmp/configflow-agent' not in script  # common noexec mount
    assert 'trap' in script
    path = tmp_path / "install.sh"
    path.write_text(script)
    result = subprocess.run(["sh", "-n", str(path)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_custom_restart_requires_explicit_lifecycle():
    with pytest.raises(ValueError, match="stop_command"):
        generated(restart_command="/custom/restart")
    cfg = generated_config(generated(
        restart_command="/custom/restart", stop_command="/custom/stop", start_command="/custom/start", status_command="/custom/status",
    ))
    assert cfg["service_manager"] == "command"
    assert cfg["stop_command"] == "/custom/stop"
    assert cfg["start_command"] == "/custom/start"


def test_installer_preserves_literal_json_and_shell_characters(tmp_path):
    # Quotes, braces, dollar signs and newlines must be data in the quoted
    # heredoc, never evaluated during installation.
    value = '/tmp/"config${HOME}{service_type}\\file\n.yaml'
    cfg = generated_config(generated(config_path=value))
    assert cfg["config_path"] == value


def test_docker_generated_volumes_persist_identity_and_transaction_roots():
    compose = yaml.safe_load(generate_docker_agent_compose("https://config.test", enable_mosdns=True))
    volumes = compose["services"]["agent"]["volumes"]
    assert "./agent_data/agent:/opt/configflow-agent" in volumes
    assert "./agent_data/mihomo:/etc/mihomo" in volumes
    assert "./agent_data/mosdns:/etc/mosdns" in volumes
    command = generate_docker_agent_run("https://config.test", enable_mosdns=True)
    assert "./agent_data/agent:/opt/configflow-agent" in command
    assert "./agent_data/mihomo:/etc/mihomo" in command
    assert "/root/.config/mihomo" not in command


def entrypoint_harness(tmp_path):
    source = (ROOT / "docker/docker-agent-entrypoint-service.sh").read_text()
    if sys.platform == "darwin":
        source = source.replace("sed -i ", "sed -i '' ")
    source = source.replace("/etc/", str(tmp_path / "etc") + "/")
    source = source.replace("/opt/configflow-agent", str(tmp_path / "agent"))
    source = source.replace("/usr/local/bin/configflow-agent", str(tmp_path / "agent-binary"))
    source = source.replace("/var/log/supervisor/", str(tmp_path / "logs") + "/")
    for service in ("mihomo", "mosdns"):
        directory = tmp_path / "etc" / service
        directory.mkdir(parents=True)
    supervisor = tmp_path / "etc/supervisor/conf.d"
    supervisor.mkdir(parents=True)
    for name in ("mihomo", "mosdns", "agent-mihomo", "agent-mosdns"):
        (supervisor / (name + ".conf")).write_text("autostart=false\n")
    binary = tmp_path / "agent-binary"
    binary.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$EVENTS"
if [ "${FAIL_RECOVERY:-}" = "yes" ]; then exit 42; fi
# Fail if a default config was created before BOTH recovery operations.
if [ -f "$CONFIG_ROOT/mihomo/config.yaml" ] || [ -f "$CONFIG_ROOT/mosdns/config.yaml" ]; then exit 43; fi
''')
    binary.chmod(0o755)
    entrypoint = tmp_path / "entrypoint.sh"
    entrypoint.write_text(source)
    env = dict(os.environ, SERVER_URL='https://config.test/"literal', ENABLE_MIHOMO="true", ENABLE_MOSDNS="true", EVENTS=str(tmp_path / "events"), CONFIG_ROOT=str(tmp_path / "etc"))
    env.pop("CONFIG_PATH", None)
    env.pop("SERVICE_TYPE", None)
    return entrypoint, env


def test_docker_boot_recovers_before_defaults_and_supervisor(tmp_path):
    entrypoint, env = entrypoint_harness(tmp_path)
    result = subprocess.run(["sh", str(entrypoint), "sh", "-c", 'echo supervisor >> "$EVENTS"'], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    events = (tmp_path / "events").read_text().splitlines()
    assert len(events) == 3
    assert "config-mihomo.json -recover-only" in events[0]
    assert "config-mosdns.json -recover-only" in events[1]
    assert events[2] == "supervisor"
    mihomo = (tmp_path / "etc/mihomo/config.yaml").read_text()
    assert "listen: 0.0.0.0:1053" in mihomo
    cfg = json.loads((tmp_path / "agent/config-mihomo.json").read_text())
    assert cfg["server_url"] == env["SERVER_URL"]
    assert cfg["service_manager"] == "supervisor"
    assert cfg["deployment_method"] == "docker"


def test_docker_recovery_failure_blocks_defaults_and_core_start(tmp_path):
    entrypoint, env = entrypoint_harness(tmp_path)
    env["FAIL_RECOVERY"] = "yes"
    result = subprocess.run(["sh", str(entrypoint), "sh", "-c", 'echo supervisor >> "$EVENTS"'], env=env, text=True, capture_output=True)
    assert result.returncode == 42
    assert not (tmp_path / "etc/mihomo/config.yaml").exists()
    assert not (tmp_path / "etc/mosdns/config.yaml").exists()
    assert "supervisor" not in (tmp_path / "events").read_text()
