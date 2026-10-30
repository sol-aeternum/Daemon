"""Exact proposed W authority, only for labelled network-none owned fixtures.

No credential/DSN lookup and no application connection. Never imported by runtime.
The immutable fixture records the proposal identity; later proposals need new data.
"""

import json
from pathlib import Path

MAP = json.loads((Path(__file__).parent / "fixtures/exact_worker_privileges.json").read_text())


def grant_exact_worker(cluster, database):
    assert database in cluster.databases and cluster.container is not None
    # The owner harness created this exact DB and the md_app login. No user input.
    statements = ["GRANT USAGE ON SCHEMA public TO md_app"]
    for entry in MAP["ordinary_tables"]:
        statements.append(
            f"GRANT {','.join(entry['table_privileges'])} ON TABLE {entry['table']} TO md_app"
        )
        if entry["update_columns"]:
            statements.append(
                f"GRANT UPDATE({','.join(entry['update_columns'])}) ON TABLE {entry['table']} TO md_app"
            )
    statements.extend(f"GRANT USAGE ON SEQUENCE {s} TO md_app" for s in MAP["sequences_usage_only"])
    statements.extend(
        f"GRANT EXECUTE ON FUNCTION {f} TO md_app"
        for f in MAP["ordinary_helper_execute"] + MAP["worker_public_entrypoints_execute"]
    )
    cluster.sql(";".join(statements) + ";", database=database, role="md_owner")
    cluster.record("exact_worker_grants", database=database, proposal=MAP["proposal_sha256"])
