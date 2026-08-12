from dataclasses import dataclass
import os
from pathlib import Path
import json

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
    # ``auto`` enables the optional cross-encoder only when CUDA is genuinely
    # available; ordinary CPU-only startup stays lightweight and offline.
    reranker_provider: str = "auto"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    reranker_cache_dir: str = "./data/models"
    reranker_device: str = "auto"
    reranker_candidates: int = 20
    retrieval_top_k: int = 6
    web_search_provider: str = "none"
    web_search_mcp_command: str = "npx"
    web_search_mcp_args: tuple[str, ...] = ("-y", "tavily-mcp@latest")
    web_search_mcp_tool: str = "tavily-search"
    web_search_max_results: int = 5
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
            reranker_provider=os.getenv("RERANKER_PROVIDER", "auto").lower(),
            reranker_model=os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"),
            reranker_cache_dir=os.getenv("RERANKER_CACHE_DIR", "./data/models"),
            reranker_device=os.getenv("RERANKER_DEVICE", "auto").lower(),
            reranker_candidates=int(os.getenv("RERANKER_CANDIDATES", "20")),
            retrieval_top_k=int(os.getenv("RETRIEVAL_TOP_K", "6")),
            web_search_provider=os.getenv("WEB_SEARCH_PROVIDER", "none").lower(),
            web_search_mcp_command=os.getenv("WEB_SEARCH_MCP_COMMAND", "npx"),
            web_search_mcp_args=tuple(json.loads(os.getenv("WEB_SEARCH_MCP_ARGS", '["-y", "tavily-mcp@latest"]'))),
            web_search_mcp_tool=os.getenv("WEB_SEARCH_MCP_TOOL", "tavily-search"),
            web_search_max_results=int(os.getenv("WEB_SEARCH_MAX_RESULTS", "5")),
            app_api_key=os.getenv("RESEARCHFLOW_APP_API_KEY", ""),
            max_upload_bytes=int(os.getenv("RESEARCHFLOW_MAX_UPLOAD_BYTES", str(15 * 1024 * 1024))),
        )

    def ensure_directories(self) -> None:
        Path(self.db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        Path(self.fastembed_cache_dir).expanduser().resolve().mkdir(parents=True, exist_ok=True)
        Path(self.reranker_cache_dir).expanduser().resolve().mkdir(parents=True, exist_ok=True)
