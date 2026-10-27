"""Remote budgets share one absolute deadline; CLI defaults keep their own budget.

All source bytes and model replies are local fixtures. These tests never contact a
model, the company download service, or production runtime/code directories.
"""

import json
from pathlib import Path

import pytest
from test_application import ScriptedModel, make_service
from test_application import inputs as inputs

from dc_agent.domain import AgentError
from dc_agent.infrastructure.execution import LocalProcessRunner
from dc_agent.infrastructure.source import FileSourceProvider


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def code_of(call):
    """Both an admission rejection and a persisted failed Result are acceptable."""
    try:
        result = call()
    except AgentError as exc:
        return exc.code.upper()
    assert result.status == "failed", result
    return result.error["code"].upper()


@pytest.mark.parametrize("remote", [False, True])
def test_remote_budget_includes_admission_but_cli_still_has_600_seconds(
    inputs, monkeypatch, remote
):
    settings, task = inputs
    assert settings.task_timeout == 600
    clock = Clock()
    monkeypatch.setattr("dc_agent.application.service.time.monotonic", clock)
    service = make_service(settings, ScriptedModel())
    register = service.repo.register
    seen = []

    def slow_admission(value):
        clock.now += 10
        return register(value)

    def stop_at_graph(task, task_id, workspace, prepared, saved, deadline):
        seen.append(deadline)
        raise AgentError("BUDGET_EXCEEDED", "Test stops before generation")

    monkeypatch.setattr(service.repo, "register", slow_admission)
    monkeypatch.setattr(service.workflow, "run", stop_at_graph)
    # Existing adapter values must not leak between service invocations.
    service.source.deadline = None
    service.runner.deadline = None
    try:
        kwargs = {"deadline": 1300.0} if remote else {}
        assert code_of(lambda: service.run(task, **kwargs)) == "BUDGET_EXCEEDED"
        assert seen == [1300.0 if remote else 1610.0]
        assert service.source.deadline is None
        assert service.runner.deadline is None
        # Budget failure must release the generation lease.
        token = service.repo.claim(task.source_key, 1)
        assert token
        service.repo.release(task.source_key, token)
        assert service.repo.current(task.source_key) is None
    finally:
        service.close()


def test_expired_remote_budget_never_reads_source_or_calls_model(inputs, monkeypatch):
    settings, task = inputs
    clock = Clock()
    monkeypatch.setattr("dc_agent.application.service.time.monotonic", clock)
    model = ScriptedModel()
    service = make_service(settings, model)
    monkeypatch.setattr(
        service.source, "read", lambda _: pytest.fail("Expired job downloaded source")
    )
    try:
        assert code_of(lambda: service.run(task, deadline=999.0)) == "BUDGET_EXCEEDED"
        assert not model.calls and model.preflight_calls == 0
        assert service.repo.current(task.source_key) is None
    finally:
        service.close()


def test_external_generation_deadline_keeps_valid_candidate_for_publication(inputs, monkeypatch):
    settings, task = inputs
    clock = Clock()
    monkeypatch.setattr("dc_agent.application.service.time.monotonic", clock)

    class DeadlineDuringReview(ScriptedModel):
        def complete(self, operation, *args):
            reply = super().complete(operation, *args)
            if operation == "review_0":
                reply["quality_findings"] = ["Optional additional source coverage"]
                clock.now += 301
            return reply

    model = DeadlineDuringReview()
    service = make_service(settings, model)
    try:
        result = service.run(task, deadline=1300.0)
        assert result.status == "ready", result
        assert model.calls == ["design", "generate_0", "review_0"]
        assert result.artifact["selection"]["stop_reason"] == "BUDGET_EXCEEDED"
        assert result.artifact["selection"]["selected_attempt"] == 0
        assert result.artifact["quality_status"] == "usable_with_warnings"
        assert service.repo.current(task.source_key)["version"] == result.artifact["version"]
        assert service.source.deadline is None
        assert service.runner.deadline is None
    finally:
        service.close()


def test_deadline_between_production_and_evidence_keeps_earlier_candidate(inputs, monkeypatch):
    settings, task = inputs
    clock = Clock()
    monkeypatch.setattr("dc_agent.application.service.time.monotonic", clock)

    class RuntimeModel(ScriptedModel):
        def complete(self, operation, *args):
            reply = super().complete(operation, *args)
            if operation.startswith("generate"):
                reply["content"] += """
def _dc_validate(params):
    result = main(params)
    result["_dc_evidence"] = [{"record_index": 0, "name_refs": ["Sheet!A2"],
        "data_ref": "Sheet!B2", "unit_refs": [], "date_refs": []}]
    return result
"""
                reply.update(evidence_mode="runtime", evidence=[])
            elif operation == "review_0":
                reply["quality_findings"] = ["Optional additional source coverage"]
            return reply

    service = make_service(settings, RuntimeModel())
    original_run = service.runner.run
    executions = []

    def production_finishes_at_deadline(*args, **kwargs):
        result = original_run(*args, **kwargs)
        executions.append(kwargs.get("entrypoint", "main"))
        if len(executions) == 3:
            # Candidate 0 passed both entrypoints. Candidate 1's production
            # call consumes the rest, so its evidence call must not execute.
            clock.now = 1301.0
        return result

    monkeypatch.setattr(service.runner, "run", production_finishes_at_deadline)
    try:
        result = service.run(task, deadline=1300.0)
        assert result.status == "ready", result
        assert executions == ["main", "_dc_validate", "main"]
        assert result.artifact["selection"]["selected_attempt"] == 0
        assert result.artifact["selection"]["stop_reason"] == "BUDGET_EXCEEDED"
        assert result.artifact["quality_status"] == "usable_with_warnings"
    finally:
        service.close()


def test_download_timeout_and_stream_share_absolute_deadline(inputs, monkeypatch):
    settings, task = inputs
    task = task.model_copy(update={"source_path": None})
    clock = Clock()
    calls = []
    monkeypatch.setattr("dc_agent.infrastructure.source.time.monotonic", clock)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            pass

        def iter_bytes(self):
            yield b"first"
            clock.now += 6
            yield b"late"

    def stream(*args, **kwargs):
        calls.append(kwargs)
        return Response()

    monkeypatch.setattr("dc_agent.infrastructure.source.httpx.stream", stream)
    provider = FileSourceProvider(settings)
    provider.deadline = clock.now + 5
    with pytest.raises(AgentError) as error:
        provider.read(task)
    assert error.value.code.upper() == "BUDGET_EXCEEDED"
    assert calls[0]["timeout"] == pytest.approx(5)
    # Reusing the provider after expiry must not make another HTTP request.
    with pytest.raises(AgentError) as error:
        provider.read(task)
    assert error.value.code.upper() == "BUDGET_EXCEEDED"
    assert len(calls) == 1


def test_production_and_evidence_runs_do_not_restart_execution_budget(monkeypatch):
    clock = Clock()
    waits = []
    monkeypatch.setattr("dc_agent.infrastructure.execution.time.monotonic", clock)

    class Process:
        returncode = 0

        def __init__(self, command, **kwargs):
            output = Path(command[-1])
            (output / "result.json").write_text(
                json.dumps({"ok": True, "result": {"code": 0, "ExcelData": []}}),
                encoding="utf-8",
            )

        def wait(self, timeout):
            waits.append(timeout)
            clock.now += 2
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr("dc_agent.infrastructure.execution.subprocess.Popen", Process)
    runner = LocalProcessRunner(allow_unsafe=True, timeout=40)
    runner.deadline = clock.now + 5
    for entrypoint in ("main", "_dc_validate"):
        runner.run("def main(params): return {}", {}, b"fixture", "id", entrypoint=entrypoint)
    assert waits == pytest.approx([5, 3])
    clock.now += 2
    with pytest.raises(Exception) as error:
        runner.run("def main(params): return {}", {}, b"fixture", "id")
    assert getattr(error.value, "code", "").upper() == "BUDGET_EXCEEDED"
    assert len(waits) == 2
