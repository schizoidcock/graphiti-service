from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict  # type: ignore


class Settings(BaseSettings):
    openai_api_key: str = Field(default="")  # Make optional to prevent startup crashes
    openai_base_url: str | None = Field(default="https://api.openai.com/v1")
    
    # CRITICAL FIX: Support both MODEL_NAME and LARGE_MODEL_NAME/SMALL_MODEL_NAME
    model_name: str | None = Field(default=None)
    large_model_name: str | None = Field(default=None)
    small_model_name: str | None = Field(default=None)
    embedding_model_name: str | None = Field(default="text-embedding-3-small")
    
    temperature: float = Field(default=0.1)  # LLM temperature for response creativity/consistency
    falkordb_host: str = Field(default="localhost")
    falkordb_port: str = Field(default="6379")
    falkordb_username: str | None = Field(default="")
    falkordb_password: str | None = Field(default="")
    port: int = Field(default=8003)  # Railway will override this with PORT env var
    
    @property
    def effective_model_name(self) -> str:
        """Get the effective model name with proper precedence"""
        # Priority: LARGE_MODEL_NAME > MODEL_NAME > fallback
        return (
            self.large_model_name or 
            self.model_name or 
            "gpt-4o-mini"
        )
    
    @property 
    def effective_small_model_name(self) -> str:
        """Get the effective small model name"""
        return (
            self.small_model_name or
            self.large_model_name or 
            self.model_name or
            "gpt-4o-mini"
        )

    model_config = SettingsConfigDict(env_file='.env', extra='ignore')


@lru_cache
def get_settings():
    return Settings()  # type: ignore[call-arg]


ZepEnvDep = Annotated[Settings, Depends(get_settings)]
