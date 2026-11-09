"""App settings, read from environment variables or backend/.env.

Every value has a safe default so the app starts without a .env file.
Secrets are only read here; no endpoint ever returns them.
"""

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ folder (this file is backend/app/core/config.py)
BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database: falls back to a local SQLite file when DATABASE_URL is empty
    database_url: str = ""

    # LLM: providers asked in this order; the next one only when one is busy, rate limited or timed out
    llm_chain: str = "groq,gemini"
    # Groq (OpenAI-compatible API)
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"
    # Gemini
    llm_model: str | None = None
    llm_api_key: str | None = None
    # Gemini model asked once when LLM_MODEL is overloaded, times out or is out of daily quota (empty: none)
    llm_fallback_model: str | None = None
    # Seconds to wait for one LLM HTTP response (thinking models can be slow when the service is busy)
    llm_timeout_seconds: float = 120.0
    # Gemini thinking level for requirement extraction: minimal | low | medium | high, or "default" to send
    # none (the model decides). The lowest level differs per model: gemini-3.8-flash rejects "minimal".
    llm_extraction_thinking: str = "low"

    # Similarity checks (app/nlp/pair_checks.py): local models, loaded on first use. False skips them and the
    # rule-based checks still run. The embedding model must give 384-dimension vectors (vector(384) column).
    similarity_enabled: bool = True
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    nli_model: str = "cross-encoder/nli-deberta-v3-small"
    # Similarity to an ACCEPTED requirement from which a different statement may be a change request. Below it,
    # similar requirements are usually separate features (e.g. "reset password by email" vs "verify email").
    change_request_similarity: float = 0.75

    # Background auto-sync of API sources (app/connectors/scheduler.py), inside the API process. Needs exactly one
    # backend worker; set False for tests and for deploys with several workers or instances.
    auto_sync_enabled: bool = True

    # Voice notes (app/ai/speech.py): Groq Whisper with GROQ_API_KEY. The transcription worker is a background task
    # in the API process like auto-sync (one backend worker); set False for tests.
    transcription_worker_enabled: bool = True
    whisper_model: str = "whisper-large-v3"
    whisper_fallback_model: str = "whisper-large-v3-turbo"
    # Groq's free limit for audio models is 20 requests per minute
    groq_audio_rpm: int = 20
    # Languages voice notes are expected in (codes). In Auto mode a note Whisper hears as another language (Hindi,
    # Urdu, Nepali, Assamese...) or writes in Devanagari is transcribed again as Bangla.
    voice_allowed_languages: str = "bn,en"
    # Keep the audio after transcription (playable on the Messages page). False deletes it once transcribed.
    keep_audio: bool = False

    # Connectors
    jira_base_url: str | None = None
    jira_email: str | None = None
    jira_api_token: str | None = None
    slack_bot_token: str | None = None
    telegram_bot_token: str | None = None
    discord_bot_token: str | None = None
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str = "http://localhost:8000/oauth/gmail/callback"
    github_token: str | None = None

    # Fernet key that encrypts connector credentials stored in the database
    token_encryption_key: str | None = None

    # Auth: secret used to sign JWTs (required for login) and token lifetime
    jwt_secret: str = ""
    jwt_expire_minutes: int = 1440
    # Login, register, OTP and forgot-password attempts per minute, per IP and per email
    auth_rate_limit_per_minute: int = 5
    # Shared with the Next.js server, which proves with it that its X-Client-IP header can be believed
    proxy_shared_secret: str = ""

    # Email. Without SMTP_HOST emails are printed to the backend console (dev mode)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""

    # Comma-separated list of frontend URLs allowed to call the API
    cors_origins: str = "http://localhost:3000"
    # Frontend base URL, used to send the browser back after the Gmail OAuth flow
    frontend_url: str = "http://localhost:3000"

    @field_validator("database_url")
    @classmethod
    def default_to_sqlite(cls, value: str) -> str:
        if value.strip():
            return value.strip()
        return f"sqlite:///{(BACKEND_DIR / 'reqguard.db').as_posix()}"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
