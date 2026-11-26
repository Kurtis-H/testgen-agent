"""Run a test case against the live API and check its expectations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import httpx

from testgen_agent.cases import TestCase

_MISSING = object()


def fill(value):
    """Replace placeholders so stateful cases (like signups) don't collide on re-runs."""
    if isinstance(value, str):
        value = value.replace("{{unique_email}}", f"tg-{uuid.uuid4().hex[:10]}@example.com")
        return value.replace("{{unique_name}}", f"user-{uuid.uuid4().hex[:6]}")
    if isinstance(value, dict):
        return {k: fill(v) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v) for v in value]
    return value


def get_path(data, dotted: str):
    """'items.0.name' -> data['items'][0]['name'];  'items.length' -> len(data['items'])."""
    cur = data
    for part in dotted.split("."):
        if part == "length" and isinstance(cur, list | str | dict):
            cur = len(cur)
        elif isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return _MISSING
    return cur


@dataclass
class Outcome:
    passed: bool
    status: int
    problems: list[str]


def execute(case: TestCase, client: httpx.Client) -> Outcome:
    try:
        resp = client.request(case.method, case.path, params=fill(case.query) or None, json=fill(case.body))
    except httpx.HTTPError as exc:
        return Outcome(False, 0, [f"request failed: {exc}"])
    problems = []
    if resp.status_code != case.expect_status:
        problems.append(f"status {resp.status_code}, expected {case.expect_status}")
    if case.expect_json:
        try:
            body = resp.json()
        except ValueError:
            body = None
            problems.append("response is not JSON")
        if body is not None:
            for path, expected in case.expect_json.items():
                actual = get_path(body, path)
                if actual is _MISSING:
                    problems.append(f"{path}: missing")
                elif actual != expected:
                    problems.append(f"{path}: got {actual!r}, expected {expected!r}")
    return Outcome(not problems, resp.status_code, problems)
