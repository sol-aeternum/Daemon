from __future__ import annotations

import asyncio
import json

import pytest

from orchestrator.tools.builtin import CalculateTool


@pytest.fixture
def tool():
    return CalculateTool()


def _run(tool, expression):
    return asyncio.run(tool.execute(expression=expression))


class TestCalculateHappyPath:
    def test_simple_addition(self, tool):
        assert json.loads(_run(tool, "2 + 2")) == {"expression": "2 + 2", "result": 4}

    def test_precedence(self, tool):
        assert json.loads(_run(tool, "2 + 3 * 4")) == {"expression": "2 + 3 * 4", "result": 14}

    def test_parentheses(self, tool):
        assert json.loads(_run(tool, "(2 + 3) * 4")) == {"expression": "(2 + 3) * 4", "result": 20}

    def test_unary_minus(self, tool):
        assert json.loads(_run(tool, "-5 + 10")) == {"expression": "-5 + 10", "result": 5}

    def test_power(self, tool):
        assert json.loads(_run(tool, "2 ** 10")) == {"expression": "2 ** 10", "result": 1024}

    def test_power_with_parenthesized_base(self, tool):
        assert json.loads(_run(tool, "(2 + 3) ** 2")) == {
            "expression": "(2 + 3) ** 2",
            "result": 25,
        }

    def test_power_with_parenthesized_exponent(self, tool):
        assert json.loads(_run(tool, "2 ** (1 + 2)")) == {"expression": "2 ** (1 + 2)", "result": 8}

    def test_power_with_signed_exponent(self, tool):
        assert json.loads(_run(tool, "2 ** -1")) == {"expression": "2 ** -1", "result": 0.5}

    def test_power_is_right_associative(self, tool):
        assert json.loads(_run(tool, "2 ** 3 ** 2")) == {"expression": "2 ** 3 ** 2", "result": 512}

    def test_unary_minus_binds_less_tightly_than_power(self, tool):
        assert json.loads(_run(tool, "-2 ** 2")) == {"expression": "-2 ** 2", "result": -4}

    def test_division_and_modulo(self, tool):
        assert json.loads(_run(tool, "17 % 5")) == {"expression": "17 % 5", "result": 2}
        assert json.loads(_run(tool, "10 / 4")) == {"expression": "10 / 4", "result": 2.5}

    def test_decimals_and_scientific(self, tool):
        assert json.loads(_run(tool, "1.5e2")) == {"expression": "1.5e2", "result": 150.0}


class TestCalculateRejectsMalformed:
    def test_unmatched_paren(self, tool):
        out = _run(tool, "(2 + 3")
        assert "error" in json.loads(out)

    def test_trailing_garbage(self, tool):
        out = _run(tool, "2 + 2 garbage")
        assert "error" in json.loads(out)

    def test_empty(self, tool):
        out = _run(tool, "")
        assert "error" in json.loads(out)

    def test_division_by_zero(self, tool):
        out = _run(tool, "1 / 0")
        assert "error" in json.loads(out)

    def test_arithmetic_exception(self, tool):
        out = _run(tool, "10 ** 1000000")
        result = json.loads(out)
        assert "error" in result
        assert "result" not in result

    def test_non_string(self, tool):
        out = _run(tool, None)  # type: ignore[arg-type]
        assert "error" in json.loads(out)


class TestCalculateRejectsSandboxEscapes:
    SANDBOX_ESCAPE_PAYLOADS = [
        "__import__('os').system('id')",
        "().__class__.__bases__[0].__subclasses__()",
        "[].__class__.__mro__[1].__subclasses__()",
        "''.__class__.__mro__[1].__subclasses__()",
        "().__class__.__bases__[0].__subclasses__()[0].__init_subclass__()",
        "{}.pop.__class__.__call__",
        "''.__class__.__mro__[1].__subclasses__()[0].__init__.__globals__",
        "eval('1+1')",
        "exec('print(1)')",
        "open('/etc/passwd').read()",
        "import os",
        "lambda: 0",
        "1 if True else 0",
        "[1 for x in range(10)]",
        "{1: 2}",
        "'a'.upper()",
        "1 and 2",
        "1 or 2",
        "not 1",
        "x = 1",
        "x",
        "True",
        "None",
        "'a'",
        '"a"',
        "f'{1}'",
    ]

    @pytest.mark.parametrize("payload", SANDBOX_ESCAPE_PAYLOADS)
    def test_payload_rejected(self, tool, payload):
        out = _run(tool, payload)
        result = json.loads(out)
        assert "error" in result, f"Payload should be rejected: {payload!r}"
        assert "result" not in result
