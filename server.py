import os
import json
import base64
import subprocess
import time
from typing import List, Dict, Any
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
    goal: str
    history: List[Dict[str, str]] = []
    image_data: str = ""
    image_mime: str = ""

class AgentStep(BaseModel):
    action: str
    target: str = ""
    content: str = ""
    reason: str = ""

def build_form_script(owner_email: str, owner_whatsapp: str) -> str:
    clean_phone = "".join(ch for ch in owner_whatsapp if ch.isdigit())
    if len(clean_phone) == 10:
        clean_phone = "91" + clean_phone
    return (
        "\n<script>\n"
        f"const OWNER_EMAIL = {owner_email!r};\n"
        f"const OWNER_PHONE = {clean_phone!r};\n"
        "document.addEventListener('click', function(e) {\n"
        "  const link = e.target.closest('a');\n"
        "  if (link) {\n"
        "    const href = link.getAttribute('href') || '';\n"
        "    if (href.startsWith('#') && href.length > 1) {\n"
        "      e.preventDefault();\n"
        "      const target = document.querySelector(href);\n"
        "      if (target) target.scrollIntoView({ behavior: 'smooth' });\n"
        "    } else if (!href.startsWith('https://wa.me') && !href.startsWith('http')) {\n"
        "      e.preventDefault();\n"
        "    }\n"
        "  }\n"
        "});\n"
        "document.addEventListener('submit', async function(e) {\n"
        "  e.preventDefault();\n"
        "  const form = e.target;\n"
        "  const formData = {};\n"
        "  form.querySelectorAll('input, textarea, select').forEach((el, idx) => {\n"
        "    if (el.type === 'submit' || el.type === 'button') return;\n"
        "    const label = el.name || el.id || el.placeholder || ('Field_' + (idx + 1));\n"
        "    if (el.value) formData[label] = el.value;\n"
        "  });\n"
        "  try {\n"
        "    await fetch('https://baxoksksjwwrlwmmilnv.supabase.co/rest/v1/submissions', {\n"
        "      method: 'POST',\n"
        "      headers: {\n"
        "        'apikey': 'sb_publishable_ibTinPvnZJtclMvnxobMVw_LoMqeEpD',\n"
        "        'Authorization': 'Bearer sb_publishable_ibTinPvnZJtclMvnxobMVw_LoMqeEpD',\n"
        "        'Content-Type': 'application/json',\n"
        "        'Prefer': 'return=minimal'\n"
        "      },\n"
        "      body: JSON.stringify({\n"
        "        site_name: (document.title || 'Website') + (OWNER_EMAIL ? ' (' + OWNER_EMAIL + ')' : ''),\n"
        "        form_data: formData\n"
        "      })\n"
        "    });\n"
        "  } catch (err) {}\n"
        "  if (OWNER_EMAIL) {\n"
        "    try {\n"
        "      await fetch('https://formsubmit.co/ajax/' + encodeURIComponent(OWNER_EMAIL), {\n"
        "        method: 'POST',\n"
        "        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },\n"
        "        body: JSON.stringify({ _subject: 'New Order / Inquiry from ' + (document.title || 'Your Website'), ...formData })\n"
        "      });\n"
        "    } catch (err) {}\n"
        "  }\n"
        "  const summary = Object.entries(formData).map(([k, v]) => k + ': ' + v).join('\\n');\n"
        "  form.reset();\n"
        "  if (OWNER_PHONE) {\n"
        "    alert('Order saved! Opening WhatsApp to notify the business owner...');\n"
        "    window.open('https://wa.me/' + OWNER_PHONE + '?text=' + encodeURIComponent('New Website Order:\\n' + summary), '_blank');\n"
        "  } else {\n"
        "    alert('Order / Inquiry sent successfully!');\n"
        "  }\n"
        "});\n"
        "</script>\n"
    )

@app.get("/")
def serve_home():
    return FileResponse("index.html")

@app.post("/api/generate")
def chat_or_build(req: ChatRequest):
    recent_chat = "\n".join([f"{m['role']}: {m['text']}" for m in req.history[-8:]])
    instruction = (
        "You are a smart, two-way AI Web Designer co-pilot with live Google Search access. "
        "CRITICAL DATA ACCURACY RULE: Never invent stale numbers for stocks, crypto, currency rates, weather, or news. "
        "Use your Google Search tool to get current real-world facts when needed, AND when building dashboards/finance/crypto/weather sites, embed real client-side JavaScript fetch calls to free public no-key APIs (like CoinGecko API for crypto, Open-Meteo API for weather, or ExchangeRate-API for currency) with a clear timestamp and disclaimer so live numbers stay fresh.\n"
        "If the user attached a photo or screenshot, inspect every visual detail and apply it.\n"
        "Return ONLY a raw JSON object (no markdown backticks) with:\n"
        "1. \"reply\": Your conversational response explaining what you built/updated or answering their question, plus 1-2 smart next suggestions.\n"
        "2. \"html\": Complete single-file HTML with clean internal CSS (nav links use #section-id, forms use <form> and <button type='submit'>). If only answering a question without changing the site, set \"html\" to \"\".\n\n"
        f"Conversation so far:\n{recent_chat}\n"
        f"Current Website HTML:\n{req.current_code if req.current_code else '(None yet)'}\n\n"
        f"User's latest message: {req.message}"
    )

    contents: List[Any] = [instruction]
    if req.image_data and req.image_mime:
        try:
            img_bytes = base64.b64decode(req.image_data)
            contents.append(types.Part.from_bytes(data=img_bytes, mime_type=req.image_mime))
        except Exception:
            pass

    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())]
    )

    for model_name in ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.6-flash"]:
        try:
            res = client.models.generate_content(model=model_name, contents=contents, config=config)
            raw = res.text.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1]
            if raw.endswith("```"):
                raw = raw.rsplit("```", 1)[0]
            raw = raw.strip()
            parsed = json.loads(raw)
            reply = parsed.get("reply", "Done! Please verify the updated preview on the right.")
            html = parsed.get("html", "").strip()
            if html:
                script = build_form_script(req.owner_email.strip(), req.owner_whatsapp.strip())
                if "</body>" in html:
                    html = html.replace("</body>", script + "\n</body>")
                else:
                    html += script
            return {"reply": reply, "html": html}
        except Exception:
            continue
    raise HTTPException(status_code=503, detail="AI servers busy, please retry in a moment.")

@app.post("/api/agent/plan")
def plan_agent_task(req: AgentPlanRequest):
    files = [f for f in os.listdir(".") if os.path.isfile(f) and not f.startswith(".")]
    recent_chat = "\n".join([f"{m['role']}: {m['text']}" for m in req.history[-6:]])
    prompt = (
        "You are an autonomous AI Coding & Windows Desktop Automation Agent (like Claude Code + Desktop Operator). "
        f"Current workspace files: {files}. Desktop GUI available: {HAS_GUI}.\n"
        "Break the user's request into safe, clear, sequential steps. Each step must be a JSON object with keys: "
        "'action' (one of: 'read_file', 'write_file', 'run_command', 'open_app', 'type_text', 'press_key', 'web_search'), "
        "'target' (filename, shell command, app name like 'notepad' or 'chrome' or 'calc', or key name like 'enter'), "
        "'content' (full file content for write_file, text to type for type_text, or search query for web_search), "
        "and 'reason' (short plain-English explanation for the user).\n"
        "Return ONLY raw JSON with keys: 'reply' (conversational summary) and 'steps' (list of step objects).\n\n"
        f"Recent context:\n{recent_chat}\nUser goal: {req.goal}"
    )

    contents: List[Any] = [prompt]
    if req.image_data and req.image_mime:
        try:
            img_bytes = base64.b64decode(req.image_data)
            contents.append(types.Part.from_bytes(data=img_bytes, mime_type=req.image_mime))
        except Exception:
            pass

    for model_name in ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.6-flash"]:
        try:
            res = client.models.generate_content(model=model_name, contents=contents)
            raw = res.text.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1]
            if raw.endswith("```"):
                raw = raw.rsplit("```", 1)[0]
            parsed = json.loads(raw.strip())
            return {
                "reply": parsed.get("reply", "Here is the step-by-step plan. Approve each step to execute."),
                "steps": parsed.get("steps", [])
            }
        except Exception:
            continue
    raise HTTPException(status_code=503, detail="Could not generate agent plan right now.")

@app.post("/api/agent/execute")
def execute_agent_step(step: AgentStep):
    act = step.action
    try:
        if act == "read_file":
            if not os.path.exists(step.target):
                return {"ok": False, "output": f"File not found: {step.target}"}
            with open(step.target, "r", encoding="utf-8") as f:
                data = f.read()
            return {"ok": True, "output": f"Read {step.target} ({len(data)} chars):\n{data[:1500]}"}

        elif act == "write_file":
            safe_path = os.path.basename(step.target) if not os.path.dirname(step.target) else step.target
            if os.path.dirname(safe_path):
                os.makedirs(os.path.dirname(safe_path), exist_ok=True)
            with open(safe_path, "w", encoding="utf-8") as f:
                f.write(step.content)
            return {"ok": True, "output": f"Saved file: {safe_path}"}

        elif act == "run_command":
            proc = subprocess.run(step.target, shell=True, capture_output=True, text=True, timeout=45)
            out = (proc.stdout or "") + (proc.stderr or "")
            return {"ok": proc.returncode == 0, "output": out.strip() or f"Command finished (exit {proc.returncode})"}

        elif act == "open_app":
            if os.name == "nt":
                os.startfile(step.target)
                time.sleep(1.2)
                return {"ok": True, "output": f"Opened Windows app/URL: {step.target}"}
            return {"ok": False, "output": "Desktop app launch requires running locally on Windows."}

        elif act == "type_text":
            if not HAS_GUI:
                return {"ok": False, "output": "PyAutoGUI desktop control is only available when running locally on your PC."}
            time.sleep(0.5)
            pyautogui.write(step.content, interval=0.03)
            return {"ok": True, "output": f"Typed text: {step.content}"}

        elif act == "press_key":
            if not HAS_GUI:
                return {"ok": False, "output": "PyAutoGUI desktop control is only available when running locally on your PC."}
            pyautogui.press(step.target)
            return {"ok": True, "output": f"Pressed key: {step.target}"}

        elif act == "web_search":
            res = client.models.generate_content(
                model="gemini-3.5-flash-lite",
                contents=step.content or step.target,
                config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())])
            )
            return {"ok": True, "output": res.text}

        return {"ok": False, "output": f"Unknown action: {act}"}
    except Exception as e:
        return {"ok": False, "output": f"Execution error: {str(e)}"}
