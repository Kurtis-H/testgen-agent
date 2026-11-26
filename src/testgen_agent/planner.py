"""Ask a model to propose test cases for one operation."""

from __future__ import annotations

import json

import httpx

from testgen_agent.spec import Operation

SYSTEM = """You are a senior QA engineer writing API tests.
For the operation below, propose test cases as a JSON array. Each item:
{"name": "short_snake_case", "why": "what this checks",
 "query": {...}, "body": {...} or null,
 "expect_status": <int>, "expect_json": {"dot.path": expected_value}}

Rules:
- Only use status codes the spec documents.
- Cover: the happy path, boundary values (exactly at min/max, one past), invalid input,
  missing required fields, and rules stated in the summary.
- In expect_json use dot paths: "total", "items.0.name", and "items.length" for list sizes.
- Only assert values you can derive from the spec. Don't guess data you can't know.
- For values that must be unique per run, use "{{unique_email}}" or "{{unique_name}}".
- Return ONLY the JSON array."""


class OllamaPlanner:
    def __init__(self, model: str, base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url.rstrip("/")

    def propose(self, op: Operation, n: int, existing: list[str]) -> str:
        user = f"Operation:\n{op.describe()}\n\nPropose {n} test cases."
        if existing:
            user += "\n\nAlready covered (don't repeat):\n" + "\n".join(f"- {e}" for e in existing[:30])
        resp = httpx.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "stream": False,
                "options": {"temperature": 0.4},
                "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
            },
            timeout=300,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]


class FakePlanner:
    """Scripted proposals for tests and demos. Like a real model, it gets some things
    wrong on purpose: an invented endpoint, a duplicate, an expectation the API doesn't
    meet, and a gap (no password-length boundary test) that mutation scoring exposes."""

    model = "fake-planner"

    SCRIPT = {
        "health": [
            {"name": "health ok", "expect_status": 200, "expect_json": {"status": "ok"}},
        ],
        "listProducts": [
            {"name": "first page", "why": "default paging", "query": {}, "expect_status": 200,
             "expect_json": {"total": 23, "items.length": 5, "totalPages": 5, "page": 1}},
            {"name": "last partial page", "why": "23 items / 5 per page leaves 3 on page 5", "query": {"page": 5},
             "expect_status": 200, "expect_json": {"items.length": 3}},
            {"name": "search is case insensitive", "query": {"q": "usb"}, "expect_status": 200,
             "expect_json": {"total": 2}},
            {"name": "page zero rejected", "why": "page minimum is 1", "query": {"page": 0}, "expect_status": 400},
            {"name": "size above max rejected", "query": {"size": 51}, "expect_status": 400},
            {"name": "ten per page", "why": "model guessed wrong: 23/10 is 3 pages, not 2", "query": {"size": 10},
             "expect_status": 200, "expect_json": {"totalPages": 2}},
            {"name": "first page again", "query": {}, "expect_status": 200,
             "expect_json": {"total": 23, "items.length": 5, "totalPages": 5, "page": 1}},
            {"name": "sorted endpoint", "method": "GET", "path": "/api/products/sorted", "expect_status": 200},
        ],
        "signup": [
            {"name": "valid signup", "body": {"name": "Ada", "email": "{{unique_email}}", "password": "correcthorse",
             "confirm": "correcthorse", "age": 30}, "expect_status": 201, "expect_json": {"name": "Ada"}},
            {"name": "invalid email rejected", "body": {"name": "Ada", "email": "a@b", "password": "correcthorse",
             "confirm": "correcthorse", "age": 30}, "expect_status": 400},
            {"name": "underage rejected", "body": {"name": "Kid", "email": "{{unique_email}}", "password": "correcthorse",
             "confirm": "correcthorse", "age": 12}, "expect_status": 400},
            {"name": "missing fields rejected", "body": {"email": "{{unique_email}}"}, "expect_status": 400},
            {"name": "creates account", "body": {"name": "Ada", "email": "{{unique_email}}"}, "expect_status": 201},
        ],
        "searchFruit": [
            {"name": "melon matches", "query": {"q": "melon"}, "expect_status": 200,
             "expect_json": {"results": ["melon", "watermelon"]}},
            {"name": "server error is a 500", "query": {"q": "error"}, "expect_status": 500},
            {"name": "unknown status", "query": {"q": "x"}, "expect_status": 418},
        ],
    }  # fmt: skip

    def propose(self, op: Operation, n: int, existing: list[str]) -> str:
        return json.dumps(self.SCRIPT.get(op.operation_id, [])[:n])
