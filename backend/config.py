from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parent
_ROOT = _BACKEND_DIR.parent
_ENV_FILES = tuple(
    str(p) for p in (_BACKEND_DIR / ".env", _ROOT / ".env") if p.exists()
)


class Settings(BaseSettings):
    # External credentials are reserved for explicit fetch-only integrations.
    NEWS_API_KEY: str = ""
    HF_TOKEN: str = ""
    
    # Model settings
    # Local inference only for the chat loop.
    LLM_PROVIDER: str = "ollama"
    LLM_MODEL: str = "qwen3:4b"
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    # Keep the KV cache within the laptop's memory budget.
    LLM_NUM_CTX: int = 8192
    PLANNING_MODEL: str = "qwen3:8b"
    # Screen-vision tier: the local model that actually understands images.
    # qwen3:4b rejects multimodal input (HTTP 400); qwen3.5:4b/gemma4 see.
    VISION_MODEL: str = "qwen3.5:4b"

    # Planning tier (think:true only) routes to the Groq API — user-approved
    # deviation from the local-only brief; ordinary chat never leaves Ollama.
    GROQ_API_KEY: str = ""
    PLANNING_PROVIDER: str = "groq"
    GROQ_API_BASE: str = "https://api.groq.com/openai/v1"
    GROQ_PLANNING_MODEL: str = "openai/gpt-oss-20b"
    
    # Other configs
    # Add other env vars here as needed

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES or ".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
