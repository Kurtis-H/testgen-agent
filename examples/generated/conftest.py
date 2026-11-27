"""Shared helpers for generated API tests. Set API_URL to point at the server."""

import os
import uuid

import httpx
import pytest


@pytest.fixture(scope="session")
def client():
    with httpx.Client(base_url=os.environ.get("API_URL", "http://127.0.0.1:3000"), timeout=15) as c:
        yield c


def fill(value):
    if isinstance(value, str):
        value = value.replace("{{unique_email}}", f"tg-{uuid.uuid4().hex[:10]}@example.com")
        return value.replace("{{unique_name}}", f"user-{uuid.uuid4().hex[:6]}")
    if isinstance(value, dict):
        return {k: fill(v) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v) for v in value]
    return value


def get_path(data, dotted):
    cur = data
    for part in dotted.split("."):
        if part == "length":
            cur = len(cur)
        elif isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur[part]
    return cur
