from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings
from typing import Literal, List
import json

# The webhook secret this repo ships with. Kept as a named constant so the
# production validator can recognise "still the default" without the literal
# being written twice and silently drifting apart.
_SHIPPED_EMAIL_WEBHOOK_SECRET = '64d229d63ce4b83a0ec981703a1be25fc256fc6a1174bc31ce8591a98ee28750'


class Settings(BaseSettings):
    # App
    app_env: Literal['development', 'production', 'test'] = 'development'
    cors_origins: List[str] = ['*']

    # Logging. log_level drives the app's own loggers (see main.py, which is
    # where logging actually gets configured — before this existed nothing
    # configured it at all, so the root WARNING default silently swallowed
    # every logger.info in the codebase). sql_log_level is separate because
    # SQLAlchemy's engine logger echoes every statement AND every bound
    # parameter list, which drowns the app's own lines at INFO.
    log_level: str = 'INFO'
    sql_log_level: str = 'WARNING'
    
    # File Upload limits
    max_upload_size_mb: int = 50
    allowed_upload_extensions: List[str] = [
        "pdf", "docx", "doc", "xlsx", "xls", "pptx", "ppt",
        "csv", "txt", "md", "rtf", "json", "jpg", "jpeg", "png", "webp", "bmp",
        # Code / config / plain-text formats — all handled by the same
        # plain-text extraction fallback as .txt/.md, so no new parser needed.
        "py", "js", "jsx", "ts", "tsx", "java", "c", "cpp", "h", "hpp", "cs",
        "go", "rb", "php", "sh", "bash", "sql", "yaml", "yml", "xml",
        "html", "css", "scss", "log", "ini", "toml", "conf",
    ]
    
    # Database
    postgres_url: str

    # D-2 security fix — the restricted, non-superuser role every real
    # FastAPI request connects as (see database.py's AppSessionLocal).
    # Empty string means "not configured yet": database.py falls back to
    # postgres_url (the superuser connection) rather than crashing, so an
    # environment that hasn't rotated to the new role yet (e.g. CI) keeps
    # working — but Row-Level Security provides no real protection until
    # this is set. Never silently assume it's fine; database.py logs a
    # warning on every startup when it's empty.
    app_postgres_url: str = ''

    # Redis
    redis_url: str
    
    # JWT
    jwt_secret_key: str = 'secret'
    jwt_algorithm: str = 'HS256'
    jwt_access_token_expire_minutes: int = 15  # 15 minutes
    jwt_refresh_token_expire_days: int = 7  # 7 days
    
    # AWS S3 / MinIO
    aws_access_key_id: str = 'minioadmin'
    aws_secret_access_key: str = 'minioadmin'
    aws_region: str = 'us-east-1'
    s3_bucket_name: str = 'docsearch-documents'
    s3_endpoint_url: str = 'http://localhost:9000'
    s3_public_endpoint_url: str = 'http://localhost:9000'
    s3_presigned_url_expiry_seconds: int = 900

    # T64 — WORM archival storage. A separate bucket from s3_bucket_name:
    # S3/MinIO Object Lock can only be enabled at bucket creation time, and
    # the main operational bucket already exists without it — retrofitting
    # would mean deleting and recreating a bucket that holds real data.
    s3_archive_bucket_name: str = 'docsearch-archive'
    
    # T91 (partial) — fail-closed air-gapped toggle. When true, any AI/OCR
    # provider that would reach an external API is refused at resolution
    # time instead of silently calling out. Today this only genuinely gates
    # embeddings (bgem3), reranking (bgem3) and OCR (pdfplumber), which
    # already have local implementations — LLM and VLM have no local
    # provider yet (T90, not built), so air-gapped mode fails closed on
    # those rather than falsely claiming to serve them locally.
    air_gapped: bool = False

    # T81 — licensing enforcement. Placeholder business model pending real
    # sign-off (A5) — see T81_licensing_assumptions.md. deployment_mode picks
    # which enforcement mechanism applies: 'saas' meters usage against a
    # subscription plan (app/services/license_service.py:PLAN_DEFINITIONS);
    # 'on_prem' verifies a signed capacity license file instead, since an
    # air-gapped install (T92) cannot phone home to check a subscription.
    deployment_mode: Literal['saas', 'on_prem'] = 'saas'
    license_enforcement_enabled: bool = True
    on_prem_license_path: str = '/etc/veritasdocs/license.lic'
    license_grace_period_days: int = 14

    # AI Providers
    ai_llm_provider: Literal['openai', 'anthropic', 'groq'] = 'openai'
    ai_embed_provider: Literal['openai', 'bgem3', 'gemini', 'cohere'] = 'bgem3'
    ai_embed_fallback_provider: Literal['cohere', 'openai', 'none'] = 'none'
    ai_rerank_provider: Literal['cohere', 'bgem3', 'none'] = 'cohere'
    ai_ocr_provider: Literal['pdfplumber', 'llamaparse', 'paddleocr', 'chandra', 'groq'] = 'pdfplumber'

    # T22 — VLM extraction path. Gemini (direct) and OpenRouter (proxying any
    # OpenRouter-hosted vision model, openrouter_vlm_model) are wired up.
    # 'chandra' calls Datalab's real /convert API (see chandra_provider.py
    # for the real, known limitation: no per-field bbox precision the way
    # Gemini's prompt-following gives us, only Datalab's own detected
    # table-cell boxes mapped onto our schema by column order).
    # 'none' disables T22 outright and ingestion falls back to chunk-only
    # indexing, same as before this task.
    ai_vlm_provider: Literal['gemini', 'openrouter', 'chandra', 'none'] = 'gemini'
    gemini_vlm_model: str = 'gemini-3.6-flash'
    # Real bug found live 2026-09-09: a 280-page register (a completely
    # ordinary document size for this product's real corpus -- district
    # Wakf-property gazettes routinely run this long) only ever got
    # structured facts for its first 25 pages under the old default. Pages
    # 26-280 still got real OCR/chunk-text and were fully searchable, but
    # had zero extracted facts -- silently degrading every field-specific
    # question about the other 91% of the document to ambiguous raw-text
    # grounding instead of the clean, properly-labelled Fact rows the rest
    # of the document got. Raised, not removed: still a real, named ceiling
    # (each page beyond it is a real VLM API call/cost) against a genuinely
    # pathological upload, just one that no longer silently truncates a
    # normal document for this product's own domain.
    vlm_max_pages_per_document: int = 500

    openrouter_api_key: str = ''
    openrouter_vlm_model: str = 'google/gemini-2.5-flash'

    datalab_api_key: str = ''

    openai_api_key: str = ''
    openai_llm_model: str = 'gpt-4o-mini'
    openai_embed_model: str = 'text-embedding-3-small'
    openai_embed_dimensions: int = 1024
    
    anthropic_api_key: str = ''
    anthropic_llm_model: str = 'claude-3-5-haiku-20241022'
    
    groq_api_key: str = ''
    groq_api_key1: str = ''
    groq_api_key2: str = ''
    groq_api_key3: str = ''
    groq_api_keys: str = ''
    groq_llm_model: str = 'openai/gpt-oss-120b'
    # AI_OCR_PROVIDER=groq: vision model used to read scanned pages. Must be
    # enabled for at least one key's Groq organization (keys whose org has it
    # blocked are skipped). Free tier is ~7k input tokens/min per org, and a
    # page costs ~1 token per 28x28 px, hence the pixel cap.
    groq_vision_model: str = 'qwen/qwen3.8-27b'
    groq_ocr_max_pixels: int = 1_600_000
    groq_ocr_timeout_seconds: float = 300.0
    
    def get_groq_api_keys(self) -> List[str]:
        keys = []
        for k in [self.groq_api_key, self.groq_api_key1, self.groq_api_key2, self.groq_api_key3]:
            if k and k.strip() and k.strip() not in keys:
                keys.append(k.strip())
        if self.groq_api_keys:
            for k in self.groq_api_keys.split(','):
                k_clean = k.strip()
                if k_clean and k_clean not in keys:
                    keys.append(k_clean)
        return keys

    
    google_api_key: str = ''
    gemini_embed_model: str = 'text-embedding-004'
    
    cohere_api_key: str = ''
    cohere_rerank_model: str = 'rerank-english-v3.0'
    bgem3_rerank_model: str = 'BAAI/bge-reranker-v2-m3'
    
    llamaparse_api_key: str = ''
    
    # Rate limiting
    rate_limit_per_user: str = '60/minute'
    rate_limit_per_tenant: str = '1000/minute'

    # Shared connector actor (demo scope, T40): every non-interactive
    # ingestion source (SFTP, watched-folder, legacy IMAP and the email-inbound
    # webhook) attributes
    # its documents to this one existing user/tenant rather than resolving
    # a per-source mapping. Was previously a hardcoded, unconfigurable
    # constant pointing at an email no seeded user actually has — every
    # connector poll failed with "Connector actor not found" until this was
    # pulled out into settings and given a real value in .env. Point this at
    # an existing user's email for connectors to work at all.
    connector_actor_email: str = 'teamworklax@gmail.com'

    # Scan quality check on every uploaded image (app/services/scan_quality_service.py).
    # A failing image is still ingested, just flagged for "Needs Review".
    # Thresholds are initial guesses, not tuned against the real corpus yet.
    scan_quality_check_enabled: bool = True
    scan_quality_min_sharpness: float = 100.0     # Laplacian variance; lower = blurry
    scan_quality_min_brightness: float = 40.0     # mean grayscale 0-255; lower = underexposed
    scan_quality_max_brightness: float = 245.0    # mean grayscale 0-255; higher = overexposed
    scan_quality_min_blank_variance: float = 100.0  # pixel variance; lower = likely blank page
    scan_quality_min_resolution_px: int = 800     # shorter edge; lower = too small for OCR

    # SFTP connector (demo scope: single fixed server/credentials/remote dir)
    sftp_enabled: bool = False
    sftp_host: str = 'sftp'
    sftp_port: int = 22
    sftp_username: str = 'connector'
    sftp_password: str = ''
    sftp_remote_dir: str = '/upload'

    # The host/port an OUTSIDE machine should actually dial in to reach the SFTP
    # server (sftp_host/sftp_port above are the Docker-internal address the
    # backend uses to poll it, not reachable from another laptop on the LAN).
    sftp_external_host: str = 'localhost'
    sftp_external_port: int = 2222

    # Email-in webhook connector (Cloudflare Email Routing + Cloudflare Worker)
    email_webhook_enabled: bool = True
    email_webhook_secret: str = _SHIPPED_EMAIL_WEBHOOK_SECRET

    # Legacy IMAP email connector (deprecated/disabled by default in main.py,
    # kept for local dev/GreenMail testing in docker-compose)
    email_enabled: bool = False
    email_imap_host: str = 'mailserver'
    email_imap_port: int = 3143
    email_username: str = 'connector'
    email_password: str = ''
    email_address: str = 'connector@dms.local'

    # The host/port an OUTSIDE machine should use to SEND mail into the demo
    # mailbox over SMTP (not the IMAP host/port above, which the backend uses
    # to poll it).
    email_external_smtp_host: str = 'localhost'
    email_external_smtp_port: int = 3025

    # Outbound transactional email (password reset, etc). Defaults point at
    # the local GreenMail container so dev/demo works with zero config;
    # point these at a real relay (Gmail SMTP, SendGrid, etc.) to deliver to
    # real inboxes. smtp_use_tls=False + smtp_use_ssl=False is GreenMail's
    # plaintext test SMTP; a real relay will need one of those true.
    smtp_host: str = 'mailserver'
    smtp_port: int = 3025
    smtp_username: str = 'connector'
    smtp_password: str = 'connector123'
    smtp_use_tls: bool = False
    smtp_use_ssl: bool = False
    smtp_from_email: str = 'noreply@dms.local'
    smtp_from_name: str = 'VeritasDocs'

    @field_validator('cors_origins', mode='before')
    @classmethod
    def parse_cors_origins(cls, v):
        if isinstance(v, str):
            return json.loads(v)
        return v
    
    @model_validator(mode='after')
    def validate_production_jwt_secret(self):
        WEAK_SECRETS = {"secret", "change_me", "changeme", "secretkey", "jwtsecret", "password", "123456"}
        if self.app_env == "production":
            if self.jwt_secret_key.lower() in WEAK_SECRETS or len(self.jwt_secret_key) < 32:
                raise ValueError("In production, JWT_SECRET_KEY must be a strong secret of at least 32 characters")
            # email_webhook_enabled defaults to True, and this secret is the
            # ONLY thing standing in front of /connectors/email-inbound, which
            # ingests documents as the connector actor. A shipped default here
            # means anyone who has read the source can post documents into the
            # tenant.
            if self.email_webhook_enabled and self.email_webhook_secret == _SHIPPED_EMAIL_WEBHOOK_SECRET:
                raise ValueError(
                    "In production, EMAIL_WEBHOOK_SECRET must be changed from the shipped default "
                    "while EMAIL_WEBHOOK_ENABLED is true — it is the only authentication on the "
                    "inbound email ingestion endpoint"
                )

            # Row-Level Security is the tenant isolation boundary. When
            # app_postgres_url is empty, database.py falls back to the
            # superuser connection, which BYPASSES RLS entirely — it logs a
            # warning and carries on, which is the right call for a dev box
            # and the wrong one for production, where it means every request
            # runs with the ability to read any tenant's rows.
            if not self.app_postgres_url:
                raise ValueError(
                    "In production, APP_POSTGRES_URL must point at the restricted (non-superuser) "
                    "database role — without it every request bypasses Row-Level Security and "
                    "tenant isolation is not enforced"
                )

            # '*' with credentialed requests means any origin can drive the
            # API using a logged-in user's browser session.
            if '*' in self.cors_origins:
                raise ValueError(
                    "In production, CORS_ORIGINS must list explicit origins rather than '*'"
                )
        return self
    
    class Config:
        env_file = '.env'
        env_file_encoding = 'utf-8'
        case_sensitive = False
        extra = 'ignore'

settings = Settings()