"""Regression test for issue #115: /v1/tools/test endpoint must be absent.

The endpoint was an undocumented cost-bearing test scaffolding route that hardcoded
the sentinel billing UUID and executed real provider completions. It was removed
because it widened the unaudited surface and was easily mistaken for a supported
endpoint by clients. This test asserts the route is no longer present in the
app factory's route table.
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from orchestrator.main import app


def test_v1_tools_test_route_is_absent() -> None:
    """Issue #115: /v1/tools/test must not be registered on the app factory."""
    paths = {route.path for route in app.routes if isinstance(route, APIRoute)}
    assert "/v1/tools/test" not in paths, (
        "/v1/tools/test must not be registered — see issue #115. "
        f"Current routes include: {sorted(p for p in paths if 'tools' in p)}"
    )


def test_test_tools_handler_function_is_absent() -> None:
    """Issue #115: the test_tools handler function must not exist in orchestrator.main."""
    import orchestrator.main as main_module

    assert not hasattr(main_module, "test_tools"), (
        "test_tools handler must not exist in orchestrator.main — see issue #115."
    )
