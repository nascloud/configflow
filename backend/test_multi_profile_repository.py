import json
import threading
from pathlib import Path

import pytest

from backend.common.config_repository import ProfileRepository, ProfileValidationError


def test_repository_creates_isolated_default_profile(tmp_path):
    repository = ProfileRepository(tmp_path)

    assert repository.list_profiles()[0]["id"] == "default"
    assert repository.profile_dir("default") == tmp_path / "profiles" / "default"
    assert (tmp_path / "profiles" / "default" / "config.json").exists()
    assert (tmp_path / "profiles" / "default" / "subscribes").is_dir()
    assert (tmp_path / "profiles" / "default" / "providers").is_dir()
    assert (tmp_path / "profiles" / "default" / "rules").is_dir()
    assert (tmp_path / "profiles" / "default" / "generated").is_dir()


def test_profile_id_cannot_escape_profiles_directory(tmp_path):
    repository = ProfileRepository(tmp_path)

    for profile_id in ("../outside", "..", "a/b", "a\\b", "C:"):
        with pytest.raises(ProfileValidationError):
            repository.profile_dir(profile_id)
        with pytest.raises(ProfileValidationError):
            repository.get_profile(profile_id)

    for relative_path in ("../outside", "", None):
        with pytest.raises(ProfileValidationError):
            repository.profile_path("default", relative_path)


def test_legacy_config_migrates_without_data_loss_and_is_idempotent(tmp_path):
    legacy = {
        "subscriptions": [{"id": "sub-1", "name": "legacy"}],
        "nodes": [{"id": "node-1", "name": "node"}],
        "rule_configs": [{"id": "rule-1", "value": "example.com"}],
        "proxy_groups": [],
        "rule_library": [],
        "system_config": {"server_domain": "https://config.example", "config_token": "keep"},
        "agents": [{"id": "agent-1", "name": "legacy-agent"}],
        "backup": {"webdav_url": "https://backup.example"},
    }
    (tmp_path / "config.json").write_text(json.dumps(legacy), encoding="utf-8")
    (tmp_path / "subscribes").mkdir()
    (tmp_path / "subscribes" / "sub-1.json").write_text("{\"nodes\": []}", encoding="utf-8")
    (tmp_path / "providers").mkdir()
    (tmp_path / "providers" / "agg.yaml").write_text("proxies: []", encoding="utf-8")
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "legacy.list").write_text("DOMAIN,example.com", encoding="utf-8")

    repository = ProfileRepository(tmp_path)
    first_profile = repository.get_profile("default")
    first_system = repository.get_system()

    assert first_profile["subscriptions"] == legacy["subscriptions"]
    assert first_profile["rule_configs"] == legacy["rule_configs"]
    assert first_system["agents"] == [{**legacy["agents"][0], "profile_id": "default"}]
    assert first_system["system_config"] == legacy["system_config"]
    assert first_system["backup"] == legacy["backup"]
    assert (tmp_path / "profiles" / "default" / "subscribes" / "sub-1.json").exists()
    assert (tmp_path / "profiles" / "default" / "providers" / "agg.yaml").exists()
    assert (tmp_path / "profiles" / "default" / "rules" / "legacy.list").exists()

    second = ProfileRepository(tmp_path)
    assert second.get_profile("default") == first_profile
    assert second.get_system() == first_system
    assert len(list((tmp_path / "migrations").glob("*/config.json"))) == 1


def test_atomic_profile_save_keeps_previous_file_when_replace_fails(tmp_path, monkeypatch):
    repository = ProfileRepository(tmp_path)
    repository.save_profile("default", {"subscriptions": [{"id": "old"}]})
    config_path = repository.profile_dir("default") / "config.json"

    def fail_replace(source, target):
        raise OSError("replace failed")

    monkeypatch.setattr("backend.common.config_repository.os.replace", fail_replace)
    with pytest.raises(OSError):
        repository.save_profile("default", {"subscriptions": [{"id": "new"}]})

    assert json.loads(config_path.read_text(encoding="utf-8"))["subscriptions"] == [{"id": "old"}]


def test_partial_system_metadata_save_keeps_other_fields(tmp_path):
    repository = ProfileRepository(tmp_path)
    repository.save_profile("default", {"system_config": {"config_token": "token"}})
    repository.save_profile("default", {"system_config": {"server_domain": "http://configflow.test"}})

    assert repository.get_system()["system_config"] == {
        "server_domain": "http://configflow.test",
        "github_proxy_domain": "",
        "config_token": "token",
    }


def test_concurrent_profile_saves_do_not_cross_contaminate(tmp_path):
    repository = ProfileRepository(tmp_path)
    repository.create_profile({"id": "alpha", "name": "Alpha"})
    repository.create_profile({"id": "beta", "name": "Beta"})
    failures = []

    def save(profile_id, value):
        try:
            for _ in range(20):
                repository.save_profile(profile_id, {"subscriptions": [{"id": value}]})
        except Exception as exc:  # pragma: no cover - assertion below reports it
            failures.append(exc)

    threads = [
        threading.Thread(target=save, args=("alpha", "alpha")),
        threading.Thread(target=save, args=("beta", "beta")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not failures
    assert repository.get_profile("alpha")["subscriptions"] == [{"id": "alpha"}]
    assert repository.get_profile("beta")["subscriptions"] == [{"id": "beta"}]


def test_concurrent_profile_creation_preserves_system_index(tmp_path, monkeypatch):
    repository = ProfileRepository(tmp_path)
    original_write_system = repository._write_system
    barrier = threading.Barrier(2)

    def delayed_write_system(system):
        barrier.wait(timeout=5)
        original_write_system(system)

    monkeypatch.setattr(repository, "_write_system", delayed_write_system)
    failures = []

    def create(profile_id):
        try:
            repository.create_profile({"id": profile_id})
        except Exception as exc:
            failures.append(exc)

    workers = [threading.Thread(target=create, args=(profile_id,)) for profile_id in ("alpha", "beta")]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert not failures
    assert {profile["id"] for profile in repository.list_profiles()} >= {"default", "alpha", "beta"}


def test_profile_transaction_preserves_concurrent_list_updates(tmp_path):
    repository = ProfileRepository(tmp_path)
    failures = []

    def append_subscription(index):
        try:
            repository.update_profile_transaction(
                "default",
                lambda profile: profile["subscriptions"].append({"id": f"sub-{index}"}),
            )
        except Exception as exc:  # pragma: no cover - assertion below reports it
            failures.append(exc)

    workers = [threading.Thread(target=append_subscription, args=(index,)) for index in range(12)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert not failures
    assert {item["id"] for item in repository.get_profile("default")["subscriptions"]} == {
        f"sub-{index}" for index in range(12)
    }


def test_save_profile_rolls_back_profile_when_system_commit_fails(tmp_path, monkeypatch):
    repository = ProfileRepository(tmp_path)
    repository.save_profile("default", {"subscriptions": [{"id": "old"}]})
    system_path = repository.system_file
    original_write_json = repository._write_json

    def fail_system_commit(path, data):
        if path == system_path:
            raise OSError("injected system commit failure")
        return original_write_json(path, data)

    monkeypatch.setattr(repository, "_write_json", fail_system_commit)
    with pytest.raises(OSError, match="injected system commit failure"):
        repository.save_profile("default", {"subscriptions": [{"id": "new"}]})

    assert repository.get_profile("default")["subscriptions"] == [{"id": "old"}]


def test_save_profile_rolls_back_system_when_commit_fails_after_replace(tmp_path, monkeypatch):
    repository = ProfileRepository(tmp_path)
    before = repository.get_system()
    system_path = repository.system_file
    original_write_json = repository._write_json

    def write_then_fail(path, data):
        if path == system_path:
            original_write_json(path, data)
            raise OSError("injected post-replace failure")
        return original_write_json(path, data)

    monkeypatch.setattr(repository, "_write_json", write_then_fail)
    with pytest.raises(OSError, match="injected post-replace failure"):
        repository.save_profile("default", {"subscriptions": [{"id": "new"}]})

    assert repository.get_system() == before


def test_create_profile_cleans_directory_when_system_commit_fails(tmp_path, monkeypatch):
    repository = ProfileRepository(tmp_path)
    system_path = repository.system_file
    original_write_json = repository._write_json

    def fail_system_commit(path, data):
        if path == system_path:
            raise OSError("injected system commit failure")
        return original_write_json(path, data)

    monkeypatch.setattr(repository, "_write_json", fail_system_commit)
    with pytest.raises(OSError, match="injected system commit failure"):
        repository.create_profile({"id": "orphan"})

    assert not repository.profile_dir("orphan").exists()
    assert "orphan" not in {profile["id"] for profile in repository.list_profiles()}


def test_create_profile_restores_preexisting_orphan_directory_on_commit_failure(tmp_path, monkeypatch):
    repository = ProfileRepository(tmp_path)
    orphan_dir = repository.profile_dir("orphan")
    orphan_dir.mkdir()
    original_config = b'{"unregistered": "original"}\n'
    (orphan_dir / "config.json").write_bytes(original_config)
    (orphan_dir / "important.txt").write_text("keep me", encoding="utf-8")
    original_write_json = repository._write_json

    def fail_system_commit(path, data):
        if path == repository.system_file:
            raise OSError("system commit failed")
        return original_write_json(path, data)

    monkeypatch.setattr(repository, "_write_json", fail_system_commit)
    with pytest.raises(OSError, match="system commit failed"):
        repository.create_profile({"id": "orphan"})

    assert (orphan_dir / "config.json").read_bytes() == original_config
    assert (orphan_dir / "important.txt").read_text(encoding="utf-8") == "keep me"
    assert sorted(path.name for path in orphan_dir.iterdir()) == ["config.json", "important.txt"]
    assert "orphan" not in {profile["id"] for profile in repository.list_profiles()}


def test_delete_profile_serializes_with_in_flight_profile_write(tmp_path, monkeypatch):
    repository = ProfileRepository(tmp_path)
    repository.create_profile({"id": "deletable"})
    write_started = threading.Event()
    allow_write = threading.Event()
    original_write_json = repository._write_json

    def blocked_profile_write(path, data):
        if path == repository.profile_dir("deletable") / "config.json":
            write_started.set()
            assert allow_write.wait(timeout=5)
        return original_write_json(path, data)

    monkeypatch.setattr(repository, "_write_json", blocked_profile_write)
    write_error = []

    def write_profile():
        try:
            repository.save_profile("deletable", {"subscriptions": [{"id": "write"}]})
        except Exception as exc:  # pragma: no cover - assertion below reports it
            write_error.append(exc)

    writer = threading.Thread(target=write_profile)
    writer.start()
    assert write_started.wait(timeout=5)

    deleter = threading.Thread(target=repository.delete_profile, args=("deletable",))
    deleter.start()
    assert deleter.is_alive()
    allow_write.set()
    writer.join(timeout=5)
    deleter.join(timeout=5)

    assert not write_error
    assert not deleter.is_alive()
    assert not repository.profile_dir("deletable").exists()
    assert "deletable" not in {profile["id"] for profile in repository.list_profiles()}


def test_update_profile_transaction_rolls_back_profile_when_system_commit_fails(tmp_path, monkeypatch):
    repository = ProfileRepository(tmp_path)
    repository.save_profile("default", {"subscriptions": [{"id": "old"}]})
    system_path = repository.system_file
    original_write_json = repository._write_json

    def fail_system_commit(path, data):
        if path == system_path:
            raise OSError("system commit failed")
        return original_write_json(path, data)

    monkeypatch.setattr(repository, "_write_json", fail_system_commit)
    with pytest.raises(OSError, match="system commit failed"):
        repository.update_profile_transaction(
            "default", lambda profile: profile.update({"subscriptions": [{"id": "new"}]}),
        )

    assert repository.get_profile("default")["subscriptions"] == [{"id": "old"}]


def test_delete_profile_keeps_tombstone_when_post_commit_cleanup_fails(tmp_path, monkeypatch):
    repository = ProfileRepository(tmp_path)
    repository.create_profile({"id": "keep", "name": "Keep"})
    (repository.profile_dir("keep") / "important.txt").write_text("keep me", encoding="utf-8")

    def fail_rmtree(path):
        raise OSError("directory removal failed")

    monkeypatch.setattr("backend.common.config_repository.shutil.rmtree", fail_rmtree)
    repository.delete_profile("keep")

    assert "keep" not in {profile["id"] for profile in repository.list_profiles()}
    assert not repository.profile_dir("keep").exists()
    tombstones = list(repository.profiles_dir.glob(".keep.tombstone-*"))
    assert len(tombstones) == 1
    assert (tombstones[0] / "important.txt").read_text(encoding="utf-8") == "keep me"


def test_delete_profile_restores_directory_when_system_commit_fails(tmp_path, monkeypatch):
    repository = ProfileRepository(tmp_path)
    repository.create_profile({"id": "keep", "name": "Keep"})
    marker = repository.profile_dir("keep") / "important.txt"
    marker.write_text("original", encoding="utf-8")
    before = repository.get_system()
    original_write_json = repository._write_json

    def fail_system_commit(path, data):
        if path == repository.system_file:
            raise OSError("system commit failed")
        return original_write_json(path, data)

    monkeypatch.setattr(repository, "_write_json", fail_system_commit)
    with pytest.raises(OSError, match="system commit failed"):
        repository.delete_profile("keep")

    assert repository.get_system() == before
    assert marker.read_text(encoding="utf-8") == "original"
    assert not list(repository.profiles_dir.glob(".keep.tombstone-*"))


def test_independent_repositories_merge_locked_field_and_list_updates(tmp_path):
    first = ProfileRepository(tmp_path)
    second = ProfileRepository(tmp_path)
    first.save_profile("default", {"mihomo": {"custom_config": "before"}})
    failures = []

    def update_field():
        try:
            first.update_profile_fields("default", {"mihomo": {"custom_config": "field"}})
        except Exception as exc:
            failures.append(exc)

    def append_item():
        try:
            second.update_profile_transaction(
                "default", lambda profile: profile["subscriptions"].append({"id": "concurrent"}),
            )
        except Exception as exc:
            failures.append(exc)

    threads = [threading.Thread(target=update_field), threading.Thread(target=append_item)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not failures
    result = first.get_profile("default")
    assert result["mihomo"]["custom_config"] == "field"
    assert result["subscriptions"] == [{"id": "concurrent"}]


def test_update_profile_fields_three_way_merges_stale_list_appends(tmp_path):
    first = ProfileRepository(tmp_path)
    second = ProfileRepository(tmp_path)
    first.save_profile("default", {"subscriptions": [{"id": "existing"}]})

    first_baseline = first.get_profile("default")
    second_baseline = second.get_profile("default")
    first_value = first_baseline["subscriptions"] + [{"id": "from-first"}]
    second_value = second_baseline["subscriptions"] + [{"id": "from-second"}]

    first.update_profile_fields(
        "default",
        {"subscriptions": first_value},
        baseline={"subscriptions": first_baseline["subscriptions"]},
    )
    second.update_profile_fields(
        "default",
        {"subscriptions": second_value},
        baseline={"subscriptions": second_baseline["subscriptions"]},
    )

    assert first.get_profile("default")["subscriptions"] == [
        {"id": "existing"},
        {"id": "from-first"},
        {"id": "from-second"},
    ]
