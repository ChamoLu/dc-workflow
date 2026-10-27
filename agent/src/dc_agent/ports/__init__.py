"""The small set of replaceable external boundaries."""

from typing import Protocol

from ..domain import Task


class ModelGateway(Protocol):
    def complete(
        self, operation: str, system: str, context: dict, schema: dict, timeout: float
    ) -> dict: ...


class SourceProvider(Protocol):
    def read(self, task: Task) -> bytes: ...


class ReferenceProvider(Protocol):
    def load(self, task: Task) -> list[dict]: ...


class RuleContextProvider(Protocol):
    """Resolve a unique catalog task and its optional, type-specific worksheet."""

    def load(self, task: Task) -> dict: ...


class ExecutionRunner(Protocol):
    def run(
        self,
        code: str,
        params: dict,
        source_bytes: bytes,
        source_id: str,
        *,
        entrypoint: str = "main",
    ) -> dict: ...


class SkillProvider(Protocol):
    """Future project Skill registration can implement the existing loader boundary."""

    def load(self, task: Task) -> dict: ...


class ToolRegistry(Protocol):
    """Reserved interface only; no registry or model tool-calling implementation yet."""

    def describe(self, kind: str) -> list[dict]: ...
    def invoke(self, tool_id: str, args: dict, context: dict) -> dict: ...
