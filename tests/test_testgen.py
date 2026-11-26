import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from testgen_agent.agent import finish, generate
from testgen_agent.cases import InvalidCase, TestCase, parse_proposals, validate
from testgen_agent.cli import main
from testgen_agent.executor import _MISSING, execute, fill, get_path
from testgen_agent.render import render_case
from testgen_agent.spec import load_spec, operations

SPEC = Path(__file__).parent.parent / "examples" / "bug-hunt-lab.openapi.json"


# --- a tiny stand-in API --------------------------------------------------------------

ITEMS = [{"id": i, "name": f"Item {i}"} for i in range(1, 24)]
FLIP = {"n": 0}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/api/health":
            FLIP["n"] += 1
            return self._send(200, {"status": "ok", "flip": FLIP["n"] % 2})
        if url.path == "/api/products":
            try:
                page, size = int(q.get("page", 1)), int(q.get("size", 5))
            except ValueError:
                return self._send(400, {"error": "bad"})
            if page < 1 or not 1 <= size <= 50:
                return self._send(400, {"error": "bad"})
            pages = -(-len(ITEMS) // size)
            return self._send(
                200, {"items": ITEMS[(page - 1) * size : page * size], "total": len(ITEMS), "totalPages": pages}
            )
        return self._send(404, {"error": "nope"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        if self.path == "/api/signup":
            ok = "@" in body.get("email", "") and "." in body.get("email", "").split("@")[-1]
            return self._send(201 if ok else 400, {"email": body.get("email")})
        return self._send(404, {})

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def api():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture
def ops():
    return {o.operation_id: o for o in operations(load_spec(str(SPEC)))}


class ScriptPlanner:
    model = "script"

    def __init__(self, script):
        self.script = script

    def propose(self, op, n, existing):
        return json.dumps(self.script.get(op.operation_id, []))


# --- spec ---------------------------------------------------------------------------


def test_spec_operations_and_ref_resolution(ops):
    assert set(ops) == {"health", "listProducts", "signup", "searchFruit"}
    assert "minLength" in json.dumps(ops["signup"].body_schema), "$ref was inlined"
    assert "page" in ops["listProducts"].describe()


# --- validation: the guardrails ------------------------------------------------------


def test_valid_case(ops):
    case = validate({"name": "First Page!", "query": {"page": 1}, "expect_status": 200}, ops["listProducts"])
    assert case.name == "first_page" and case.method == "GET"


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        ({"name": "x", "path": "/api/other", "expect_status": 200}, "not this operation"),
        ({"name": "x", "expect_status": 418}, "documents no 418"),
        ({"name": "x", "expect_status": "200"}, "HTTP status"),
        ({"name": "x", "query": {"sort": "name"}, "expect_status": 200}, "unknown query"),
        ({"name": "x", "query": {"page": {"$gt": 1}}, "expect_status": 200}, "simple values"),
        ({"name": "x", "body": {"a": 1}, "expect_status": 200}, "can't have a body"),
        ({"name": "x", "expect_status": 200, "code": "import os"}, "unknown fields"),
        ({"name": "x", "expect_status": 200, "why": "y" * 5000}, "too large"),
        ("not an object", "not an object"),
    ],
)
def test_invalid_cases_are_rejected(ops, raw, error):
    with pytest.raises(InvalidCase, match=error):
        validate(raw, ops["listProducts"])


def test_parse_proposals_tolerates_chatter():
    assert parse_proposals('Here you go!\n[{"name": "a"}]\nHope that helps') == [{"name": "a"}]
    with pytest.raises(InvalidCase):
        parse_proposals("no json here")


def test_duplicate_key_ignores_names(ops):
    a = validate({"name": "a", "query": {"page": 2}, "expect_status": 200}, ops["listProducts"])
    b = validate(
        {"name": "b", "query": {"page": 2}, "expect_status": 200, "why": "different words"}, ops["listProducts"]
    )
    assert a.key() == b.key()


# --- executor -----------------------------------------------------------------------


def test_get_path():
    data = {"items": [{"name": "A"}, {"name": "B"}], "total": 2}
    assert get_path(data, "items.length") == 2
    assert get_path(data, "items.1.name") == "B"
    assert get_path(data, "items.5.name") is _MISSING
    assert get_path(data, "nope") is _MISSING


def test_fill_makes_unique_values():
    a, b = fill({"email": "{{unique_email}}"}), fill({"email": "{{unique_email}}"})
    assert a["email"] != b["email"] and a["email"].endswith("@example.com")


def test_execute_reports_every_problem(api):
    case = TestCase("t", "listProducts", "GET", "/api/products", expect_status=200,
                    expect_json={"total": 99, "items.length": 5, "missing.key": 1})  # fmt: skip
    with httpx.Client(base_url=api) as c:
        out = execute(case, c)
    assert not out.passed
    assert any("total" in p for p in out.problems) and any("missing" in p for p in out.problems)


# --- rendering: data in, safe code out ------------------------------------------------


def test_rendered_code_compiles_and_neutralises_injection():
    nasty = TestCase(
        name="x",
        operation_id="op",
        method="POST",
        path="/api/signup",
        body={"name": "'); import os; os.system('rm -rf /'); ('"},
        expect_status=201,
        expect_json={"a'b": '"); boom()'},
        why="line one\nimport os",
    )
    code = render_case(nasty)
    compile(code, "<generated>", "exec")
    assert "os.system" in code  # present only inside a string literal...
    tree_names = [
        n.id for n in __import__("ast").walk(__import__("ast").parse(code)) if isinstance(n, __import__("ast").Name)
    ]
    assert "os" not in tree_names, "...never as executable code"
    assert "\nimport os" not in code


# --- the agent loop ---------------------------------------------------------------------


def test_agent_buckets(api, ops, tmp_path):
    planner = ScriptPlanner(
        {
            "listProducts": [
                {
                    "name": "first page",
                    "query": {},
                    "expect_status": 200,
                    "expect_json": {"total": 23, "items.length": 5},
                },
                {
                    "name": "same again",
                    "query": {},
                    "expect_status": 200,
                    "expect_json": {"total": 23, "items.length": 5},
                },
                {"name": "wrong guess", "query": {"size": 10}, "expect_status": 200, "expect_json": {"totalPages": 2}},
                {"name": "page zero", "query": {"page": 0}, "expect_status": 400},
                {"name": "made up", "path": "/api/products/x", "expect_status": 200},
            ],
            "health": [{"name": "flaky", "expect_status": 200, "expect_json": {"flip": 1}}],
            "signup": [{"name": "weak", "body": {"email": "{{unique_email}}"}, "expect_status": 201}],
        }
    )
    res = generate([ops["listProducts"], ops["health"], ops["signup"]], planner, api)
    assert [c.name for c in res.kept] == ["first_page", "page_zero", "weak"]
    assert [f.case.name for f in res.review] == ["wrong_guess"], "a failing expectation is never rewritten"
    assert [f.case.name for f in res.flaky] == ["flaky"]
    assert res.duplicates == ["same_again"]
    assert [n for n, _ in res.invalid] == ["made up"]
    assert [c.name for c in res.weak] == ["weak"]

    res = finish(res, str(tmp_path / "gen"), api, {})
    assert res.pytest_ok is True
    assert (tmp_path / "gen" / "test_listproducts.py").exists()


def test_relative_output_dir_works(api, ops, tmp_path, monkeypatch):
    # Regression: run_pytest used cwd=out_dir with a relative path, so pytest found no tests.
    monkeypatch.chdir(tmp_path)
    planner = ScriptPlanner(
        {"listProducts": [{"name": "total", "query": {}, "expect_status": 200, "expect_json": {"total": 23}}]}
    )
    res = finish(generate([ops["listProducts"]], planner, api), "generated", api, {})
    assert res.pytest_ok is True


def test_planner_garbage_is_recorded_not_fatal(api, ops):
    class Garbage:
        model = "g"

        def propose(self, op, n, existing):
            return "sorry, I can't do that"

    res = generate([ops["health"]], Garbage(), api)
    assert res.planner_errors and not res.kept


def test_cli_end_to_end_with_mutant(api, tmp_path, monkeypatch):
    from testgen_agent import planner as planner_mod

    script = {"listProducts": [{"name": "total", "query": {}, "expect_status": 200, "expect_json": {"total": 23}}]}
    monkeypatch.setattr(planner_mod.FakePlanner, "SCRIPT", script)
    out, md = tmp_path / "gen", tmp_path / "r.md"
    args = [
        "--spec",
        str(SPEC),
        "--base-url",
        api,
        "--provider",
        "fake",
        "--out",
        str(out),
        "--md",
        str(md),
        "--only",
        "listProducts",
    ]
    # The "mutant" is the same healthy API, so the mutant survives and the score gate fails.
    assert main([*args, "--mutant", f"same={api}", "--min-mutation-score", "1"]) == 1
    assert "0 of 1 mutants caught" in md.read_text()
    assert main(args) == 0
    assert main(["--spec", "missing.json", "--base-url", api]) == 2
