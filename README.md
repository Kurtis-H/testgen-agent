# testgen-agent

![CI](https://github.com/Kurtis-H/testgen-agent/actions/workflows/ci.yml/badge.svg)

**An AI agent that writes API tests from an OpenAPI spec, and has to prove every one before it's kept.**

Point it at a spec and a running API. A local model proposes tests, and the agent validates them, runs each one three times against the live API, and writes only the ones that hold up. Then it measures whether those tests would actually catch bugs.

```
GET /api/products
  ✓ kept      first_page
  ✓ kept      search_is_case_insensitive
  ✓ kept      page_zero_rejected
  ? review    ten_per_page: totalPages: got 3, expected 2
  = duplicate first_page_again
  ✗ invalid   sorted endpoint: GET /api/products/sorted is not this operation
...
mutant TABLE-2: caught by test_first_page, test_last_partial_page
mutant SIGNUP-2: SURVIVED
```

See [`examples/report.md`](examples/report.md) for a full report and [`examples/generated/`](examples/generated/) for the tests it wrote in that run (scripted planner, against bug-hunt-lab).

## The guardrails

AI-written tests fail in predictable ways: they invent endpoints, assert things nobody knows, pass by luck, or quietly encode bugs as "expected". Each guardrail targets one of those.

| Guardrail | Why |
|---|---|
| **The model writes data, not code.** It proposes JSON (method, path, inputs, expected result). A fixed template renders that into pytest, with every value written as a Python literal. | Nothing the model produces is ever executed. A test whose body contains `os.system(...)` becomes a harmless string. There's a test for this. |
| **Validated against the spec** before anything runs | Invented endpoints, undocumented status codes, unknown parameters and unexpected fields are rejected. |
| **Must pass 3 runs in a row** to be kept | A test that passes once by luck isn't a test. Mixed results go to "flaky" and are dropped. |
| **Failing tests are never "fixed" to pass** | A test that fails every run means either the model guessed wrong or **the API has a bug**. Rewriting the expectation to match would turn a bug into a passing test, so these go to a human-review list. |
| **Duplicates dropped** | Same request and checks under a different name doesn't add coverage. |
| **Weak tests flagged** | A test that only checks for `200` proves little, and the report says so. |
| **The written suite is re-run with pytest** | Proves the rendered file actually works, not just the agent's internal checks. |
| **Mutation score** | Runs the kept tests against copies of the API with one known bug each. A bug that "survives" is a gap in coverage, and the report names it. |

### Why "generate against a known-good build" matters

The agent verifies tests against whatever API you point it at. Run it against an API that has a bug, and a test with a **wrong** expectation can pass, which locks in the bug. This really happens: against bug-hunt-lab's TABLE-2 bug (wrong page count), the tests that hit the bug went to review as they should, but a proposal that guessed the page count wrong was **kept**, because the buggy API agreed with it. That's why the README says to generate against a build you trust, and why every kept test still gets a human read before it's committed.

## Quickstart

Uses [bug-hunt-lab](https://github.com/Kurtis-H/bug-hunt-lab) as the API under test, since its bugs can be switched on individually to act as mutants.

```bash
pip install -e .
ollama pull qwen2.5-coder:14b

# In bug-hunt-lab: the correct API, plus a few one-bug copies
BUGS=none     PORT=3000 node app/server.mjs &
BUGS=TABLE-2  PORT=3001 node app/server.mjs &
BUGS=SIGNUP-2 PORT=3002 node app/server.mjs &

testgen --spec examples/bug-hunt-lab.openapi.json --base-url http://127.0.0.1:3000 \
  --out generated --md report.md \
  --mutant TABLE-2=http://127.0.0.1:3001 --mutant SIGNUP-2=http://127.0.0.1:3002
```

Works with any OpenAPI 3 JSON spec, from a file or a URL (`--spec http://localhost:8001/openapi.json` for vuln-shop).

No GPU? `--provider fake` uses a scripted planner that makes realistic mistakes on purpose (an invented endpoint, a duplicate, a wrong guess, a missing boundary test), so every guardrail gets exercised. CI runs exactly this against bug-hunt-lab with five mutants, and requires at least 80% of the mutants to be caught.

## Results with the scripted planner

- 17 proposed → **12 kept**, 2 for review, 2 invalid, 1 duplicate
- Generated suite re-runs green
- **Mutation score 4/5.** The survivor is SIGNUP-2 (passwords of exactly 8 characters rejected), because no proposal tested the boundary. That's the gap to fill by hand, and the reason boundary-value analysis is still a person's job.

## Design decisions

- **JSON proposals over generated code.** It costs some flexibility (no custom setup steps yet) and buys safety plus easy review: each test is one readable record.
- **Spec-bounded.** Tests can only use what the spec documents. A model that has spotted something the spec doesn't say is a reason to fix the spec, not to bypass it.
- **Mutation score as the quality metric,** not test count or coverage. A hundred passing tests that catch no bugs score zero.
- **No auth yet.** The roadmap includes bearer tokens and multi-step flows (create, then read back).

## Roadmap

- [ ] Auth (login fixture + token) and multi-step flows
- [ ] Feed review-list items back to the model with the actual response, so it can explain whether it's a bug
- [ ] Run [flaky-hunter](https://github.com/Kurtis-H/flaky-hunter) over repeated runs of the generated suite

## License

MIT
