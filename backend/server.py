"""AI Website Optimizer Agent — FastAPI backend.

Audit service: fetches a target website's HTML, sends it to a configured
LLM via LiteLLM for a Technical SEO + CRO audit, and stores the report.

Auth: all endpoints except /health require an API key in the
`X-API-Key` header. The key is set via the API_KEY env var (dummy value
is fine for local dev; use a strong random secret in production).

Every secret/key/URL is configured via environment variables — see
.env.example. Never hardcode real credentials.
"""
import os
import uuid
import json
import hmac
import ipaddress
import socket
import logging
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, Depends, Request, BackgroundTasks, HTTPException, Security
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from dotenv import load_dotenv
from pydantic import BaseModel, Field, HttpUrl, field_validator

import litellm

from database import engine, Base, SessionLocal, get_db
from models import ExecutionLog

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("optimizer")

# ---------------------------------------------------------------------------
# Configuration (all from env — no hardcoded secrets, keys, or URLs)
# ---------------------------------------------------------------------------
API_KEY: Optional[str] = os.getenv("API_KEY")  # shared secret for the X-API-Key header
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
CORS_ORIGINS: list[str] = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()]
MAX_HTML_CHARS: int = int(os.getenv("MAX_HTML_CHARS", "15000"))
FETCH_TIMEOUT: float = float(os.getenv("FETCH_TIMEOUT_SECONDS", "15"))
LITELLM_MAX_TOKENS: int = int(os.getenv("LITELLM_MAX_TOKENS", "2000"))

# Model prefixes this service will route through LiteLLM.
ALLOWED_MODEL_PREFIXES = ("gpt-", "claude-", "gemini/", "zhipu/", "ollama/")

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(provided: Optional[str] = Security(api_key_header)) -> str:
    """Require the shared-secret API key on all non-public endpoints."""
    if not API_KEY:
        # Misconfigured server: fail closed rather than serving unauthenticated.
        raise HTTPException(status_code=503, detail={"code": "server_misconfigured",
                                                    "message": "API_KEY is not set on the server."})
    if not provided or not hmac.compare_digest(provided, API_KEY):
        raise HTTPException(status_code=401, detail={"code": "unauthorized",
                                                    "message": "Missing or invalid X-API-Key header."})
    return provided


# ---------------------------------------------------------------------------
# App + middleware
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(
    title="Website Optimizer Agent",
    description="AI-powered Technical SEO & CRO audit service.",
    version="1.1.0",
    lifespan=lifespan,
)

# Sane CORS: explicit allowlist only, no wildcard + credentials combo.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key", "X-OpenAI-Key", "X-Anthropic-Key",
                   "X-Gemini-Key", "X-GLM-Key"],
)


# ---------------------------------------------------------------------------
# Error handling — structured JSON, never leak stack traces to clients
# ---------------------------------------------------------------------------
@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"code": "validation_error",
                 "message": "Request validation failed.",
                 "details": [{"field": ".".join(str(p) for p in e.get("loc", [])),
                              "error": e.get("msg")} for e in exc.errors()]},
    )


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content=detail)


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500,
                        content={"code": "internal_error",
                                 "message": "An unexpected error occurred. Check server logs."})


# ---------------------------------------------------------------------------
# Input validation helpers
# ---------------------------------------------------------------------------
def _assert_public_host(url: HttpUrl) -> None:
    """SSRF guard: refuse targets that resolve to private/loopback IPs.
    DISABLE_SSRF_GUARD=1 skips the check (dev/test only — never in production)."""
    if os.getenv("DISABLE_SSRF_GUARD") == "1":
        return
    host = url.host
    if not host:
        raise ValueError("URL must include a host.")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise ValueError(f"Could not resolve host '{host}'.") from e
    for info in infos:
        ip = info[4][0]
        try:
            parsed = ipaddress.ip_address(ip.split("%")[0])  # strip IPv6 zone id
        except ValueError:
            raise ValueError(f"Could not parse resolved IP for host '{host}'.")
        if parsed.is_private or parsed.is_loopback:
            raise ValueError(f"Target host '{host}' resolves to a non-public IP ({ip}).")


class ExecuteRequest(BaseModel):
    target_url: HttpUrl = Field(..., max_length=2048,
                                description="Public http(s) URL to audit.")
    model_id: str = Field(..., min_length=1, max_length=128,
                          description="LiteLLM model id, e.g. gpt-4o, claude-3-5-sonnet-20240620, "
                                      "gemini/gemini-1.5-pro, zhipu/glm-4, ollama/llama3.")

    @field_validator("target_url")
    @classmethod
    def _validate_target(cls, v: HttpUrl) -> HttpUrl:
        if v.scheme not in ("http", "https"):
            raise ValueError("target_url must use http or https.")
        _assert_public_host(v)
        return v

    @field_validator("model_id")
    @classmethod
    def _validate_model(cls, v: str) -> str:
        if not v.startswith(ALLOWED_MODEL_PREFIXES):
            raise ValueError(f"model_id must start with one of {ALLOWED_MODEL_PREFIXES}.")
        return v


# ---------------------------------------------------------------------------
# Public endpoints
# ---------------------------------------------------------------------------
@app.get("/health", tags=["public"])
async def health():
    return {"status": "ok", "service": "website-optimizer-agent", "version": "1.1.0"}


# ---------------------------------------------------------------------------
# Core job logic (real LLM via LiteLLM — no stubs, no mock fallbacks)
# ---------------------------------------------------------------------------
async def fetch_website(url: str) -> str:
    async with httpx.AsyncClient(follow_redirects=True, timeout=FETCH_TIMEOUT) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style"]):
            tag.extract()
        return soup.prettify()[:MAX_HTML_CHARS]


async def process_optimizer_job(task_id: str, target_url: str, model_id: str, api_keys: dict):
    async with SessionLocal() as db:
        try:
            result = await db.execute(select(ExecutionLog).where(ExecutionLog.task_id == task_id))
            db_log = result.scalar_one()
            db_log.status = "running"
            await db.commit()

            html_content = await fetch_website(target_url)

            system_prompt = (
                "You are an expert Technical SEO and Conversion Rate Optimization (CRO) AI. "
                "Analyze the provided HTML code of the website and output ONLY a raw JSON object containing your audit.\n"
                "JSON format:\n"
                "{\n"
                '  "seo_score": 85,\n'
                '  "cro_score": 70,\n'
                '  "critical_issues": ["Missing H1", "Slow load time suspected"],\n'
                '  "seo_recommendations": ["Add meta description", "Optimize alt tags"],\n'
                '  "cro_recommendations": ["Make CTA button larger", "Add social proof"]\n'
                "}\n"
                "Do NOT include markdown backticks around the JSON."
            )
            user_prompt = f"Target URL: {target_url}\n\nHTML Content:\n{html_content}"

            api_key: Optional[str] = None
            api_base: Optional[str] = None

            if model_id.startswith("gpt-"):
                api_key = api_keys.get("openai") or os.getenv("OPENAI_API_KEY")
            elif model_id.startswith("claude-"):
                api_key = api_keys.get("anthropic") or os.getenv("ANTHROPIC_API_KEY")
            elif model_id.startswith("gemini/"):
                api_key = api_keys.get("gemini") or os.getenv("GEMINI_API_KEY")
            elif model_id.startswith("zhipu/"):
                api_key = api_keys.get("glm") or os.getenv("ZHIPUAI_API_KEY")
            elif model_id.startswith("ollama/"):
                api_base = OLLAMA_BASE_URL

            if not api_key and not api_base:
                raise RuntimeError(f"No API key configured for model '{model_id}'. "
                                   "Set the provider key env var or pass it via the X-<Provider>-Key header.")

            response = await litellm.acompletion(
                model=model_id,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                api_key=api_key,
                api_base=api_base,
                max_tokens=LITELLM_MAX_TOKENS,
            )

            raw_text = response.choices[0].message.content.strip()
            for fence in ("```json", "```"):
                if raw_text.startswith(fence):
                    raw_text = raw_text[len(fence):]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3]

            try:
                report_data = json.loads(raw_text)
            except json.JSONDecodeError:
                raise RuntimeError("LLM did not return valid JSON; audit could not be parsed.")

            db_log.report_json = json.dumps(report_data)
            db_log.status = "success"
            await db.commit()

        except Exception as e:
            log.error("Error processing job %s: %s", task_id, e)
            result = await db.execute(select(ExecutionLog).where(ExecutionLog.task_id == task_id))
            db_log = result.scalar_one_or_none()
            if db_log:
                db_log.status = "error"
                # Generic message only — no stack traces leave the server.
                db_log.report_json = json.dumps({"code": "audit_failed",
                                                 "message": "The audit could not be completed."})
                await db.commit()


# ---------------------------------------------------------------------------
# Authenticated API
# ---------------------------------------------------------------------------
@app.post("/api/execute", status_code=202, tags=["audit"],
          dependencies=[Depends(verify_api_key)])
async def enqueue_task(req: ExecuteRequest, background_tasks: BackgroundTasks,
                       request: Request, db: AsyncSession = Depends(get_db)):
    task_id = str(uuid.uuid4())

    db_log = ExecutionLog(
        task_id=task_id,
        target_url=str(req.target_url),
        model_provider=req.model_id,
        status="pending",
    )
    db.add(db_log)
    await db.commit()

    # Per-request provider keys (also usable from env on the server).
    api_keys = {
        "openai": request.headers.get("X-OpenAI-Key"),
        "anthropic": request.headers.get("X-Anthropic-Key"),
        "gemini": request.headers.get("X-Gemini-Key"),
        "glm": request.headers.get("X-GLM-Key"),
    }

    background_tasks.add_task(process_optimizer_job, task_id, str(req.target_url), req.model_id, api_keys)
    return {"status": "accepted", "task_id": task_id}


@app.get("/api/tasks/{task_id}", tags=["audit"], dependencies=[Depends(verify_api_key)])
async def get_task_status(task_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ExecutionLog).where(ExecutionLog.task_id == task_id))
    db_log = result.scalar_one_or_none()

    if not db_log:
        raise HTTPException(status_code=404, detail={"code": "not_found",
                                                     "message": "Task not found."})

    report_data = None
    if db_log.report_json:
        try:
            report_data = json.loads(db_log.report_json)
        except json.JSONDecodeError:
            pass

    return {"status": db_log.status, "report": report_data}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8010"))
    uvicorn.run("server:app", host="0.0.0.0", port=port)
