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
    image_data: str = ""
    image_mime: str = ""

class AgentReq(BaseModel):
    task: str
    history: List[Dict[str, str]] = []

class StepReq(BaseModel):
    action: str
    target: str = ""
    content: str = ""

def parse_json_safe(raw_text: str) -> dict:
    txt = raw_text.strip()
    s = txt.find("{")
    e = txt.rfind("}")
    if s == -1 or e == -1:
        raise ValueError("Model returned non-JSON output")
    return json.loads(txt[s:e + 1], strict=False)

def call_groq(prompt: str) -> tuple[dict, str]:
    gk = os.getenv("GROQ_API_KEY", "").strip()
    if not gk:
        raise RuntimeError("GROQ_API_KEY missing in Render")
    headers = {"Authorization": f"Bearer {gk}", "Content-Type": "application/json", "User-Agent": "AI-Studio/1.0"}
    endpoint = urlunparse(("https", "api." + "groq.com", "/openai/v1/chat/completions", "", "", ""))
    last_err = ""
    for m in ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]:
        payload = {
            "model": m,
            "max_tokens": 3500,
            "messages": [
                {"role": "system", "content": "Return ONLY a valid raw JSON object matching the requested keys. No markdown fences."},
                {"role": "user", "content": prompt}
            ],
        }
        req = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
                content = raw.get("choices").pop(0).get("message").get("content")
                return parse_json_safe(content), f"Groq {m}"
        except urllib.error.HTTPError as he:
            last_err = f"{m} HTTP {he.code}: {he.read().decode('utf-8', errors='ignore')[:120]}"
        except Exception as e:
            last_err = f"{m}: {e}"
    raise RuntimeError(last_err or "Groq failed")

def ask_ai(prompt: str, img_data: str = "", img_mime: str = "") -> tuple[dict, str]:
    errs = []
    if not img_data:
        try:
            return call_groq(prompt)
        except Exception as e:
            errs.append(f"Groq: {e}")

    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if gemini_key:
        client = genai.Client(api_key=gemini_key)
        contents = [prompt]
        if img_data and img_mime:
            try:
                contents.append(types.Part.from_bytes(data=base64.b64decode(img_data), mime_type=img_mime))
            except Exception:
                pass
        for m in ["gemini-3.5-flash-lite", "gemini-3.8-flash"]:
            try:
                res = client.models.generate_content(
                    model=m,
                    contents=contents,
                    config=types.GenerateContentConfig(response_mime_type="application/json"),
                )
                return parse_json_safe(res.text), f"Gemini {m}"
            except Exception as e:
                errs.append(f"{m}: {e}")
    else:
        errs.append("GEMINI_API_KEY missing")

    raise HTTPException(status_code=503, detail=" | ".join(errs))

@app.get("/api/health")
def health():
    return {"status": "awake", "groq": bool(os.getenv("GROQ_API_KEY", "").strip()), "gemini": bool(os.getenv("GEMINI_API_KEY", "").strip())}

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
    hist = "\n".join(f"{m['role']}: {m['text']}" for m in req.history[-6:])
    prompt = (
        "You are a Senior Web Architect. Return ONLY JSON with keys 'reply' (short helpful summary) and 'html' (complete single-file HTML/CSS/JS website, or '' if just answering a question). "
        "All nav links must use #section-id anchors.\n"
        f"History:\n{hist}\nCurrent HTML:\n{req.current_code or '(none)'}\nUser: {req.message}"
    )
    data, badge = ask_ai(prompt, req.image_data, req.image_mime)
    return {"reply": data.get("reply", "Done!"), "html": data.get("html", ""), "checker": badge}

@app.post("/api/agent/plan")
def plan_agent(req: AgentReq):
    hist = "\n".join(f"{m['role']}: {m['text']}" for m in req.history[-6:])
    env = "Windows PC" if os.name == "nt" else "Cloud Linux (always use write_file to generate standalone Windows Python tkinter GUI scripts that auto-install missing pip packages and call root.mainloop())"
    prompt = (
        f"You are an Autonomous AI Agent. Environment: {env}. "
        "Write top-level Python tkinter GUI code (do NOT hide execution behind uncalled functions) using ONLY built-in modules (tkinter, from tkinter import messagebox, os, subprocess, webbrowser, shutil, glob, urllib.request, json) and ending with root.mainloop(). Design a sleek dark-mode always-on-top floating toggle assistant with a Natural-Language Command Input box where typing ANY instruction (opening apps via subprocess.Popen('start ' + app, shell=True), opening websites via webbrowser.open, sorting files, or running tasks) sends the user's typed command via urllib.request POST to 'https://onrender.com' with JSON {'task': cmd} so the cloud AI generates exact Python code for complex tasks (like creating Excel files with openpyxl/csv, sorting folders, or automating apps) and executes the returned steps locally on Windows! IMPORTANT: In the generated desktop GUI script, NEVER pass raw natural-language sentences to os.startfile() or subprocess! Instead, when the user submits any command in the entry box, POST JSON {'task': cmd} via urllib.request to 'https://onrender.com' (in a background thread so UI never freezes), and execute the returned 'steps' locally on Windows (write_file -> open(target,'w').write(content), run_cmd -> subprocess.run(target, shell=True), open_app -> subprocess.Popen('start ' + target, shell=True))! Return ONLY JSON with keys 'reply' (string) and 'steps' (list of objects with 'action' in ['write_file', 'run_cmd', 'open_app'], 'target', 'content').\n"
        f"History:\n{hist}\nUser: {req.task}"
    )
    data, badge = ask_ai(prompt)
    return {"reply": data.get("reply", "Plan ready."), "steps": data.get("steps", []), "checker": badge}

@app.post("/api/agent/execute")
def exec_step(step: StepReq):
    try:
        if step.action == "write_file":
            safe = os.path.basename(step.target)
            code = step.content.replace("\u00d7", "x").replace("\u2013", "-").replace("\u2014", "-")
            if safe.endswith(".py"):
                if "def main(" in code and "main()" not in code.split("def main(", 1):
                    code += "\nmain()\n"
                indented = "\n".join("    " + line for line in code.splitlines())
                code = (
                    "import traceback\n"
                    "try:\n"
                    f"{indented}\n"
                    "except Exception:\n"
                    "    traceback.print_exc()\n"
                    "    input('Press Enter to close...')\n"
                )
            with open(safe, "w", encoding="utf-8") as f:
                f.write(code)
            return {"ok": True, "output": f"Saved {safe}"}
        if step.action == "run_cmd":
            if os.name == "nt" and step.target.startswith("python "):
                subprocess.Popen(step.target, shell=True)
                return {"ok": True, "output": f"Launched {step.target}"}
            p = subprocess.run(step.target, shell=True, capture_output=True, text=True, timeout=15)
            return {"ok": p.returncode == 0, "output": (p.stdout + p.stderr).strip()[:800] or "Done"}
        if step.action == "open_app" and os.name == "nt":
            os.system(f'start "" "{step.target}"')
            return {"ok": True, "output": f"Opened {step.target}"}
        return {"ok": False, "output": "Click Download in the top bar to run on Windows."}
    except Exception as e:
        return {"ok": False, "output": str(e)}
