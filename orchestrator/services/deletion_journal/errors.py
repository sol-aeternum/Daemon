"""Closed diagnostics: never expose a journal payload, path or database DSN."""

from typing import Literal

ErrorCode = Literal[
    "invalid_entry",
    "conflict",
    "unavailable",
    "corrupt",
    "unknown_append",
    "unknown_upgrade",
    "invalid_config",
    "database_required",
    "schema_required",
    "binding_required",
    "marker_mismatch",
    "recovery_required",
]


class JournalError(RuntimeError):
    def __init__(self, code: ErrorCode) -> None:
        self.code = code
        super().__init__(code)
