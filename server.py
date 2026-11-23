import os, json, base64, subprocess, requests
from typing import List, Dict
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
client = genai.Client()
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class ChatReq(BaseModel):
    message: str
    history: List[Dict[str, str]] = []
    current_code: str = ""
    owner_email: str = ""
    owner_whatsapp: str = ""
    image_data: str = ""
    image_mime: str = ""

class AgentReq(BaseModel):
    task: str
    history: List[Dict[str, str]] = []

class StepReq(BaseModel):
    action: str
    target: str = ""
    content: str = ""

class SiteOut(BaseModel):
    reply: str
    html: str = ""

class AgentStep(BaseModel):
    action: str
    target: str
    content: str = ""

class AgentOut(BaseModel):
    reply: str
    steps: List[AgentStep]

def groq_json(prompt: str) -> dict:
    gk = os.getenv("GROQ_API_KEY", "").strip()
    if not gk:
        raise RuntimeError("No GROQ_API_KEY")
    r = requests.post(
        "https://groq.com",
        headers={"Authorization": f"Bearer {gk}", "Content-Type": "application/json"},
        json={
            "model": "llama-3.3-70b-versatile",
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=20,
    )
    r.raise_for_status()
    return json.loads(r.json()["choices"][0]["message"]["content"])

def ask_ai(prompt: str, schema_cls, img_data: str = "", img_mime: str = "") -> tuple[dict, str]:
    contents = [prompt]
    if img_data and img_mime:
        try:
            contents.append(types.Part.from_bytes(data=base64.b64decode(img_data), mime_type=img_mime))
        except Exception:
            pass
    for m in ["gemini-2.5-flash", "gemini-2.5-flash-lite"]:
        try:
            res = client.models.generate_content(
                model=m,
                contents=contents,
                config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema_cls),
            )
            return json.loads(res.text), f"Maker: {m} | Checked"
        except Exception:
            continue
    return groq_json(prompt), "Maker: Groq Llama-3.3-70B | Checked"

@app.get("/")
def home():
    return FileResponse("index.html")

@app.get("/api/download/{filename}")
def download_file(filename: str):
    safe = os.path.basename(filename)
    if os.path.isfile(safe):
        return FileResponse(safe, filename=safe)
    raise HTTPException(status_code=404, detail="File not found")

@app.post("/api/generate")
def generate_site(req: ChatReq):
    hist = "\n".join(f"{m['role']}: {m['text']}" for m in req.history[-8:])
    prompt = (
        "You are a proactive Senior Web Architect. Return JSON with 'reply' (conversational summary + suggestions) and 'html' (complete single-file HTML/CSS/JS if building/updating, or '' if just chatting). "
        "All nav links must use #section-id anchors.\n"
        f"History:\n{hist}\nCurrent HTML:\n{req.current_code or '(none)'}\nUser: {req.message}"
    )
    data, badge = ask_ai(prompt, SiteOut, req.image_data, req.image_mime)
    return {"reply": data.get("reply", "Done!"), "html": data.get("html", ""), "checker": badge}

@app.post("/api/agent/plan")
def plan_agent(req: AgentReq):
    hist = "\n".join(f"{m['role']}: {m['text']}" for m in req.history[-8:])
    env = "Windows PC (open_app/type_text allowed)" if os.name == "nt" else "Cloud Linux (use write_file to create downloadable .py scripts for Windows; never call open_app or blocking input())"
    prompt = (
        f"You are an autonomous AI Agent & Backup Brain. Environment: {env}. "
        "Anticipate unspoken needs (e.g., if user asks for a desktop toggle assistant on cloud, write a complete standalone Python script via write_file). "
        "Return JSON with 'reply' and 'steps' (action in ['write_file', 'run_cmd', 'open_app', 'type_text'], target, content).\n"
        f"History:\n{hist}\nUser: {req.task}"
    )
    data, badge = ask_ai(prompt, AgentOut)
    return {"reply": data.get("reply", "Plan ready."), "steps": data.get("steps", []), "checker": badge}

@app.post("/api/agent/execute")
def exec_step(step: StepReq):
    try:
        if step.action == "write_file":
            with open(step.target, "w", encoding="utf-8") as f:
                f.write(step.content)
            if step.target.endswith(".py"):
                subprocess.run(["python", "-m", "py_compile", step.target], check=False)
            return {"ok": True, "output": f"Saved {step.target}"}
        if step.action == "run_cmd":
            if os.name == "nt" and step.target.startswith("python "):
                subprocess.Popen(step.target, shell=True)
                return {"ok": True, "output": f"Launched {step.target}"}
            p = subprocess.run(step.target, shell=True, capture_output=True, text=True, timeout=20)
            return {"ok": p.returncode == 0, "output": (p.stdout + p.stderr).strip()[:1000] or "Done"}
        if step.action == "open_app" and os.name == "nt":
            os.system(f'start "" "{step.target}"')
            return {"ok": True, "output": f"Opened {step.target}"}
        return {"ok": False, "output": "Download the generated .py file to run desktop actions on Windows."}
    except Exception as e:
        return {"ok": False, "output": str(e)}
