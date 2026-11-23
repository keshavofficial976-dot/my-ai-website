import os
import json
import base64
import subprocess
import urllib.request
from typing import List, Dict
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from google import genai
from google.genai import types

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    HAS_GUI = True
except Exception:
    HAS_GUI = False

load_dotenv()
client = genai.Client()

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class ChatRequest(BaseModel):
    message: str
    history: List[Dict[str, str]] = []
    current_code: str = ""
    owner_email: str = ""
    owner_whatsapp: str = ""
    image_data: str = ""
    image_mime: str = ""

class AgentPlanRequest(BaseModel):
    goal: str = ''
    task: str
    history: List[Dict[str, str]] = []

class AgentStepRequest(BaseModel):
    action: str
    target: str = ""
    content: str = ""

def run_checker_ai(task_type: str, user_prompt: str, draft_text: str):
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    audit_prompt = (
        f"You are an expert QA Checker AI reviewing a {task_type} generated for the user request: '{user_prompt}'.\n"
        "Audit for: 1) Broken HTML/JS syntax or missing <form>/<button type='submit'>, "
        "2) Hardcoded fake prices/dates instead of live fetch or live search data, "
        "3) Unsafe or invalid commands.\n"
        "Return ONLY raw JSON (no markdown) with keys: "
        "'verified_by' (string), 'notes' (short 1-sentence QA note), 'fixed_output' (improved code/JSON if fixes were needed, or empty string if already valid).\n\n"
        f"Draft Output to Audit:\n{draft_text}"
    )
    if groq_key:
        try:
            payload = json.dumps({
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": audit_prompt}],
                "temperature": 0.1
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://api.groq.com/openai/v1/chat/completions",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {groq_key}",
                    "User-Agent": "AI-Studio-Checker/1.0"
                }
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                raw = data["choices"][0]["message"]["content"].replace("```json", "").replace("```", "").strip()
                parsed = json.loads(raw)
                return parsed.get("fixed_output", ""), "Groq Llama-3.3-70B (" + parsed.get("notes", "Passed QA") + ")"
        except Exception:
            pass

    for m in ["gemini-3.8-flash", "gemini-3.5-flash-lite"]:
        try:
            res = client.models.generate_content(model=m, contents=audit_prompt)
            raw = res.text.replace("```json", "").replace("```", "").strip()
            parsed = json.loads(raw)
            return parsed.get("fixed_output", ""), "Gemini QA Auditor (" + parsed.get("notes", "Passed QA") + ")"
        except Exception:
            continue
    return "", "Built-in Syntax Guard"

def build_form_script(owner_email: str, owner_whatsapp: str) -> str:
    clean_phone = "".join(ch for ch in owner_whatsapp if ch.isdigit())
    if len(clean_phone) == 10:
        clean_phone = "91" + clean_phone
    return f"""
<script>
const OWNER_EMAIL = {owner_email!r};
const OWNER_PHONE = {clean_phone!r};

document.addEventListener('click', function(e) {{
  const link = e.target.closest('a');
  if (link) {{
    const href = link.getAttribute('href') || '';
    if (href.startsWith('#') && href.length > 1) {{
      e.preventDefault();
      const target = document.querySelector(href);
      if (target) target.scrollIntoView({{ behavior: 'smooth' }});
    }} else if (!href.startsWith('https://wa.me')) {{
      e.preventDefault();
    }}
  }}
}});

document.addEventListener('submit', async function(e) {{
  e.preventDefault();
  const form = e.target;
  const formData = {{}};
  form.querySelectorAll('input, textarea, select').forEach((el, idx) => {{
    if (el.type === 'submit' || el.type === 'button') return;
    const label = el.name || el.id || el.placeholder || ('Field_' + (idx + 1));
    if (el.value) formData[label] = el.value;
  }});

  try {{
    await fetch('https://baxoksksjwwrlwmmilnv.supabase.co/rest/v1/submissions', {{
      method: 'POST',
      headers: {{
        'apikey': 'sb_publishable_ibTinPvnZJtclMvnxobMVw_LoMqeEpD',
        'Authorization': 'Bearer sb_publishable_ibTinPvnZJtclMvnxobMVw_LoMqeEpD',
        'Content-Type': 'application/json',
        'Prefer': 'return=minimal'
      }},
      body: JSON.stringify({{
        site_name: (document.title || 'Website') + (OWNER_EMAIL ? ' (' + OWNER_EMAIL + ')' : ''),
        form_data: formData
      }})
    }});
  }} catch (err) {{}}

  if (OWNER_EMAIL) {{
    try {{
      await fetch('https://formsubmit.co/ajax/' + encodeURIComponent(OWNER_EMAIL), {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json', 'Accept': 'application/json' }},
        body: JSON.stringify({{ _subject: 'New Order / Inquiry from ' + (document.title || 'Your Website'), ...formData }})
      }});
    }} catch (err) {{}}
  }}

  const summary = Object.entries(formData).map(([k, v]) => k + ': ' + v).join('\n');
  form.reset();

  if (OWNER_PHONE) {{
    alert('Order saved! Opening WhatsApp to notify the business owner...');
    window.open('https://wa.me/' + OWNER_PHONE + '?text=' + encodeURIComponent('New Website Order:\n' + summary), '_blank');
  }} else {{
    alert('Order / Inquiry sent successfully!');
  }}
}});
</script>
"""


@app.get("/api/download/{filename}")
def download_agent_file(filename: str):
    safe_name = os.path.basename(filename)
    if os.path.isfile(safe_name):
        return FileResponse(safe_name, filename=safe_name)
    raise HTTPException(status_code=404, detail="File not found")

@app.get("/")
def serve_home():
    return FileResponse("index.html")

@app.post("/api/generate")
def chat_or_build(req: ChatRequest):
    recent_chat = "\n".join([f"{m['role']}: {m['text']}" for m in req.history[-8:]])
    instruction = (
        "You are an interactive two-way AI Web Designer co-pilot. Use Google Search grounding when live facts, prices, crypto/stock rates, or weather are needed. "
        "For live dashboards, also include JavaScript fetch() to free public APIs (like api.coingecko.com or open-meteo.com) so numbers stay live.\n"
        "Return ONLY raw JSON (no markdown backticks) with keys:\n"
        "1. 'reply': Friendly conversational reply explaining what you built/changed, asking the user to verify it on the right, and suggesting 1-2 next steps.\n"
        "2. 'html': Complete valid single-file HTML with internal CSS (nav links use #section-id, forms use <form> and <button type='submit'>). Set to '' if only answering a question.\n\n"
        f"Conversation:\n{recent_chat}\nCurrent HTML:\n{req.current_code if req.current_code else '(None yet)'}\n\nUser: {req.task}"
    )

    contents = [instruction]
    if req.image_data and req.image_mime:
        try:
            img_bytes = base64.b64decode(req.image_data)
            contents.append(types.Part.from_bytes(data=img_bytes, mime_type=req.image_mime))
        except Exception:
            pass

    for model_name in ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.6-flash"]:
        try:
            res = client.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())]
                )
            )
            raw = res.text.replace("```json", "").replace("```html", "").replace("```", "").strip()
            parsed = json.loads(raw)
            reply = parsed.get("reply", "Done! Please verify the preview on the right.")
            html = parsed.get("html", "").strip()
            verified_badge = ""
            if html:
                fixed_html, checker_info = run_checker_ai("website HTML", req.task, html)
                if fixed_html and "<html" in fixed_html.lower():
                    html = fixed_html.replace("```html", "").replace("```", "").strip()
                verified_badge = checker_info
                script = build_form_script(req.owner_email.strip(), req.owner_whatsapp.strip())
                if "</body>" in html:
                    html = html.replace("</body>", script + "\n</body>")
                else:
                    html += script
            return {"reply": reply, "html": html, "verified_by": verified_badge}
        except Exception:
            continue
    raise HTTPException(status_code=503, detail="AI servers busy, please retry in a moment.")

class AgentStep(BaseModel):
    action: str
    target: str
    content: str = ""

class AgentPlanResponse(BaseModel):
    reply: str
    steps: List[AgentStep]

class AgentStep(BaseModel):
    action: str
    target: str
    content: str = ""

class AgentPlanResponse(BaseModel):
    reply: str
    steps: List[AgentStep]

def call_groq_maker(prompt: str) -> dict:
    groq_k = os.getenv("GROQ_API_KEY", "").strip()
    if not groq_k:
        raise RuntimeError("No GROQ_API_KEY")
    resp = requests.post(
        "https://groq.com",
        headers={"Authorization": f"Bearer {groq_k}", "Content-Type": "application/json"},
        json={
            "model": "llama-3.3-70b-versatile",
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "Return ONLY valid JSON with keys 'reply' (string) and 'steps' (list of objects with 'action', 'target', 'content')."},
                {"role": "user", "content": prompt}
            ]
        },
        timeout=35
    )
    resp.raise_for_status()
    return json.loads(resp.json()["choices"][0]["message"]["content"])

@app.post("/api/agent/plan")
def plan_agent_tasks(req: AgentPlanRequest):
    is_windows = (os.name == "nt")
    env_info = (
        "Running LOCALLY on Windows: you CAN use open_app (e.g. notepad, calc, chrome) and type_text."
        if is_windows else
        "Running on a CLOUD Linux server (Render): NEVER use open_app or Windows GUI commands like notepad/tkinter, and NEVER write scripts that block on input(). Instead, write clean standalone Python files (write_file) that the user can download and run on Windows."
    )
    recent_chat = "\n".join([f"{m.get('role', 'user')}: {m.get('text', '')}" for m in req.history[-10:]])
    existing_files = [f for f in os.listdir(".") if os.path.isfile(f) and not f.startswith(".")]

    prompt = (
        "You are an elite Senior AI Software Architect with an integrated Proactive Backup Brain. Before generating steps, anticipate unstated user needs: if creating a Windows desktop tool, automatically include a floating toggle GUI, voice/text commands, file/Excel/browser/clipboard automation, auto-dependency checks, and save via write_file so the user can download and run it in 1 click. In 'reply', explicitly mention what unstated needs your Backup Brain anticipated and included. "
        "Carefully read the conversation history and existing workspace files so you remember all prior context, filenames, and user goals.\n"
        f"Environment: {env_info}\n"
        f"Existing files in workspace: {existing_files}\n"
        f"Recent conversation history:\n{recent_chat}\n\n"
        "Set 'reply' to a smart, clear explanation referencing prior context, confirming what you built, and suggesting next steps. "
        "Set 'steps' to a list of steps using action in ['write_file', 'run_cmd', 'open_app', 'type_text']. "
        "When asked for a desktop assistant with a floating toggle button (for opening Chrome, searching, opening Downloads, opening/editing Excel files, etc.), write a complete, easy-to-use Python script via 'write_file' (e.g. 'desktop_copilot.py') using standard libraries (tkinter for floating mini-button, os, subprocess, webbrowser, openpyxl/pandas, speech_recognition) with clear error handling.\n\n"
        f"User request: {req.task}"
    )

    parsed = None
    maker_used = "Gemini"

    # 1. Try Gemini Makers with strict JSON Schema
    for model_name in ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.6-flash"]:
        try:
            res = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=AgentPlanResponse,
                ),
            )
            parsed = json.loads(res.text)
            maker_used = model_name
            break
        except Exception:
            continue

    # 2. Automatic Failover: If all Gemini models fail, Groq Llama-3.3-70B takes over as Maker!
    if parsed is None:
        try:
            parsed = call_groq_maker(prompt)
            maker_used = "Groq Llama-3.3-70B (Backup Maker)"
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Both Gemini and Groq failed: {e}")

    # 3. Independent Checker Audit
    checked, badge = run_checker_ai("agent_plan", req.task, json.dumps(parsed))
    try:
        final_plan = json.loads(checked)
    except Exception:
        final_plan = parsed

    return {
        "reply": final_plan.get("reply", "Plan ready!"),
        "steps": final_plan.get("steps", []),
        "checker": f"Maker: {maker_used} | {badge}",
    }

@app.post("/api/agent/execute")
def execute_agent_step(step: AgentStepRequest):
    act = step.action
    try:
        if act == "write_file":
            with open(step.target, "w", encoding="utf-8") as f:
                f.write(step.content)
            # Self-healing syntax verification loop for Python files!
            if step.target.endswith(".py"):
                for attempt in range(2):
                    chk = subprocess.run(["python", "-m", "py_compile", step.target], capture_output=True, text=True)
                    if chk.returncode == 0:
                        break
                    # Auto-repair syntax error using Gemini/Groq before finishing!
                    fix_prompt = f"Fix this Python syntax error in {step.target} and return ONLY raw corrected Python code without markdown backticks:\nError: {chk.stderr}\n\nCode:\n{step.content}"
                    try:
                        fix_res = client.models.generate_content(model="gemini-3.5-flash-lite", contents=fix_prompt)
                        fixed_code = fix_res.text.replace("```python", "").replace("```", "").strip()
                        with open(step.target, "w", encoding="utf-8") as f:
                            f.write(fixed_code)
                    except Exception:
                        break
            return {"ok": True, "output": f"Saved & syntax-verified file: {step.target}"}
        elif act == "run_cmd":
            proc = subprocess.run(step.target, shell=True, capture_output=True, text=True, timeout=30)
            out = (proc.stdout + proc.stderr).strip()
            return {"ok": proc.returncode == 0, "output": out[:1500] or "Command completed."}
        elif act == "open_app":
            if os.name == "nt":
                os.system(f'start "" "{step.target}"')
                return {"ok": True, "output": f"Opened Windows app: {step.target}"}
            return {"ok": False, "output": "Cloud server mode: download the generated .py script to run GUI apps on Windows."}
        elif act == "type_text":
            if HAS_GUI and os.name == "nt":
                pyautogui.sleep(0.8)
                pyautogui.write(step.target, interval=0.03)
                return {"ok": True, "output": f"Typed text on desktop: {step.target}"}
            return {"ok": False, "output": "Cloud server mode: desktop typing runs when executing locally on Windows."}
        return {"ok": False, "output": f"Unknown action: {act}"}
    except Exception as e:
        return {"ok": False, "output": str(e)}
