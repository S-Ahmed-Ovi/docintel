from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/app.db"
    storage_dir: str = "./data/files"
    api_key: str = "dev-key"              # sent as X-API-Key; CHANGE in production
    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    max_upload_mb: int = 50
    max_pdf_pages: int = 3000
    rate_limit_per_min: int = 300
    run_inline: bool = False              # tests: process synchronously
    worker_threads: int = 2
    max_attempts: int = 3
    ocr_dpi: int = 200
    min_page_chars: int = 40              # below this a PDF page is treated as scanned
    ocr_lang: str = "eng"
    # LLM (optional). Without a key the rule-based extractor is used.
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    llm_provider: str = "auto"            # auto|gemini|anthropic ; auto = Gemini if GEMINI_API_KEY set, else Anthropic
    gemini_model: str = "gemini-2.5-flash"
    llm_model: str = "claude-sonnet-4-5"
    llm_concurrency: int = 4
    embedding_dim: int = 256
    tolerance: float = 0.02               # absolute money tolerance for validation
    review_confidence: float = 0.6


settings = Settings()
