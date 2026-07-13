import os
import uuid
import json
import asyncio
import httpx
from bs4 import BeautifulSoup
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Request, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from dotenv import load_dotenv
from pydantic import BaseModel
import litellm

from database import engine, Base, SessionLocal, get_db
from models import Setting, ExecutionLog

load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

async def fetch_website(url: str) -> str:
    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Remove scripts and styles to reduce token count
        for script in soup(["script", "style"]):
            script.extract()
            
        return soup.prettify()[:15000] # Cap at ~15k chars for prompt safety

async def process_optimizer_job(task_id: str, target_url: str, model_id: str, api_keys: dict):
    async with SessionLocal() as db:
        try:
            result = await db.execute(select(ExecutionLog).where(ExecutionLog.task_id == task_id))
            log = result.scalar_one()
            log.status = "running"
            await db.commit()
            
            # Scrape website
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
            
            # Determine correct api_key and base_url based on litellm model string
            api_key = None
            api_base = None
            
            if model_id.startswith("gpt"):
                api_key = api_keys.get("openai") or os.getenv("OPENAI_API_KEY")
            elif model_id.startswith("claude"):
                api_key = api_keys.get("anthropic") or os.getenv("ANTHROPIC_API_KEY")
            elif model_id.startswith("gemini"):
                api_key = api_keys.get("gemini") or os.getenv("GEMINI_API_KEY")
            elif model_id.startswith("zhipu"):
                api_key = api_keys.get("glm") or os.getenv("ZHIPUAI_API_KEY")
            elif model_id.startswith("ollama"):
                api_base = "http://localhost:11434"
                
            if not api_key and not api_base:
                raise Exception(f"No API key provided for {model_id}")

            response = await litellm.acompletion(
                model=model_id,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                api_key=api_key,
                api_base=api_base,
                max_tokens=2000
            )
            
            raw_text = response.choices[0].message.content.strip()
            if raw_text.startswith("```json"):
                raw_text = raw_text[7:]
            if raw_text.startswith("```"):
                raw_text = raw_text[3:]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3]
                
            report_data = json.loads(raw_text)
            
            log.report_json = json.dumps(report_data)
            log.status = "success"
            await db.commit()
            
        except Exception as e:
            print(f"Error processing job: {e}")
            result = await db.execute(select(ExecutionLog).where(ExecutionLog.task_id == task_id))
            log = result.scalar_one_or_none()
            if log:
                log.status = "error"
                log.report_json = json.dumps({"error": str(e)})
                await db.commit()

class ExecuteRequest(BaseModel):
    target_url: str
    model_id: str

@app.post("/api/execute")
async def enqueue_task(req: ExecuteRequest, background_tasks: BackgroundTasks, request: Request, db: AsyncSession = Depends(get_db)):
    task_id = str(uuid.uuid4())
    
    log = ExecutionLog(
        task_id=task_id,
        target_url=req.target_url,
        model_provider=req.model_id,
        status="pending"
    )
    db.add(log)
    await db.commit()
    
    api_keys = {
        "openai": request.headers.get("X-OpenAI-Key"),
        "anthropic": request.headers.get("X-Anthropic-Key"),
        "gemini": request.headers.get("X-Gemini-Key"),
        "glm": request.headers.get("X-GLM-Key")
    }
    
    background_tasks.add_task(process_optimizer_job, task_id, req.target_url, req.model_id, api_keys)
    
    return {"status": "success", "task_id": task_id}

@app.get("/api/tasks/{task_id}")
async def get_task_status(task_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ExecutionLog).where(ExecutionLog.task_id == task_id))
    log = result.scalar_one_or_none()
    
    if not log:
        raise HTTPException(status_code=404, detail="Task not found")
        
    report_data = None
    if log.report_json:
        try:
            report_data = json.loads(log.report_json)
        except:
            pass
            
    return {
        "status": log.status,
        "report": report_data
    }

class ApiKeysUpdate(BaseModel):
    openai_api_key: str = None
    anthropic_api_key: str = None
    gemini_api_key: str = None
    glm_api_key: str = None

@app.post("/api/settings/keys")
async def update_keys(req: ApiKeysUpdate, db: AsyncSession = Depends(get_db)):
    keys = {
        "openai_api_key": req.openai_api_key,
        "anthropic_api_key": req.anthropic_api_key,
        "gemini_api_key": req.gemini_api_key,
        "glm_api_key": req.glm_api_key
    }
    
    for k, v in keys.items():
        if v:
            res = await db.execute(select(Setting).where(Setting.key == k))
            setting = res.scalar_one_or_none()
            if setting:
                setting.value = v
            else:
                db.add(Setting(key=k, value=v))
            await db.commit()
            
    return {"status": "success"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8010, reload=True)
