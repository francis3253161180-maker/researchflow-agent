from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    db_path: str = "./data/researchflow.db"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_thinking: str = "disabled"
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = ""
    embedding_provider: str = "hash"
    fastembed_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    fastembed_cache_dir: str = "./data/models"
    app_api_key: str = ""
    max_upload_bytes: int = 15 * 1024 * 1024

    @classmethod
    def from_env(cls) -> "Settings":
        # Keep host/container environment variables authoritative while making the
        # documented local `.env` workflow work without extra uvicorn flags.
        load_dotenv(Path.cwd() / ".env", override=False)
        deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
        return cls(
            db_path=os.getenv("RESEARCHFLOW_DB_PATH", "./data/researchflow.db"),
            llm_base_url=os.getenv("LLM_BASE_URL", "") or ("https://api.deepseek.com" if deepseek_key else ""),
            llm_api_key=os.getenv("LLM_API_KEY", "") or deepseek_key,
            llm_model=os.getenv("LLM_MODEL", "") or ("deepseek-v4-flash" if deepseek_key else ""),
            llm_thinking=os.getenv("LLM_THINKING", "disabled").lower(),
            embedding_base_url=os.getenv("EMBEDDING_BASE_URL", ""),
            embedding_api_key=os.getenv("EMBEDDING_API_KEY", ""),
            embedding_model=os.getenv("EMBEDDING_MODEL", ""),
            embedding_provider=os.getenv("EMBEDDING_PROVIDER", "hash").lower(),
            fastembed_model=os.getenv("FASTEMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"),
            fastembed_cache_dir=os.getenv("FASTEMBED_CACHE_DIR", "./data/models"),
            app_api_key=os.getenv("RESEARCHFLOW_APP_API_KEY", ""),
            max_upload_bytes=int(os.getenv("RESEARCHFLOW_MAX_UPLOAD_BYTES", str(15 * 1024 * 1024))),
        )

    def ensure_directories(self) -> None:
        Path(self.db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        Path(self.fastembed_cache_dir).expanduser().resolve().mkdir(parents=True, exist_ok=True)
