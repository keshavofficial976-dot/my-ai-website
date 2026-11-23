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

def parse_loose_json(raw_text: str) -> dict:
    txt = raw_text.strip()
    s = txt.find("{")
    e = txt.rfind("}")
    if s == -1 or e == -1:
        raise ValueError("No JSON object in response")
    return json.loads(txt[s:e + 1], strict=False)

def call_groq_fast(prompt: str) -> tuple[dict, str]:
    gk = os.getenv("GROQ_API_KEY", "").strip()
    if not gk:
        raise HTTPException(status_code=503, detail="GROQ_API_KEY missing in Render")
    headers = {"Authorization": f"Bearer {gk}", "Content-Type": "application/json", "User-Agent": "AI-Studio/1.0"}
    endpoint = urlunparse(("https", "api." + "groq.com", "/openai/v1/chat/completions", "", "", ""))
    last_err = None
    for m in ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]:
        payload = {
            "model": m,
            "max_tokens": 3500,
            "messages": [
                {"role": "system", "content": "Return ONLY a valid raw JSON object matching the requested keys. No markdown."},
                {"role": "user", "content": prompt}
            ],
        }
        req = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
                content = raw.get("choices").pop(0).get("message").get("content")
                return parse_loose_json(content), f"Maker: Groq {m} | Verified"
        except urllib.error.HTTPError as he:
            err_txt = he.read().decode("utf-8", errors="ignore")[:150]
            last_err = f"{m} HTTP {he.code}: {err_txt}"
        except Exception as e:
            last_err = f"{m}: {e}"
    raise HTTPException(status_code=503, detail=f"Groq diagnostic: {last_err}")

def ask_ai(prompt: str, schema_cls, img_data: str = "", img_mime: str = "") -> tuple[dict, str]:
    if not img_data:
        return call_groq_fast(prompt)
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not gemini_key:
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY missing")
    client = genai.Client(api_key=gemini_key)
    contents = [prompt, types.Part.from_bytes(data=base64.b64decode(img_data), mime_type=img_mime)]
    try:
        res = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=contents,
            config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema_cls),
        )
        return parse_loose_json(res.text), "Maker: Gemini 3.5 Flash-Lite | Verified"
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Gemini Vision error: {e}")

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
        "Anticipate unspoken needs: when creating a Windows desktop assistant on cloud, write a complete standalone Python GUI script via write_file that auto-installs any missing pip packages at startup and ALWAYS opens a visible tkinter window with root.mainloop() so double-clicking it on Windows never flashes and closes. "
        "Return ONLY a JSON object with keys 'reply' (string) and 'steps' (list of objects each with 'action' in ['write_file', 'run_cmd', 'open_app', 'type_text'], 'target', 'content').\n"
        f"History:\n{hist}\nUser: {req.task}"
    )
    data, badge = ask_ai(prompt, AgentOut)
    return {"reply": data.get("reply", "Plan ready."), "steps": data.get("steps", []), "checker": badge}

@app.post("/api/agent/execute")
def exec_step(step: StepReq):
    try:
        if step.action == "write_file":
            safe_target = os.path.basename(step.target)
            with open(safe_target, "w", encoding="utf-8") as f:
                f.write(step.content)
            return {"ok": True, "output": f"Saved {safe_target}"}
        if step.action == "run_cmd":
            if os.name == "nt" and step.target.startswith("python "):
                subprocess.Popen(step.target, shell=True)
                return {"ok": True, "output": f"Launched {step.target}"}
            p = subprocess.run(step.target, shell=True, capture_output=True, text=True, timeout=15)
            return {"ok": p.returncode == 0, "output": (p.stdout + p.stderr).strip()[:1000] or "Done"}
        if step.action == "open_app" and os.name == "nt":
            os.system(f'start "" "{step.target}"')
            return {"ok": True, "output": f"Opened {step.target}"}
        return {"ok": False, "output": "Download the generated .py file using the top-bar button to run desktop actions on Windows."}
    except Exception as e:
        return {"ok": False, "output": str(e)}
