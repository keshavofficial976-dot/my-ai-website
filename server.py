import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from google import genai

load_dotenv()
client = genai.Client()

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class SiteRequest(BaseModel):
    prompt: str
    current_code: str = ""
    owner_email: str = ""
    owner_whatsapp: str = ""

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

  /* 1. Save to Supabase database */
  try {{
    await fetch('https://supabase.co', {{
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

  /* 2. Send Email via FormSubmit if owner provided email */
  if (OWNER_EMAIL) {{
    try {{
      await fetch('https://formsubmit.co' + encodeURIComponent(OWNER_EMAIL), {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json', 'Accept': 'application/json' }},
        body: JSON.stringify({{ _subject: 'New Order / Inquiry from ' + (document.title || 'Your Website'), ...formData }})
      }});
    }} catch (err) {{}}
  }}

  const summary = Object.entries(formData).map(([k, v]) => k + ': ' + v).join('\\n');
  form.reset();

  /* 3. Open WhatsApp to Owner if WhatsApp number provided */
  if (OWNER_PHONE) {{
    alert('Order saved! Opening WhatsApp to notify the business owner...');
    window.open('https://wa.me' + OWNER_PHONE + '?text=' + encodeURIComponent('New Website Order:\\n' + summary), '_blank');
  }} else {{
    alert('Order / Inquiry sent successfully!');
  }}
}});
</script>
"""

@app.get("/")
def serve_home():
    return FileResponse("index.html")

@app.post("/api/generate")
def generate_site(req: SiteRequest):
    instruction = (
        "Return ONLY raw valid single-file HTML with clean internal CSS styling. "
        "No markdown backticks, no explanations. All navigation links must use #section-id anchors, "
        "and every Order / Contact / Booking form MUST be wrapped in a <form> tag with a <button type='submit'> button. "
        + (f"Update this HTML: {req.current_code} based on: " if req.current_code else "Create a complete responsive website for: ")
        + req.prompt
    )
    for model_name in ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.6-flash"]:
        try:
            res = client.models.generate_content(model=model_name, contents=instruction)
            html = res.text.replace("```html", "").replace("```", "").strip()
            script = build_form_script(req.owner_email.strip(), req.owner_whatsapp.strip())
            if "</body>" in html:
                html = html.replace("</body>", script + "\n</body>")
            else:
                html += script
            return {"html": html}
        except Exception:
            continue
    raise HTTPException(status_code=503, detail="AI servers busy, please retry in a moment.")
