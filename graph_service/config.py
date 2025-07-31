from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict  # type: ignore


class Settings(BaseSettings):
    openai_api_key: str = Field(default="")  # Make optional to prevent startup crashes
    openai_base_url: str | None = Field(default="https://api.openai.com/v1")
    model_name: str | None = Field(default="gpt-4o-mini")
    embedding_model_name: str | None = Field(default=None)
    falkordb_host: str = Field(default="localhost")
    falkordb_port: str = Field(default="6379")
    falkordb_username: str | None = Field(default="")
    falkordb_password: str | None = Field(default="")
    port: int = Field(default=8003)  # Railway will override this with PORT env var

    model_config = SettingsConfigDict(env_file='.env', extra='ignore')


@lru_cache
def get_settings():
    return Settings()  # type: ignore[call-arg]


ZepEnvDep = Annotated[Settings, Depends(get_settings)]
