"""
main.py — JARVIS Unified Core
==============================
Thin FastAPI shell. All logic lives in services/, stt/, brain/, executor/, tts/.
"""

import os
import sys
from contextlib import asynccontextmanager
from dotenv import load_dotenv

# Windows consoles often use cp1252 — unicode log arrows must not crash the pipeline
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Add backend and root to path
sys.path.append(os.path.join(os.path.dirname(__file__), '.'))
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

if getattr(sys, 'frozen', False):
    env_path = os.path.join(os.path.dirname(sys.executable), '.env')
else:
    env_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(env_path)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.websocket_manager import manager
from services.startup import start_all_services
from services.shutdown import shutdown_all_services
from api.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await start_all_services()
    yield
    await shutdown_all_services(reason="server_lifespan")


app = FastAPI(title="Jarvis Backend", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
