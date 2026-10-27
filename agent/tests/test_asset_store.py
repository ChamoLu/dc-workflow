import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from dc_agent.assets.layout import AssetLayout
from dc_agent.assets.store import ArtifactStore
from dc_agent.config import Settings
from dc_agent.domain import AgentError, Task
from dc_agent.infrastructure.locking import publication_lock
from dc_agent.infrastructure.storage import Repository


def task_for(file_type="xlsx", title="甲市\\统计信息"):
    return Task(
        pageTitle=title,
        fileType=file_type,
        fileMd5="excel",
        markdownMD5="markdown",
        GZKJG=[{"rule_indicator_name": "甲"}],
    )


@pytest.fixture
def store(tmp_path):
    settings = Settings(_env_file=None, runtime_dir=tmp_path / "runtime")
    repo = Repository(settings.resolved_runtime())
    try:
        yield ArtifactStore(settings, repo)
    finally:
        repo.close()


def publish(store, task, content="first"):
    token = store.repo.claim(task.source_key, 60)
    assert token
    try:
        return store.publish(task, token, content, {"version": str(uuid4()), "contract": "test"})
    finally:
        store.repo.release(task.source_key, token)


@pytest.mark.parametrize(
    "kind,bucket,extension",
    [
        ("xls", "excel", ".py"),
        ("xlsx", "excel", ".py"),
        ("csv", "excel", ".py"),
        ("html", "html", ".py"),
        ("htm", "html", ".py"),
        ("shtml", "html", ".py"),
        ("pdf", "md", ".md"),
        ("png", "md", ".md"),
        ("doc", "md", ".md"),
        ("docx", "md", ".md"),
    ],
)
def test_real_named_files_keep_types_separate(store, kind, bucket, extension):
    task = task_for(kind)
    artifact = publish(store, task)
    assert artifact["content_path"] == f"{bucket}/{kind}/甲市_统计信息{extension}"
    assert store.content_path(artifact).read_text(encoding="utf-8") == "first"
    assert store.content_path(artifact, historical=True).read_bytes() == b"first"
    assert store.lookup(task) == artifact
    if kind == "xlsx":
        other = publish(store, task_for("xls"), "second")
        assert store.content_path(artifact).read_bytes() == b"first"
        assert store.content_path(other).read_bytes() == b"second"


@pytest.mark.parametrize("other", ["甲市_统计信息", "SITE", "site"])
def test_normalized_path_collision_never_overwrites(store, other):
    title = "甲市\\统计信息" if other == "甲市_统计信息" else other.swapcase()
    first = publish(store, task_for(title=title))
    with pytest.raises(AgentError) as error:
        publish(store, task_for(title=other), "bad replacement")
    assert error.value.code == "ARTIFACT_PATH_COLLISION"
    assert store.content_path(first).read_bytes() == b"first"


@pytest.mark.parametrize(
    "title", ["../escape", "C:drive", "CON", "A?B", "title.", "title ", "长" * 100]
)
def test_invalid_business_name_is_not_silently_changed(title):
    with pytest.raises(AgentError) as error:
        AssetLayout(title, "xlsx")
    assert error.value.code == "INVALID_ARTIFACT_NAME"


def test_legacy_import_preserves_bytes_version_and_historical_manifest(store):
    task = task_for()
    version = str(uuid4())
    legacy = store.runtime / "artifacts" / task.source_key / version / "parser.py"
    legacy.parent.mkdir(parents=True)
    source = b"# original\r\ndef main(params):\r\n    return {}\r\n"
    legacy.write_bytes(source)
    artifact = {
        "version": version,
        "pageTitle": task.pageTitle,
        "fileType": task.fileType,
        "content_path": str(legacy.relative_to(store.runtime)),
        "content_hash": hashlib.sha256(source).hexdigest(),
        "validation_scope": "v1-proof",
    }
    manifest = legacy.with_name("manifest.json")
    manifest.write_text(json.dumps(artifact), encoding="utf-8")
    original_manifest = manifest.read_bytes()
    token = store.repo.claim(task.source_key, 60)
    store.repo.activate(task.source_key, token, artifact)
    store.repo.release(task.source_key, token)
    imported = store.lookup(task)
    assert imported["layout_version"] == 2
    assert imported["version"] == version
    assert imported["content_hash"] == artifact["content_hash"]
    assert store.content_path(imported).read_bytes() == source
    assert store.content_path(imported, historical=True).read_bytes() == source
    assert manifest.read_bytes() == original_manifest
    assert legacy.read_bytes() == source
    assert store.lookup(task) == imported


def test_lost_lease_cannot_replace_the_current_file(store):
    task = task_for()
    original = publish(store, task)
    with pytest.raises(AgentError) as error:
        store.publish(task, "expired-token", "bad replacement", {"version": str(uuid4())})
    assert error.value.code == "LEASE_LOST"
    assert store.lookup(task) == original
    assert store.content_path(original).read_bytes() == b"first"


@pytest.mark.parametrize("committed", [False, True])
def test_publication_crash_recovers_the_committed_version(store, monkeypatch, committed):
    class Crash(BaseException):
        pass

    task = task_for()
    original = publish(store, task)
    activate = store.repo.activate

    def interrupted(key, token, artifact, *, before_commit=None):
        if committed:
            activate(key, token, artifact, before_commit=before_commit)
            raise Crash()

        def fail_after_file_write():
            before_commit()
            raise Crash()

        activate(key, token, artifact, before_commit=fail_after_file_write)

    monkeypatch.setattr(store.repo, "activate", interrupted)
    token = store.repo.claim(task.source_key, 60)
    try:
        with pytest.raises(Crash):
            store.publish(task, token, "second", {"version": str(uuid4())})
    finally:
        store.repo.release(task.source_key, token)
    assert (store.root / ".publication.json").exists()
    monkeypatch.setattr(store.repo, "activate", activate)
    current = store.lookup(task)
    assert store.content_path(current).read_text(encoding="utf-8") == (
        "second" if committed else "first"
    )
    if not committed:
        assert current == original
    assert not (store.root / ".publication.json").exists()


def test_concurrent_colliding_sources_have_one_owner(store):
    def attempt(title):
        try:
            return publish(store, task_for(title=title))
        except AgentError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, ["同级\\指标", "同级_指标"]))
    assert sum(isinstance(value, dict) for value in outcomes) == 1
    assert "ARTIFACT_PATH_COLLISION" in outcomes


def test_unregistered_or_tampered_files_are_not_used(store):
    task = task_for()
    store.ensure_available(task)
    path = store.root / AssetLayout(task.pageTitle, task.fileType).current
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("unreviewed", encoding="utf-8")
    with pytest.raises(AgentError) as error:
        store.lookup(task)
    assert error.value.code == "ARTIFACT_UNREGISTERED"


def test_code_root_is_bound_to_one_database(store, tmp_path):
    store.ensure_available(task_for())
    other_settings = Settings(
        _env_file=None, runtime_dir=tmp_path / "other-runtime", code_dir=store.root
    )
    other_repo = Repository(other_settings.resolved_runtime())
    try:
        with pytest.raises(AgentError) as error:
            ArtifactStore(other_settings, other_repo).ensure_available(task_for())
        assert error.value.code == "ASSET_ROOT_CONFLICT"
    finally:
        other_repo.close()


def test_publication_lock_coordinates_separate_processes(store):
    script = """
import sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from dc_agent.domain import AgentError
from dc_agent.infrastructure.locking import publication_lock
try:
    with publication_lock(Path(sys.argv[2]), timeout=0.2):
        pass
except AgentError as exc:
    raise SystemExit(7 if exc.code == 'ASSET_BUSY' else 8)
"""
    from pathlib import Path

    command = [
        sys.executable,
        "-I",
        "-c",
        script,
        str(Path(__file__).resolve().parents[1] / "src"),
        str(store.lock),
    ]
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
    }
    with publication_lock(store.lock):
        blocked = subprocess.run(command, env=env, capture_output=True, timeout=10)
        assert blocked.returncode == 7, blocked.stderr
    released = subprocess.run(command, env=env, capture_output=True, timeout=10)
    assert released.returncode == 0, released.stderr
