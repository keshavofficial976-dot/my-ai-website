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

SUPABASE_FORM_SCRIPT = """
<script>
document.addEventListener('click', function(e) {
  const link = e.target.closest('a');
  if (link) {
    const href = link.getAttribute('href') || '';
    if (href.startsWith('#') && href.length > 1) {
      e.preventDefault();
      const target = document.querySelector(href);
      if (target) target.scrollIntoView({ behavior: 'smooth' });
    } else {
      e.preventDefault();
    }
  }
});

document.addEventListener('submit', async function(e) {
  e.preventDefault();
  const form = e.target;
  const formData = {};
  new FormData(form).forEach((val, key) => { formData[key || 'field'] = val; });
  form.querySelectorAll('input, textarea, select').forEach((el, idx) => {
    const label = el.name || el.id || el.placeholder || ('field_' + (idx + 1));
    if (el.value) formData[label] = el.value;
  });

  try {
    const res = await fetch('https://supabase.co', {
      method: 'POST',
      headers: {
        'apikey': 'sb_publishable_ibTinPvnZJtclMvnxobMVw_LoMqeEpD',
        'Authorization': 'Bearer sb_publishable_ibTinPvnZJtclMvnxobMVw_LoMqeEpD',
        'Content-Type': 'application/json',
        'Prefer': 'return=minimal'
      },
      body: JSON.stringify({ site_name: document.title || 'Generated Website', form_data: formData })
    });
    if (res.ok) {
      alert('Order / Inquiry sent and saved to database!');
      form.reset();
    } else {
      alert('Submitted! (Check database permissions)');
    }
  } catch (err) {
    alert('Order submitted!');
  }
});
</script>
"""

@app.get("/")
def serve_home():
    return FileResponse("index.html")

@app.post("/api/generate")
def generate_site(req: SiteRequest):
    instruction = (
        "Return ONLY raw valid single-file HTML with clean internal CSS styling. "
        "No markdown backticks, no explanations. All navigation links must use #section-id anchors, and any order or contact form must use proper <form> and <input name='...'> tags. "
        + (f"Update this HTML: {req.current_code} based on: " if req.current_code else "Create a complete responsive website for: ")
        + req.prompt
    )
    for model_name in ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.6-flash"]:
        try:
            res = client.models.generate_content(model=model_name, contents=instruction)
            html = res.text.replace("```html", "").replace("```", "").strip()
            if "</body>" in html:
                html = html.replace("</body>", SUPABASE_FORM_SCRIPT + "\n</body>")
            else:
                html += SUPABASE_FORM_SCRIPT
            return {"html": html}
        except Exception:
            continue
    raise HTTPException(status_code=503, detail="AI servers busy, please retry in a moment.")
