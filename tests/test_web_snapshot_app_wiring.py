"""Pin registration on the real application, not only a standalone router."""


def test_snapshot_routes_are_in_main_application():
    from orchestrator.main import app

    paths = app.openapi()["paths"]
    root = "/conversations/{conversation_id}/web-snapshots"
    assert "get" in paths[root]
    assert "get" in paths[root + "/{snapshot_id}/export"]
    assert "delete" in paths[root + "/{snapshot_id}"]
