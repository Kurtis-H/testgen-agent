"""`testgen --spec openapi.json --base-url http://127.0.0.1:3000 --out generated/`"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from testgen_agent import __version__
from testgen_agent.agent import Result, finish, generate
from testgen_agent.planner import FakePlanner, OllamaPlanner
from testgen_agent.spec import load_spec, operations


def md_escape(text: str) -> str:
    return str(text).replace("|", "\\|").replace("<", "&lt;").replace("`", "'").replace("\n", " ")


def report(res: Result, model: str) -> str:
    score = ""
    if res.mutants:
        caught = sum(bool(f) for f in res.mutants.values())
        score = f"**{caught} of {len(res.mutants)} mutants caught ({caught / len(res.mutants):.0%})**"
    lines = [
        "## 🤖 testgen-agent report",
        "",
        f"Model `{model}` · {res.operations} operations · {res.proposed} tests proposed",
        "",
        "| Outcome | Count | Meaning |",
        "|---|--:|---|",
        f"| ✅ Kept | {len(res.kept)} | passed 3 runs in a row; written to disk |",
        f"| ❓ Needs review | {len(res.review)} | failed every run: a real bug **or** a wrong expectation |",
        f"| 〰️ Flaky | {len(res.flaky)} | passed sometimes; dropped |",
        f"| ✗ Invalid | {len(res.invalid)} | broke the rules (unknown endpoint, undocumented status...); dropped |",
        f"| = Duplicate | {len(res.duplicates)} | same request and checks as another test; dropped |",
        f"| ⚠️ Weak | {len(res.weak)} | kept, but only checks a success status code |",
        "",
    ]
    if res.pytest_ok is not None:
        lines += [f"Generated pytest suite re-run against the API: **{'passes' if res.pytest_ok else 'FAILS'}**", ""]
    if res.mutants:
        lines += [f"### Mutation score: {score}", "", "| Mutant | Result | Caught by |", "|---|---|---|"]
        for name, failing in res.mutants.items():
            lines.append(
                f"| {md_escape(name)} | {'✅ caught' if failing else '❌ survived'} | {md_escape(', '.join(failing[:3])) or '-'} |"
            )
        lines += ["", "A surviving mutant is a bug the generated tests can't see: a gap to fill by hand.", ""]
    if res.review:
        lines += ["### ❓ Needs human review", "", "| Test | Request | Problem |", "|---|---|---|"]
        for f in res.review:
            req = f"{f.case.method} {f.case.path} {json.dumps(f.case.query) if f.case.query else ''}".strip()
            lines.append(f"| `{f.case.name}` | {md_escape(req)} | {md_escape('; '.join(f.problems))} |")
        lines.append("")
    if res.invalid:
        lines += (
            ["### ✗ Rejected proposals", ""] + [f"- `{md_escape(n)}`: {md_escape(r)}" for n, r in res.invalid] + [""]
        )
    if res.weak:
        lines += ["### ⚠️ Weak tests (status code only)", ""] + [f"- `{c.name}`" for c in res.weak] + [""]
    if res.planner_errors:
        lines += ["### Planner errors", ""] + [f"- {md_escape(e)}" for e in res.planner_errors] + [""]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="testgen", description="Generate API tests from an OpenAPI spec, verified against the live API."
    )
    p.add_argument("--spec", required=True, help="OpenAPI JSON file or URL")
    p.add_argument("--base-url", required=True, help="the running API to verify tests against")
    p.add_argument("--out", default="generated_tests")
    p.add_argument("--provider", choices=["ollama", "fake"], default="ollama")
    p.add_argument("--model", default="qwen2.5-coder:14b")
    p.add_argument("--ollama-url", default=os.getenv("OLLAMA_URL", "http://localhost:11434"))
    p.add_argument("--per-op", type=int, default=8, help="tests to ask for per operation")
    p.add_argument("--runs", type=int, default=3, help="times each test must pass before it is kept")
    p.add_argument("--only", action="append", help="operationId to include (repeatable)")
    p.add_argument(
        "--mutant", action="append", default=[], metavar="NAME=URL", help="a buggy copy of the API to score against"
    )
    p.add_argument("--min-mutation-score", type=float, help="exit 1 if fewer mutants are caught (0-1)")
    p.add_argument("--md", help="write the report here")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = p.parse_args(argv)

    try:
        ops = operations(load_spec(args.spec), args.only)
    except Exception as exc:  # noqa: BLE001 - any spec problem is a usage error
        print(f"testgen: can't read spec: {exc}", file=sys.stderr)
        return 2
    if not ops:
        print("testgen: no operations found", file=sys.stderr)
        return 2
    mutants = {}
    for m in args.mutant:
        if "=" not in m:
            print(f"testgen: --mutant must be NAME=URL, got {m!r}", file=sys.stderr)
            return 2
        name, url = m.split("=", 1)
        mutants[name] = url

    planner = FakePlanner() if args.provider == "fake" else OllamaPlanner(args.model, args.ollama_url)
    log = lambda m: print(m, file=sys.stderr)  # noqa: E731
    res = generate(ops, planner, args.base_url, args.per_op, args.runs, log)
    res = finish(res, args.out, args.base_url, mutants, log)

    text = report(res, planner.model)
    if args.md:
        Path(args.md).write_text(text, encoding="utf-8")
        if os.getenv("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
                fh.write(text)
    else:
        print(text)

    if res.pytest_ok is False:
        print("testgen: generated tests failed when re-run - not trustworthy", file=sys.stderr)
        return 1
    if args.min_mutation_score is not None and mutants:
        score = sum(bool(f) for f in res.mutants.values()) / len(mutants)
        if score < args.min_mutation_score:
            print(f"testgen: mutation score {score:.0%} < {args.min_mutation_score:.0%}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
