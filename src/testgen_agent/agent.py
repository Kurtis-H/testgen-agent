"""The agent loop: propose -> validate -> dedupe -> run 3x -> keep, drop or flag for review."""

from __future__ import annotations

import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from testgen_agent.cases import InvalidCase, TestCase, parse_proposals, validate
from testgen_agent.executor import execute
from testgen_agent.render import write_tests
from testgen_agent.spec import Operation


@dataclass
class Flagged:
    case: TestCase
    problems: list[str]


@dataclass
class Result:
    operations: int = 0
    proposed: int = 0
    invalid: list[tuple[str, str]] = field(default_factory=list)  # (name, reason)
    duplicates: list[str] = field(default_factory=list)
    kept: list[TestCase] = field(default_factory=list)
    flaky: list[Flagged] = field(default_factory=list)
    review: list[Flagged] = field(default_factory=list)  # failed every run: a bug, or a wrong expectation
    planner_errors: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    pytest_ok: bool | None = None
    mutants: dict[str, list[str]] = field(default_factory=dict)  # mutant -> failing tests (empty = survived)

    @property
    def weak(self) -> list[TestCase]:
        """Kept tests that only check a success status code - they prove little."""
        return [c for c in self.kept if not c.expect_json and c.expect_status < 400]


def generate(
    ops: list[Operation],
    planner,
    base_url: str,
    per_op: int = 8,
    runs: int = 3,
    log=lambda _: None,
) -> Result:
    res = Result(operations=len(ops))
    seen: set[str] = set()
    with httpx.Client(base_url=base_url, timeout=15) as client:
        for op in ops:
            log(f"{op.method} {op.path}")
            try:
                proposals = parse_proposals(planner.propose(op, per_op, []))
            except (InvalidCase, httpx.HTTPError, KeyError) as exc:
                res.planner_errors.append(f"{op.operation_id}: {exc}")
                continue
            for raw in proposals:
                res.proposed += 1
                name = str(raw.get("name", "?")) if isinstance(raw, dict) else "?"
                try:
                    case = validate(raw, op)
                except InvalidCase as exc:
                    res.invalid.append((name, str(exc)))
                    log(f"  ✗ invalid   {name}: {exc}")
                    continue
                if case.key() in seen:
                    res.duplicates.append(case.name)
                    log(f"  = duplicate {case.name}")
                    continue
                seen.add(case.key())

                outcomes = [execute(case, client) for _ in range(runs)]
                passes = sum(o.passed for o in outcomes)
                if passes == runs:
                    res.kept.append(case)
                    log(f"  ✓ kept      {case.name}")
                elif passes == 0:
                    # Never "fix" the expectation to match the API: if the API is wrong,
                    # that would turn a bug into a passing test. A human decides.
                    res.review.append(Flagged(case, outcomes[0].problems))
                    log(f"  ? review    {case.name}: {'; '.join(outcomes[0].problems)}")
                else:
                    res.flaky.append(Flagged(case, [f"passed {passes}/{runs} runs"]))
                    log(f"  ~ flaky     {case.name}")
    return res


def run_pytest(out_dir: str, api_url: str) -> tuple[bool, list[str]]:
    """Run the generated files; return (all passed, failing test names)."""
    junit = Path(out_dir) / ".junit.xml"
    env = {**os.environ, "API_URL": api_url}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", out_dir, "-q", "-p", "no:cacheprovider", f"--junitxml={junit}"],
        env=env,
        capture_output=True,
        text=True,
        cwd=out_dir,
    )
    failing = []
    if junit.exists():
        for tc in ET.parse(junit).getroot().iter("testcase"):
            if tc.find("failure") is not None or tc.find("error") is not None:
                failing.append(tc.get("name", "?"))
        junit.unlink()
    return proc.returncode == 0, failing


def finish(res: Result, out_dir: str, base_url: str, mutants: dict[str, str], log=lambda _: None) -> Result:
    res.files = [str(p) for p in write_tests(res.kept, out_dir)]
    if not res.kept:
        return res
    res.pytest_ok, failing = run_pytest(out_dir, base_url)
    log(f"generated suite against {base_url}: {'pass' if res.pytest_ok else 'FAIL ' + ', '.join(failing)}")
    for name, url in mutants.items():
        _, failing = run_pytest(out_dir, url)
        res.mutants[name] = failing
        log(f"mutant {name}: {'caught by ' + ', '.join(failing) if failing else 'SURVIVED'}")
    return res
