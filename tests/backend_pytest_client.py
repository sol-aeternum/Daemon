"""Authenticate the original private handoff before importing pytest."""

import sys
import time

from tests import backend_pytest_supervisor as supervisor


def main(argv):
    supervisor.client_channel(time.monotonic() + 30)
    import pytest

    return pytest.main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
