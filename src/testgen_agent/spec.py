"""Read an OpenAPI 3 spec into the list of operations the agent writes tests for."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import httpx

METHODS = ("get", "post", "put", "patch", "delete")


@dataclass
class Operation:
    method: str  # upper case
    path: str
    operation_id: str
    summary: str
    parameters: list[dict] = field(default_factory=list)
    body_schema: dict | None = None
    responses: dict[str, str] = field(default_factory=dict)  # status -> description

    def describe(self) -> str:
        """Compact description for the model prompt."""
        lines = [f"{self.method} {self.path}  ({self.operation_id})", f"Summary: {self.summary}"]
        for p in self.parameters:
            schema = json.dumps(p.get("schema", {}))
            lines.append(f"- {p.get('in')} param '{p.get('name')}': {schema} {p.get('description', '')}".rstrip())
        if self.body_schema:
            lines.append(f"- JSON body schema: {json.dumps(self.body_schema)}")
        for status, desc in self.responses.items():
            lines.append(f"- responds {status}: {desc}")
        return "\n".join(lines)


def _resolve(node, root, depth=0):
    """Inline local $refs (a few levels deep) so the model sees real field names."""
    if depth > 4:
        return node
    if isinstance(node, dict):
        if "$ref" in node and node["$ref"].startswith("#/"):
            target = root
            for part in node["$ref"][2:].split("/"):
                target = target.get(part, {})
            return _resolve(target, root, depth + 1)
        return {k: _resolve(v, root, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve(v, root, depth + 1) for v in node]
    return node


def load_spec(source: str) -> dict:
    if source.startswith(("http://", "https://")):
        resp = httpx.get(source, timeout=30)
        resp.raise_for_status()
        return resp.json()
    return json.loads(Path(source).read_text(encoding="utf-8"))


def operations(spec: dict, only: list[str] | None = None) -> list[Operation]:
    ops = []
    for path, item in (spec.get("paths") or {}).items():
        for method in METHODS:
            op = item.get(method)
            if not op:
                continue
            op_id = op.get("operationId") or f"{method}_{path}"
            if only and op_id not in only:
                continue
            body = (((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema")
            ops.append(
                Operation(
                    method=method.upper(),
                    path=path,
                    operation_id=op_id,
                    summary=op.get("summary") or op.get("description") or "",
                    parameters=_resolve(op.get("parameters") or [], spec),
                    body_schema=_resolve(body, spec) if body else None,
                    responses={
                        str(k): (v or {}).get("description", "") for k, v in (op.get("responses") or {}).items()
                    },
                )
            )
    return ops
