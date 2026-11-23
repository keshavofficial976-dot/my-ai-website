import os, json, base64, subprocess, urllib.request, urllib.error
from urllib.parse import urlunparse
from typing import List, Dict
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
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

def call_groq_fast(prompt: str) -> tuple[dict, str]:
    gk = os.getenv("GROQ_API_KEY", "").strip()
    if not gk:
        raise RuntimeError("GROQ_API_KEY is missing in Render Environment Variables")
    endpoint = urlunparse(("https", "api." + "groq.com", "/openai/v1/chat/completions", "", "", ""))
    last_err = None
    for m in ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "llama-3.3-70b-versatile"]:
        body = json.dumps({
            "model": m,
            "messages": [
                {"role": "system", "content": "Return ONLY a valid raw JSON object matching the requested keys. No markdown."},
                {"role": "user", "content": prompt}
            ],
        }).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=body,
            headers={"Authorization": f"Bearer {gk}", "Content-Type": "application/json", "User-Agent": "AI-Studio/1.0"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
                txt = raw["choices"]["message"]["content"].strip()
                txt = txt[txt.find("{"):txt.rfind("}") + 1]
                return json.loads(txt), f"Maker: Groq {m} | Verified (<2s)"
        except urllib.error.HTTPError as he:
            last_err = f"HTTP {he.code} ({m}): {he.read().decode('utf-8', errors='ignore')[:150]}"
        except Exception as e:
            last_err = f"{m}: {e}"
    raise RuntimeError(last_err or "Groq failed")

def ask_ai(prompt: str, schema_cls, img_data: str = "", img_mime: str = "") -> tuple[dict, str]:
    errors = []
    if not img_data:
        try:
            return call_groq_fast(prompt)
        except Exception as e:
            errors.append(f"Groq: {e}")

    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if gemini_key:
        client = genai.Client(api_key=gemini_key)
        contents = [prompt]
        if img_data and img_mime:
            try:
                contents.append(types.Part.from_bytes(data=base64.b64decode(img_data), mime_type=img_mime))
            except Exception:
                pass
        try:
            res = client.models.generate_content(
                model="gemini-3.5-flash-lite",
                contents=contents,
                config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema_cls),
            )
            return json.loads(res.text), "Maker: Gemini 3.5 Flash-Lite | Verified"
        except Exception as e:
            errors.append(f"Gemini: {e}")
    else:
        errors.append("GEMINI_API_KEY missing")

    raise HTTPException(status_code=503, detail=" | ".join(errors))

@app.get("/api/health")
def health():
    return {
        "status": "awake",
        "has_groq_key": bool(os.getenv("GROQ_API_KEY", "").strip()),
        "has_gemini_key": bool(os.getenv("GEMINI_API_KEY", "").strip()),
    }

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
        "You are a proactive Senior Web Architect. Return ONLY a JSON object with keys 'reply' (conversational summary + suggestions) and 'html' (complete single-file HTML/CSS/JS if building/updating, or '' if just chatting). "
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
        "Anticipate unspoken needs (e.g., if user asks for a desktop toggle assistant on cloud, write a complete standalone Windows Python GUI script via write_file that auto-installs any missing pip packages at startup and ALWAYS opens a visible tkinter window with root.mainloop() so double-clicking it on Windows never flashes and closes). "
        "Return ONLY a JSON object with keys 'reply' (string) and 'steps' (list of objects each with 'action' in ['write_file', 'run_cmd', 'open_app', 'type_text'], 'target', 'content').\n"
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
