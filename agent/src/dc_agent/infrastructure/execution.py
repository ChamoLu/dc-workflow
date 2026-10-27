"""Candidate execution adapters.

LocalProcessRunner is explicitly unsafe: a subprocess is not a security sandbox.
Use only reviewed code locally; use the restricted Docker adapter for automation.
Neither adapter inherits model/API credentials into the candidate environment.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from ..domain import AgentError

MAX_RESULT_BYTES = 8 * 1024 * 1024


class RunnerError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _execution_timeout(timeout: float, deadline: float | None) -> float:
    if deadline is None:
        return timeout
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        # The orchestration layer retains earlier validated candidates on a
        # task-budget interruption. A runner-specific error would bypass that path.
        raise AgentError("BUDGET_EXCEEDED", "Task deadline exceeded before candidate execution")
    return min(timeout, remaining)


def _environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
    }


def _prepare(
    root: Path,
    code: str,
    params: dict,
    source_bytes: bytes,
    source_id: str,
    entrypoint: str = "main",
):
    if entrypoint not in {"main", "_dc_validate"}:
        raise RunnerError("invalid_input", "Unsupported validation entrypoint")
    if not source_id or any(char in source_id for char in "/\\?#"):
        raise RunnerError("invalid_source", "A single source identifier is required")
    input_dir, output_dir = root / "input", root / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    # Container uid has access only to its unique input/output directories.
    input_dir.chmod(0o755)
    output_dir.chmod(0o777)
    (input_dir / "candidate.py").write_text(code, encoding="utf-8")
    try:
        request = json.dumps(
            {"params": params, "source_id": source_id, "entrypoint": entrypoint}, allow_nan=False
        )
    except (ValueError, TypeError) as exc:
        raise RunnerError("invalid_input", "Parameters must be strict JSON") from exc
    (input_dir / "request.json").write_text(request, encoding="utf-8")
    (input_dir / "source.bin").write_bytes(source_bytes)
    shutil.copyfile(Path(__file__).with_name("_runner.py"), input_dir / "runner.py")
    for item in input_dir.iterdir():
        item.chmod(0o644)
    return input_dir, output_dir


def _read_result(output_dir: Path) -> dict:
    result_file = output_dir / "result.json"
    if not result_file.is_file() or result_file.stat().st_size > MAX_RESULT_BYTES:
        raise RunnerError("invalid_result", "Missing or oversized execution result")
    try:

        def reject_constant(value):
            raise ValueError(value)

        payload = json.loads(
            result_file.read_text(encoding="utf-8"), parse_constant=reject_constant
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
            raise ValueError("Invalid result envelope")
        if not payload["ok"]:
            raise RunnerError(
                payload.get("code", "execution_error"), payload.get("message", "Execution failed")
            )
        result = payload["result"]
        if not isinstance(result, dict):
            raise ValueError("main(params) must return a dictionary")
        return result
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise RunnerError("invalid_result", "Result is not a strict JSON dictionary") from exc


def _stop_process(process: subprocess.Popen):
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        finally:
            # taskkill can be restricted by the host; terminate our own child
            # directly as a fallback so it cannot retain the temporary cwd.
            if process.poll() is None:
                process.kill()
    else:
        os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=10)


class LocalProcessRunner:
    """Run reviewed code in a separate process, NOT a sandbox."""

    def __init__(self, allow_unsafe: bool = False, timeout: float = 30):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive")
        self.allow_unsafe = allow_unsafe
        self.timeout = timeout
        self.deadline: float | None = None

    def run(
        self,
        code: str,
        params: dict,
        source_bytes: bytes,
        source_id: str,
        *,
        entrypoint: str = "main",
    ) -> dict:
        _execution_timeout(self.timeout, self.deadline)
        if not self.allow_unsafe:
            raise RunnerError(
                "unsafe_execution_disabled", "Local execution requires explicit allow_unsafe=True"
            )
        with tempfile.TemporaryDirectory(prefix="dc-agent-run-") as temp:
            root = Path(temp)
            input_dir, output_dir = _prepare(
                root, code, params, source_bytes, source_id, entrypoint
            )
            wait_timeout = _execution_timeout(self.timeout, self.deadline)
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    str(input_dir / "runner.py"),
                    str(input_dir),
                    str(output_dir),
                ],
                cwd=root,
                env=_environment(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=os.name != "nt",
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            )
            try:
                process.wait(timeout=wait_timeout)
            except subprocess.TimeoutExpired as exc:
                raise RunnerError(
                    "execution_timeout", "Candidate execution exceeded its time limit"
                ) from exc
            finally:
                _stop_process(process)
            return _read_result(output_dir)


class DockerRunner:
    """Restricted Docker CLI execution; the image must be built separately."""

    def __init__(self, image: str = "dc-agent-runner:local", timeout: float = 30):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive")
        self.image = image
        self.timeout = timeout
        self.deadline: float | None = None

    def run(
        self,
        code: str,
        params: dict,
        source_bytes: bytes,
        source_id: str,
        *,
        entrypoint: str = "main",
    ) -> dict:
        _execution_timeout(self.timeout, self.deadline)
        docker = shutil.which("docker")
        if docker is None:
            raise RunnerError("runner_unavailable", "Docker CLI is not installed")
        name = "dc-agent-" + uuid.uuid4().hex
        with tempfile.TemporaryDirectory(prefix="dc-agent-run-") as temp:
            input_dir, output_dir = _prepare(
                Path(temp), code, params, source_bytes, source_id, entrypoint
            )
            command = [
                docker,
                "run",
                "--name",
                name,
                "--rm",
                "--network",
                "none",
                "--user",
                "65534:65534",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--memory",
                "512m",
                "--cpus",
                "1",
                "--pids-limit",
                "64",
                "--ulimit",
                "fsize=16777216:16777216",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=64m",
                "--mount",
                f"type=bind,source={input_dir},target=/input,readonly",
                "--mount",
                f"type=bind,source={output_dir},target=/output",
                "--workdir",
                "/tmp",
                "--entrypoint",
                "python",
                self.image,
                "-I",
                "/input/runner.py",
                "/input",
                "/output",
            ]
            wait_timeout = _execution_timeout(self.timeout, self.deadline)
            process = subprocess.Popen(
                command, env=_environment(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            try:
                process.wait(timeout=wait_timeout)
            except subprocess.TimeoutExpired as exc:
                raise RunnerError(
                    "execution_timeout", "Container execution exceeded its time limit"
                ) from exc
            finally:
                try:
                    subprocess.run(
                        [docker, "rm", "-f", name],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=15,
                        check=False,
                    )
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=10)
            if process.returncode != 0:
                raise RunnerError(
                    "runner_failed", "Container failed; verify image and Docker daemon availability"
                )
            return _read_result(output_dir)
