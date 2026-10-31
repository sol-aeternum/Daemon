"""COPY-aware structural validation of our pinned pg_restore output.

This is not an untrusted SQL sandbox. The immutable archive hash remains mandatory.
Only the converter constructs used by this approved public-schema dump are accepted.
"""

from __future__ import annotations

import re
from pathlib import Path

from orchestrator.eval.campaign_validation import require

_PUBLIC = r'public\.(?:"(?:[^"]|"")+"|[a-z_][a-z_0-9]*)'
_ALLOWED = [
    r"SET (?:statement_timeout|lock_timeout|idle_in_transaction_session_timeout|client_encoding|standard_conforming_strings|check_function_bodies|xmloption|client_min_messages|row_security|default_tablespace|default_table_access_method)\s*=",
    r"SELECT pg_catalog\.set_config\('search_path', '', false\);$",
    r"CREATE EXTENSION IF NOT EXISTS (?:pgcrypto|vector) WITH SCHEMA public;$",
    r"COMMENT ON EXTENSION (?:pgcrypto|vector) IS ",
    # The remapped-copy dump clears public's comment; this exact form changes no
    # owner, ACL or catalogue-hashed state (catalogue() hashes no comments).
    r"COMMENT ON SCHEMA public IS '';$",
    rf"CREATE (?:TABLE|SEQUENCE|FUNCTION|TYPE|VIEW|MATERIALIZED VIEW) {_PUBLIC}(?=\s|\()",
    rf"ALTER (?:TABLE (?:ONLY )?|SEQUENCE |FUNCTION |TYPE |VIEW ){_PUBLIC}(?:\s|\()",
    rf"COMMENT ON (?:TABLE|COLUMN|FUNCTION|TYPE|SEQUENCE|VIEW) {_PUBLIC}(?:\.|\s|\()",
    rf'CREATE (?:UNIQUE )?INDEX (?:"[^"]+"|[a-z_][a-z_0-9]*) ON (?:ONLY )?{_PUBLIC}\s',
    rf"SELECT pg_catalog\.setval\('{_PUBLIC}', [0-9]+, (?:true|false)\);$",
    rf'CREATE TRIGGER (?:"[^"]+"|[a-z_][a-z_0-9]*) .* ON {_PUBLIC} .* EXECUTE (?:FUNCTION|PROCEDURE) {_PUBLIC}\([^;]*\);$',
]


def validate_sql(path: Path) -> dict[str, int]:
    """Reject unknown executable control syntax; COPY data never becomes SQL."""
    statement = ""
    quote = ""
    block_comment = 0
    copy = False
    restrict: str | None = None
    statements = copies = data_lines = 0
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if copy:
                if line.rstrip("\r\n") == r"\.":
                    copy = False
                else:
                    data_lines += 1
                continue
            if not quote and not block_comment and line.lstrip().startswith("\\"):
                require(not statement.strip(), "Unexpected psql command inside statement")
                parts = line.strip().split()
                require(
                    len(parts) == 2 and re.fullmatch(r"[A-Za-z0-9]+", parts[1]),
                    "Unknown psql command",
                )
                if parts[0] == r"\restrict":
                    require(restrict is None, "Nested restrict command")
                    restrict = parts[1]
                else:
                    require(
                        parts[0] == r"\unrestrict" and restrict == parts[1],
                        "Forbidden psql command",
                    )
                    restrict = None
                continue
            i = 0
            while i < len(line):
                char = line[i]
                pair = line[i : i + 2]
                if block_comment:
                    if pair == "/*":
                        block_comment += 1
                        i += 2
                    elif pair == "*/":
                        block_comment -= 1
                        i += 2
                    else:
                        i += 1
                    continue
                if quote:
                    if line.startswith(quote, i):
                        statement += quote
                        i += len(quote)
                        if quote in ("'", '"') and line.startswith(quote, i):
                            statement += quote
                            i += len(quote)
                        else:
                            quote = ""
                    else:
                        statement += char
                        i += 1
                    continue
                if pair == "--":
                    break
                if pair == "/*":
                    block_comment = 1
                    i += 2
                    continue
                if char in ("'", '"'):
                    quote = char
                elif char == "$":
                    match = re.match(r"\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$", line[i:])
                    if match:
                        quote = match[0]
                        statement += quote
                        i += len(quote)
                        continue
                statement += char
                i += 1
                if char == ";":
                    command = statement.strip()
                    if re.fullmatch(rf"COPY {_PUBLIC} \([^;]+\) FROM stdin;", command, re.S):
                        copy = True
                        copies += 1
                        require(not line[i:].strip(), "SQL after COPY header")
                    else:
                        keywords = re.match(r"[A-Z]+(?: [A-Z]+){0,2}", command)
                        require(
                            any(re.match(pattern, command, re.S) for pattern in _ALLOWED),
                            "Unknown restore SQL control statement: "
                            + (keywords[0] if keywords else "unclassified"),
                        )
                    statements += 1
                    statement = ""
            statement += "\n"
    require(
        not copy and not quote and not block_comment and not statement.strip() and restrict is None,
        "Incomplete restore SQL",
    )
    require(statements > 0 and copies > 0, "Empty/non-data restore SQL")
    return {"statements": statements, "copy_blocks": copies, "copy_lines": data_lines}
