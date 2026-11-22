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

@app.get("/")
def serve_home():
    return FileResponse("index.html")

@app.post("/api/generate")
def generate_site(req: SiteRequest):
    instruction = (
        "Return ONLY raw valid single-file HTML with clean internal CSS styling. "
        "No markdown backticks, no explanations. "
        + (f"Update this HTML: {req.current_code} based on: " if req.current_code else "Create a complete responsive website for: ")
        + req.prompt
    )
    for model_name in ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.6-flash"]:
        try:
            res = client.models.generate_content(model=model_name, contents=instruction)
            html = res.text.replace("```html", "").replace("```", "").strip()
            return {"html": html}
        except Exception:
            continue
    raise HTTPException(status_code=503, detail="AI servers busy, please retry in a moment.")
instruction = (
    "Return ONLY raw valid single-file HTML with clean internal CSS styling. "
    "No markdown backticks, no explanations. All buttons and navigation links must scroll smoothly to sections on the same page using #section-id (never link to external URLs or reload the page). "
    + (f"Update this HTML: {req.current_code} based on: " if req.current_code else "Create a complete responsive website for: ")
    + req.prompt
)
