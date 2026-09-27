"""Smoke test: boots the real app and hits the main endpoints.

Run:  cd backend && API_KEY=test-key DATABASE_URL=sqlite+aiosqlite:////tmp/smoke.db python smoke_test.py

Uses dummy credentials only. The audit job is submitted with a target that
fails fast (connection refused), so the background task errors out quickly
and never calls a real LLM.
"""
import os
import sys
import time

os.environ.setdefault("API_KEY", "smoke-test-key")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////tmp/optimizer_smoke.db")

# Fresh DB per run
_db = "/tmp/optimizer_smoke.db"
for suffix in ("", "-shm", "-wal", "-journal"):
    try:
        os.remove(_db + suffix)
    except FileNotFoundError:
        pass

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server

# Context manager runs app lifespan (creates DB tables).
client = TestClient(server.app)
client.__enter__()
GOOD = {"X-API-Key": "smoke-test-key"}
checks = []


def check(name, cond, detail=""):
    checks.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), "-", name, detail)


# 1. /health is public and returns 200
r = client.get("/health")
check("GET /health -> 200", r.status_code == 200, str(r.json()))
check("health body has status ok", r.json().get("status") == "ok")

# 2. Auth: protected endpoints reject missing/wrong key
r = client.post("/api/execute", json={"target_url": "https://example.com", "model_id": "gpt-4o"})
check("POST /api/execute without key -> 401", r.status_code == 401)
r = client.post("/api/execute", json={"target_url": "https://example.com", "model_id": "gpt-4o"},
                headers={"X-API-Key": "wrong"})
check("POST /api/execute with wrong key -> 401", r.status_code == 401)
r = client.get("/api/tasks/nope", headers={"X-API-Key": "wrong"})
check("GET /api/tasks/x with wrong key -> 401", r.status_code == 401)

# 3. Input validation: bad URL / bad model rejected with structured JSON
r = client.post("/api/execute", json={"target_url": "not-a-url", "model_id": "gpt-4o"}, headers=GOOD)
check("invalid URL -> 422", r.status_code == 422)
check("422 body is structured JSON", isinstance(r.json().get("code"), str), str(r.json())[:80])
r = client.post("/api/execute", json={"target_url": "https://example.com", "model_id": "evil-model"},
                headers=GOOD)
check("disallowed model_id -> 422", r.status_code == 422)
r = client.post("/api/execute", json={"target_url": "http://localhost:8010/admin", "model_id": "gpt-4o"},
                headers=GOOD)
check("private/loopback target rejected (SSRF guard) -> 422", r.status_code == 422)

# 4. Valid submit -> 202 accepted with task_id (public target; no provider key
# configured, so the background job fails fast and never calls a real LLM)
# Sandbox DNS resolves everything to a proxy IP, so relax the SSRF guard here
# only; the guard itself was verified by the loopback-rejection check above.
os.environ["DISABLE_SSRF_GUARD"] = "1"
r = client.post("/api/execute",
                json={"target_url": "https://example.com", "model_id": "gpt-4o"}, headers=GOOD)
ok = r.status_code == 202 and "task_id" in r.json()
check("valid submit -> 202 + task_id", ok, str(r.status_code))
task_id = r.json().get("task_id") if ok else None

# 5. Unknown task -> 404 structured JSON
r = client.get("/api/tasks/does-not-exist", headers=GOOD)
check("unknown task -> 404", r.status_code == 404)
check("404 body structured, no stack trace", "stack" not in r.text.lower().replace("stacktrace", "x")
      and r.json().get("code") == "not_found")

# 6. Task status endpoint returns the created task (background job may have failed by now — fine)
if task_id:
    time.sleep(2)  # let the background job attempt/fail
    r = client.get(f"/api/tasks/{task_id}", headers=GOOD)
    body = r.json()
    check("task status readable", r.status_code == 200 and body.get("status") in
          ("pending", "running", "success", "error"), str(body.get("status")))
    check("no stack trace leaked in report", "Traceback" not in str(body.get("report")))

# 7. CORS is not wide-open
cors = [v for k, v in client.get("/health").headers.items() if k.lower() == "access-control-allow-origin"]
check("no wildcard CORS on public endpoint", "*" not in cors)

failed = [n for n, c in checks if not c]
print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed.")
sys.exit(1 if failed else 0)
