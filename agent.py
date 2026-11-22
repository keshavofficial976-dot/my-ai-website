import os
import time
import subprocess
import numpy as np
import sounddevice as sd
from scipy.io.wavfile import write
from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import APIError


# 1. Load API Key & Initialize Gemini Client (15s timeout so it never hangs silently)
load_dotenv()
client = genai.Client(http_options=types.HttpOptions(timeout=60000))

# Put the fastest, lowest-congestion model first!
MODELS = ["gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-3.8-flash"]

# 2. Define Agent Tools (Windows-safe + File Reading + Permissions)
def list_files(directory: str = ".") -> str:
    """Lists all files and folders in the project directory."""
    try:
        items = [f for f in os.listdir(directory) if f not in (".git", "__pycache__", ".env", "command.wav")]
        return f"Files in '{directory}': {', '.join(items) or 'Empty folder'}"
    except Exception as e:
        return f"Error listing files: {e}"

def read_code_file(filepath: str) -> str:
    """Reads the contents of an existing file before editing it."""
    if not os.path.exists(filepath):
        return f"File '{filepath}' does not exist."
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()

def write_code_file(filepath: str, content: str) -> str:
    """Creates or updates a file in the project directory."""
    print(f"\n[PERMISSION REQUIRED] Agent wants to write file: '{filepath}'")
    approval = input("Approve? (y/n): ").strip().lower()
    if approval != 'y':
        return "Action denied by the user."
    
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"✅ Saved {filepath} (Check your VS Code sidebar!)")
    return f"Successfully wrote {filepath}"

def run_terminal_command(command: str) -> str:
    """Runs a Windows shell command (e.g., git, npx supabase, npm)."""
    print(f"\n[PERMISSION REQUIRED] Agent wants to run command: '{command}'")
    approval = input("Approve? (y/n): ").strip().lower()
    if approval != 'y':
        return "Command denied by the user."
    
    result = subprocess.run(command, shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace")

    output = result.stdout if result.returncode == 0 else result.stderr
    print(f"💻 Terminal Output:\n{output}")
    return output or "Command executed with no output."

# 3. Flexible Push-to-Talk Voice Recording (OR Type Text Directly!)
def get_user_input(filename="command.wav", fs=16000):
    typed = input("\n🎙️ Press ENTER to speak (or type your instruction and press ENTER): ").strip()
    if typed:
        return "text", typed

    print("🔴 Recording... Speak now! (Press ENTER again when finished)")
    chunks = []
    def callback(indata, frames, time_info, status):
        chunks.append(indata.copy())

    with sd.InputStream(samplerate=fs, channels=1, dtype='int16', callback=callback):
        input()

    audio_data = np.concatenate(chunks, axis=0)
    write(filename, fs, audio_data)
    print("⏹️ Recording complete. Sending to Gemini...")
    return "audio", filename

# 4. Main Agent Loop with Smart Token Memory & 429/503 Fallback
def start_agent():
    config = types.GenerateContentConfig(
        system_instruction=(
            "You are an autonomous AI software engineer working alongside the user in VS Code on Windows. "
            "Use `list_files` and `read_code_file` to inspect existing code before making changes. "
            "Use `write_code_file` to create or edit files, and `run_terminal_command` ONLY with valid Windows commands "
            "(never use Linux commands like `ls`). First briefly state what you heard/read from the user."
        ),
        tools=[list_files, read_code_file, write_code_file, run_terminal_command],
    )

    history = []
    print("🤖 AI Cowork Agent Ready! (Press Ctrl+C to exit)")

    while True:
        mode, payload = get_user_input()
        
        if mode == "text":
            user_message = [payload]
        else:
            with open(payload, "rb") as f:
                audio_bytes = f.read()
            user_message = [
                types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav"),
                "Listen to my dictated instructions and execute them step by step."
            ]

        for model_name in MODELS:
            try:
                # Keep only the last 6 turns so we never exceed the 250k tokens/min Free Tier limit
                trimmed_history = history[-6:] if len(history) > 6 else history
                chat = client.chats.create(model=model_name, config=config, history=trimmed_history)
                response = chat.send_message(user_message)
                
                # Save a lightweight text summary to history instead of re-sending heavy audio bytes
                history.append(types.Content(role="user", parts=[types.Part.from_text(text=payload if mode == "text" else "[User voice command executed]")]))
                history.append(types.Content(role="model", parts=[types.Part.from_text(text=response.text or "Done.")]))
                
                print(f"\n🤖 Agent ({model_name}): {response.text}")
                break
            except APIError as e:
                if e.code in (429, 503):
                    print(f"⚠️ {model_name} busy/rate-limited ({e.code}). Switching to backup model...")
                    time.sleep(1)
                else:
                    print(f"❌ API Error on {model_name}: {e.message}")
                    break

if __name__ == "__main__":
    start_agent()
