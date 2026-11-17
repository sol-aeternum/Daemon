"""Exact proposed W/A authority, only for labelled network-none owned fixtures.

No credential/DSN lookup and no application connection. Never imported by runtime.
The pinned maps live in config/task_privileges and carry their proposal identity;
grants come from the same builder the live provisioning script uses.
"""

from scripts.provision_task_roles import grant_statements, load_maps

ACCEPTOR_MAP, MAP = load_maps()


def grant_exact_worker(cluster, database):
    assert database in cluster.databases and cluster.container is not None
    # The owner harness created this exact DB and the md_app login. No user input.
    cluster.sql(";".join(grant_statements(MAP, "md_app")) + ";", database=database, role="md_owner")
    cluster.record("exact_worker_grants", database=database, proposal=MAP["proposal_sha256"])


def grant_exact_acceptor(cluster, database):
    assert database in cluster.databases and cluster.container is not None
    cluster.sql(
        ";".join(grant_statements(ACCEPTOR_MAP, "md_acceptor")) + ";",
        database=database,
        role="md_owner",
    )
    cluster.record(
        "exact_acceptor_grants", database=database, proposal=ACCEPTOR_MAP["proposal_sha256"]
    )
