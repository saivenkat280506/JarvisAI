from pydantic_settings import BaseSettings, SettingsConfigDict
import os

class Settings(BaseSettings):
    # API Keys
    GROQ_API_KEY: str = ""
    NEWS_API_KEY: str = ""
    
    # Model settings
    LLM_MODEL: str = "llama-3.1-8b-instant"
    
    # Other configs
    # Add other env vars here as needed

    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
