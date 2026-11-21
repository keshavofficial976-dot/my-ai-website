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

# 1. Load API Key & Initialize Gemini Client
load_dotenv()
client = genai.Client()

# Models to try in order if one hits a temporary 503 traffic spike
MODELS = ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.6-flash"]

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
    
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    output = result.stdout if result.returncode == 0 else result.stderr
    print(f"💻 Terminal Output:\n{output}")
    return output or "Command executed with no output."

# 3. Flexible Push-to-Talk Voice Recording (Press Enter to Start, Enter to Stop)
def record_voice(filename="command.wav", fs=16000):
    input("\n🎙️ Press ENTER to START speaking...")
    print("🔴 Recording... Speak now! (Press ENTER again when finished)")
    
    chunks = []
    def callback(indata, frames, time_info, status):
        chunks.append(indata.copy())

    with sd.InputStream(samplerate=fs, channels=1, dtype='int16', callback=callback):
        input()

    audio_data = np.concatenate(chunks, axis=0)
    write(filename, fs, audio_data)
    print("⏹️ Recording complete. Sending to Gemini...")
    return filename

# 4. Main Agent Loop with Automatic Fallback Across Models
def start_agent():
    config = types.GenerateContentConfig(
        system_instruction=(
            "You are an autonomous AI software engineer working alongside the user in VS Code on Windows. "
            "Use `list_files` and `read_code_file` to inspect existing code before making changes. "
            "Use `write_code_file` to create or edit files, and `run_terminal_command` ONLY with valid Windows commands "
            "(never use Linux commands like `ls` or `rm -rf`). First briefly state what you heard the user ask for."
        ),
        tools=[list_files, read_code_file, write_code_file, run_terminal_command],
    )

    history = []
    print("🤖 AI Cowork Agent Ready! (Press Ctrl+C to exit)")

    while True:
        audio_path = record_voice()
        with open(audio_path, "rb") as f:
            audio_bytes = f.read()

        user_message = [
            types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav"),
            "Listen to my dictated instructions and execute them step by step."
        ]

        # Try primary model first, automatically switch to backup models on 503
        for model_name in MODELS:
            try:
                chat = client.chats.create(model=model_name, config=config, history=history)
                response = chat.send_message(user_message)
                history = chat.get_history()  # Save conversation memory across turns
                print(f"\n🤖 Agent ({model_name}): {response.text}")
                break
            except APIError as e:
                if e.code == 503:
                    print(f"⚠️ {model_name} is busy (503). Switching to backup model...")
                    time.sleep(1)
                else:
                    print(f"❌ API Error on {model_name}: {e.message}")
                    break

if __name__ == "__main__":
    start_agent()
