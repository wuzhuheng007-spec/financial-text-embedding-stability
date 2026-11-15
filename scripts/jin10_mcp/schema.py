"""Explicit tool schema and budget objects for a negotiated MCP session."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: str  # FLASH or CALENDAR
    input_schema: dict[str, Any]
    arguments: dict[str, Any]
    items_path: tuple[str | int, ...]
    cursor_argument: str | None = None
    has_more_path: tuple[str | int, ...] | None = None
    next_cursor_path: tuple[str | int, ...] | None = None
    source_id_field: str | None = None
    source_id_stability_confirmed: bool = False
    url_field: str | None = None
    url_semantics_confirmed: bool = False
    published_field: str | None = None
    timezone_field: str | None = None
    title_field: str | None = None
    content_field: str | None = None
    business_status_path: tuple[str | int, ...] | None = None
    business_success_values: tuple[Any, ...] = field(default_factory=tuple)

    def validate(self) -> None:
        if not self.name or self.kind not in {"FLASH", "CALENDAR"}:
            raise ValueError("Tool name and kind are required")
        properties = self.input_schema.get("properties", {})
        if self.input_schema.get("type") != "object" or not isinstance(properties, dict):
            raise ValueError("Negotiated input schema must be an object")
        if set(self.arguments) - set(properties):
            raise ValueError("Arguments not present in negotiated tool schema")
        if set(self.input_schema.get("required", [])) - set(self.arguments):
            raise ValueError("Required negotiated tool arguments are missing")
        if self.cursor_argument and self.cursor_argument not in properties:
            raise ValueError("Cursor parameter not present in negotiated tool schema")
        if self.kind == "FLASH" and not self.items_path:
            raise ValueError("Flash item path must be confirmed from a response")
        if not isinstance(self.url_semantics_confirmed, bool):
            raise ValueError("URL semantic confirmation must be explicit")
        if not isinstance(self.source_id_stability_confirmed, bool):
            raise ValueError("Source-ID stability confirmation must be explicit")
        if bool(self.has_more_path) != bool(self.next_cursor_path):
            raise ValueError("Pagination fields must be configured together")

    def args_for_cursor(self, cursor: Any) -> dict[str, Any]:
        self.validate()
        arguments = dict(self.arguments)
        if cursor is not None:
            if not self.cursor_argument:
                raise ValueError("Server returned a cursor but no negotiated input field exists")
            arguments[self.cursor_argument] = cursor
        return arguments


@dataclass(frozen=True)
class Budget:
    max_requests: int = 3
    max_pages: int = 2
    max_items: int = 100
    max_runtime_seconds: int = 120
    timeout_seconds: int = 25
    max_attempts_per_page: int = 2
    max_response_bytes: int = 8 * 1024 * 1024

    def validate(self) -> None:
        if not (1 <= self.max_requests <= 1000):
            raise ValueError("Invalid request budget")
        if not (1 <= self.max_pages <= 1000) or not (1 <= self.max_items <= 100000):
            raise ValueError("Invalid page or item budget")
        if not (1 <= self.max_runtime_seconds <= 86400):
            raise ValueError("Trial runtime must be at most 24 hours")
        if not (1 <= self.timeout_seconds <= 120) or not (1 <= self.max_attempts_per_page <= 3):
            raise ValueError("Invalid timeout or retry bound")
        if not (1024 <= self.max_response_bytes <= 50 * 1024 * 1024):
            raise ValueError("Invalid response byte bound")


@dataclass(frozen=True)
class ToolResponse:
    payload: dict[str, Any]
    http_status: int | None = None
    business_status: str | int | None = None


class ToolCallError(RuntimeError):
    def __init__(self, message: str, *, http_status: int | None = None,
                 business_status: str | int | None = None,
                 retry_after_seconds: float | None = None):
        super().__init__(message)
        self.http_status = http_status
        self.business_status = business_status
        self.retry_after_seconds = retry_after_seconds


def at_path(value: Any, path: tuple[str | int, ...]) -> Any:
    current = value
    for part in path:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and isinstance(part, int) and 0 <= part < len(current):
            current = current[part]
        else:
            raise ValueError(f"Response path missing at {part!r}")
    return current
