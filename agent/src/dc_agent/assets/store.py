"""Named code files, immutable versions, legacy import, and recoverable activation."""

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from ..config import Settings
from ..domain import AgentError, Task, digest
from ..infrastructure.files import filesystem_path, write_json
from ..infrastructure.locking import publication_lock
from ..infrastructure.storage import Repository
from .layout import AssetLayout


class ArtifactStore:
    def __init__(self, settings: Settings, repo: Repository):
        self.root = settings.resolved_code()
        self.runtime = settings.resolved_runtime()
        self.repo = repo
        self.lock = self.root / ".publication.lock"

    def _path(self, relative: str | Path, *, legacy: bool = False) -> Path:
        root = self.runtime if legacy else self.root
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or path == root:
            raise AgentError("ARTIFACT_CORRUPT", "Asset path escapes its registered root")
        return filesystem_path(path)

    def content_path(self, artifact: dict, *, historical: bool = False) -> Path:
        if artifact.get("layout_version", 1) == 1:
            return self._path(artifact["content_path"], legacy=True)
        if artifact.get("layout_version") != 2 or artifact.get("content_root") != "code":
            raise AgentError("ARTIFACT_CORRUPT", "Unknown asset layout")
        return self._path(artifact["version_path"] if historical else artifact["content_path"])

    @staticmethod
    def _read(path: Path, expected: str) -> bytes:
        if not path.is_file():
            raise AgentError("ARTIFACT_CORRUPT", "Published artifact file is missing")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise AgentError("ARTIFACT_CORRUPT", "Published artifact checksum mismatch")
        return data

    def _owner_path(self, layout: AssetLayout) -> Path:
        return self._path(Path(".owners") / f"{digest(layout.path_key)}.json")

    def _owner(self, layout: AssetLayout) -> dict | None:
        path = self._owner_path(layout)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def _bind(self):
        path = self._path(".runtime.json")
        if path.exists():
            if json.loads(path.read_text(encoding="utf-8"))["runtime_root"] != str(self.runtime):
                raise AgentError(
                    "ASSET_ROOT_CONFLICT", "A code directory must belong to one task database"
                )
        else:
            write_json(path, {"runtime_root": str(self.runtime)})

    def _recover(self):
        self._bind()
        journal = self._path(".publication.json")
        if not journal.exists():
            return
        operation = json.loads(journal.read_text(encoding="utf-8"))
        new, old = operation["new"], operation["old"]
        layout = AssetLayout(new["pageTitle"], new["fileType"])
        if new["content_path"] != layout.current.as_posix():
            raise AgentError("ARTIFACT_CORRUPT", "Invalid publication journal path")
        committed = self.repo.current(new["source_key"])
        chosen = new if committed == new else old
        if chosen:
            data = self._read(self.content_path(chosen, historical=True), chosen["content_hash"])
            self._write(self._path(chosen["content_path"]), data)
            write_json(self._owner_path(layout), chosen)
        else:
            current = self._path(new["content_path"])
            if current.exists():
                self._read(current, new["content_hash"])
                current.unlink()
            self._owner_path(layout).unlink(missing_ok=True)
        journal.unlink()

    @staticmethod
    def _write(path: Path, data: bytes):
        path = filesystem_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{uuid4().hex}.tmp")
        try:
            temp.write_bytes(data)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)

    def _available(self, task: Task, layout: AssetLayout):
        owner = self._owner(layout)
        if owner and (
            owner["source_key"] != task.source_key
            or owner["pageTitle"] != task.pageTitle
            or owner["fileType"] != task.fileType
        ):
            raise AgentError(
                "ARTIFACT_PATH_COLLISION", "Different sources map to the same asset filename"
            )
        if not owner and self._path(layout.current).exists():
            raise AgentError(
                "ARTIFACT_UNREGISTERED", "Named asset exists without validated registration"
            )

    def ensure_available(self, task: Task):
        layout = AssetLayout(task.pageTitle, task.fileType)
        with publication_lock(self.lock):
            self._recover()
            self._available(task, layout)

    def verify_historical(self, task: Task, artifact: dict):
        self._identity(task, artifact)
        with publication_lock(self.lock):
            self._recover()
            self._read(self.content_path(artifact, historical=True), artifact["content_hash"])

    @staticmethod
    def _identity(task: Task, artifact: dict):
        if artifact.get("pageTitle") != task.pageTitle or artifact.get("fileType") != task.fileType:
            raise AgentError("ARTIFACT_CORRUPT", "Registered artifact identity mismatch")

    def lookup(self, task: Task, token: str | None = None) -> dict | None:
        layout = AssetLayout(task.pageTitle, task.fileType)
        with publication_lock(self.lock):
            self._recover()
            self._available(task, layout)
            artifact = self.repo.current(task.source_key)
            if not artifact:
                return None
            self._identity(task, artifact)
            if artifact.get("layout_version", 1) == 1:
                data = self._read(self.content_path(artifact), artifact["content_hash"])
                own_token = token is None
                token = token or self.repo.claim(task.source_key, 30)
                if token is None:
                    raise AgentError("ASSET_BUSY", "Legacy asset migration is busy", retryable=True)
                try:
                    artifact = self._publish(
                        task,
                        token,
                        data,
                        {
                            **artifact,
                            "legacy_content_path": artifact["content_path"],
                            "migrated_from_layout": 1,
                        },
                        layout,
                    )
                finally:
                    if own_token:
                        self.repo.release(task.source_key, token)
            if artifact["content_path"] != layout.current.as_posix():
                raise AgentError("ARTIFACT_CORRUPT", "Registered business filename mismatch")
            self._read(self.content_path(artifact, historical=True), artifact["content_hash"])
            self._read(self.content_path(artifact), artifact["content_hash"])
            owner = self._owner(layout)
            if owner != artifact:
                raise AgentError("ARTIFACT_CORRUPT", "Asset registration and current file disagree")
            return artifact

    def save_candidate(self, task: Task, task_id: str, attempt: int, content: str) -> Path:
        layout = AssetLayout(task.pageTitle, task.fileType)
        path = self._path(layout.directory / ".staging" / task_id / str(attempt) / layout.filename)
        self._write(path, content.encode("utf-8"))
        return path

    def publish(self, task: Task, token: str, content: str, metadata: dict) -> dict:
        layout = AssetLayout(task.pageTitle, task.fileType)
        with publication_lock(self.lock):
            self._recover()
            self._available(task, layout)
            return self._publish(task, token, content.encode("utf-8"), metadata, layout)

    def _publish(
        self, task: Task, token: str, data: bytes, metadata: dict, layout: AssetLayout
    ) -> dict:
        version = metadata["version"]
        version_path = layout.directory / ".versions" / task.source_key / version / layout.filename
        artifact = {
            **metadata,
            "layout_version": 2,
            "content_root": "code",
            "source_key": task.source_key,
            "pageTitle": task.pageTitle,
            "fileType": task.fileType,
            "display_name": layout.filename,
            "content_path": layout.current.as_posix(),
            "version_path": version_path.as_posix(),
            "content_hash": hashlib.sha256(data).hexdigest(),
        }
        destination = self._path(version_path)
        if destination.exists():
            self._read(destination, artifact["content_hash"])
        else:
            self._write(destination, data)
        # New manifests are separate from V1 historical evidence, even for an imported version.
        manifest = self._path(
            Path("v2_manifests") / task.source_key / f"{version}.json", legacy=True
        )
        write_json(manifest, artifact)
        journal = self._path(".publication.json")
        old = self._owner(layout)

        def replace_current():
            write_json(journal, {"new": artifact, "old": old})
            self._write(self._path(layout.current), data)
            write_json(self._owner_path(layout), artifact)

        try:
            # Repository obtains the SQLite write lock and verifies the fencing token
            # before this callback can touch the current business file.
            self.repo.activate(task.source_key, token, artifact, before_commit=replace_current)
        except Exception:
            self._recover()
            raise
        journal.unlink(missing_ok=True)
        return artifact
