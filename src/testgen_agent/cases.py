"""Test cases are DATA, not code.

The model proposes test cases as JSON. Each one is validated against the spec and a
strict shape before anything runs, and only a fixed template turns cases into pytest
code. Nothing the model writes is ever executed directly.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

from testgen_agent.spec import Operation

PLACEHOLDERS = ("{{unique_email}}", "{{unique_name}}")
MAX_CASE_BYTES = 4000


@dataclass
class TestCase:
    __test__ = False  # not a pytest test class, despite the name

    name: str
    operation_id: str
    method: str
    path: str
    query: dict = field(default_factory=dict)
    body: dict | list | None = None
    expect_status: int = 200
    expect_json: dict = field(default_factory=dict)  # "dot.path" -> value, "items.length" -> int
    why: str = ""

    def key(self) -> str:
        """Two cases with the same request and expectations are duplicates, whatever their names."""
        d = asdict(self)
        d.pop("name")
        d.pop("why")
        return json.dumps(d, sort_keys=True)


class InvalidCase(Exception):
    pass


def _identifier(name: str) -> str:
    ident = re.sub(r"\W+", "_", name.strip().lower()).strip("_")[:60]
    return ident if ident and not ident[0].isdigit() else f"case_{ident}"


def validate(raw: dict, op: Operation) -> TestCase:
    if not isinstance(raw, dict):
        raise InvalidCase("case is not an object")
    if len(json.dumps(raw)) > MAX_CASE_BYTES:
        raise InvalidCase("case is too large")
    allowed = {"name", "query", "body", "expect_status", "expect_json", "why", "method", "path"}
    extra = set(raw) - allowed
    if extra:
        raise InvalidCase(f"unknown fields {sorted(extra)}")
    method = str(raw.get("method", op.method)).upper()
    path = str(raw.get("path", op.path))
    if (method, path) != (op.method, op.path):
        raise InvalidCase(f"{method} {path} is not this operation ({op.method} {op.path})")
    status = raw.get("expect_status")
    if not isinstance(status, int) or not 100 <= status <= 599:
        raise InvalidCase(f"expect_status must be an HTTP status code, got {status!r}")
    if str(status) not in op.responses and status != 422:
        raise InvalidCase(f"spec documents no {status} response for {op.operation_id}")
    query = raw.get("query") or {}
    if not isinstance(query, dict) or not all(isinstance(v, str | int | float | bool) for v in query.values()):
        raise InvalidCase("query must be an object of simple values")
    known = {p.get("name") for p in op.parameters if p.get("in") == "query"}
    if set(query) - known:
        raise InvalidCase(f"unknown query parameters {sorted(set(query) - known)}")
    body = raw.get("body")
    if body is not None and op.method == "GET":
        raise InvalidCase("GET requests can't have a body")
    expect_json = raw.get("expect_json") or {}
    if not isinstance(expect_json, dict):
        raise InvalidCase("expect_json must be an object of 'dot.path': value")
    if not expect_json and status < 400 and not body and not query:
        pass  # status-only checks are allowed but weak; the report counts them
    name = _identifier(str(raw.get("name") or "unnamed"))
    return TestCase(
        name=name,
        operation_id=op.operation_id,
        method=method,
        path=path,
        query={k: v for k, v in query.items()},
        body=body,
        expect_status=status,
        expect_json=expect_json,
        why=str(raw.get("why", ""))[:200],
    )


def parse_proposals(text: str) -> list:
    """Pull the first JSON array out of a model reply (models like to add chatter)."""
    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        raise InvalidCase("no JSON array in model output")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise InvalidCase(f"invalid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise InvalidCase("expected a JSON array")
    return data
