"""Fixed Docker Library Redis qualification shared by CI and the owned fixture.

Registry index -> linux/amd64 manifest -> config hashes were verified against
Docker Library's Redis 7.4.7-alpine publication. See docs/OWNED_TEST_FIXTURES.md.
This module inspects only; it never pulls, substitutes or retries an image.
"""

from __future__ import annotations

import json
import os
import subprocess

INDEX_DIGEST = "sha256:02f2cc4882f8bf87c79a220ac958f58c700bdec0dfb9b9ea61b62fb0e8f1bfcf"
AMD64_MANIFEST_DIGEST = "sha256:b9f6cf0cab55fdd102fd2182deeae150457943d33439d18c6e2fc5666d3228c1"
CONFIG_DIGEST = "sha256:e08bd8d5a6774025cff22e5db8361f6a5d023811e84d77d0033e47d44970cc94"
IMAGE_REFERENCE = f"docker.io/library/redis@{INDEX_DIGEST}"
PLATFORM = "linux/amd64"


def validate_cached_image(data: object) -> str:
    """Fail closed on unknown identities, metadata shapes or platform selection."""
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise ValueError("Ambiguous pinned Redis image inspection")
    item = data[0]
    image = item.get("Id")
    digests = item.get("RepoDigests")
    allowed_references = {IMAGE_REFERENCE, f"redis@{INDEX_DIGEST}"}
    if (
        not isinstance(image, str)
        or image not in {INDEX_DIGEST, CONFIG_DIGEST}
        or item.get("Os") != "linux"
        or item.get("Architecture") != "amd64"
        or not isinstance(digests, list)
        or not digests
        or not all(isinstance(ref, str) and ref in allowed_references for ref in digests)
    ):
        raise ValueError("Pinned Redis image identity/platform mismatch")
    # Docker's containerd store exposes the index Id; classic stores expose the
    # amd64 config Id. Both are bound by the verified index/manifest/config chain.
    return image


def main() -> None:
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", IMAGE_REFERENCE],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
            env={key: os.environ[key] for key in ("PATH", "HOME", "LANG") if key in os.environ},
        )
    except subprocess.CalledProcessError as error:
        raise SystemExit(
            f"docker image inspect {IMAGE_REFERENCE} failed (exit {error.returncode}); "
            f"stderr={json.dumps((error.stderr or '')[:2048])}; "
            f"stdout={json.dumps((error.stdout or '')[:2048])}"
        ) from error
    try:
        image = validate_cached_image(json.loads(result.stdout))
    except ValueError as error:
        raise SystemExit(f"Pinned Redis prerequisite rejected: {error}") from error
    print(f"Qualified {IMAGE_REFERENCE} platform={PLATFORM} daemon_id={image}")


if __name__ == "__main__":
    main()
