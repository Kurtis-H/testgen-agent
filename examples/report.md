## 🤖 testgen-agent report

Model `fake-planner` · 4 operations · 17 tests proposed

| Outcome | Count | Meaning |
|---|--:|---|
| ✅ Kept | 12 | passed 3 runs in a row; written to disk |
| ❓ Needs review | 2 | failed every run: a real bug **or** a wrong expectation |
| 〰️ Flaky | 0 | passed sometimes; dropped |
| ✗ Invalid | 2 | broke the rules (unknown endpoint, undocumented status...); dropped |
| = Duplicate | 1 | same request and checks as another test; dropped |
| ⚠️ Weak | 0 | kept, but only checks a success status code |

Generated pytest suite re-run against the API: **passes**

### Mutation score: **4 of 5 mutants caught (80%)**

| Mutant | Result | Caught by |
|---|---|---|
| TABLE-2 | ✅ caught | test_first_page, test_last_partial_page |
| TABLE-3 | ✅ caught | test_search_is_case_insensitive |
| TABLE-4 | ✅ caught | test_page_zero_rejected, test_size_above_max_rejected |
| SIGNUP-1 | ✅ caught | test_invalid_email_rejected |
| SIGNUP-2 | ❌ survived | - |

A surviving mutant is a bug the generated tests can't see: a gap to fill by hand.

### ❓ Needs human review

| Test | Request | Problem |
|---|---|---|
| `ten_per_page` | GET /api/products {"size": 10} | totalPages: got 3, expected 2 |
| `creates_account` | POST /api/signup | status 400, expected 201 |

### ✗ Rejected proposals

- `sorted endpoint`: GET /api/products/sorted is not this operation (GET /api/products)
- `unknown status`: spec documents no 418 response for searchFruit

