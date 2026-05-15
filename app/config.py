from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AgentFlowViz Backend"
    database_url: str = "postgresql+psycopg://agentflowviz:agentflowviz@localhost:5433/agentflowviz"
    test_database_url: str = "postgresql+psycopg://agentflowviz:agentflowviz@localhost:5433/agentflowviz_test"
    ollama_host: str = "http://localhost:11434"
    ollama_default_model: str = "gpt-oss:20b"
    ollama_fast_model: str = "qwen3.5:9b"
    ollama_embedding_model: str = "qwen3-embedding:4b"
    semantic_cache_enabled: bool = True
    semantic_cache_similarity_threshold: float = 0.92
    semantic_cache_ttl_hours: int = 24
    semantic_cache_embedding_dimensions: int = 1024
    ollama_context_window_tokens: int = 8192
    ollama_context_window_short_tokens: int = 4096
    ollama_context_window_long_tokens: int = 8192
    ollama_context_window_large_tokens: int = 16384
    context_window_overhead_tokens: int = 192
    final_response_min_output_tokens: int = 360
    final_response_default_output_tokens: int = 420
    final_response_max_output_tokens: int = 1024
    final_response_repair_tokens: int = 180
    inference_power_watts: float = 450.0
    electricity_cost_per_kwh_usd: float = 0.15
    rss_scheduler_enabled: bool = True
    rss_crawl_interval_minutes: int = 5
    rss_feed_urls: str = (
        "https://news.ycombinator.com/rss,"
        "https://export.arxiv.org/rss/cs.AI,"
        "https://export.arxiv.org/rss/cs.LG"
    )
    rss_max_feeds: int = 5
    rss_max_items_per_feed: int = 10
    rss_max_new_items_per_run: int = 25
    rss_item_max_chars: int = 1200
    rss_retrieval_limit: int = 3
    rss_retrieval_candidates: int = 20
    rss_freshness_hours: int = 24
    rss_context_max_chars: int = 1800
    context_reduction_order: tuple[str, ...] = (
        "workflow_context",
        "tool_results",
        "conversation_memory",
        "incoming_agent_context",
        "original_user_request",
        "agent_instructions",
        "response_budget",
        "public_response_rules",
    )
    conversation_memory_recent_runs: int = 4
    conversation_memory_summary_max_chars: int = 1600
    conversation_memory_recent_max_chars: int = 1800
    minio_enabled: bool = True
    minio_endpoint: str = "http://localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "agentflowviz-artifacts"
    minio_secure: bool = False
    attachment_max_upload_mb: int = 25
    attachment_text_max_chars: int = 12000
    attachment_context_max_chars: int = 6000
    attachment_max_pdf_pages: int = 8
    ollama_vision_model: str = ""
    telegram_bot_token: str | None = None
    telegram_allowed_user_ids: str = ""
    fake_llm: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def allowed_telegram_users(self) -> set[int]:
        user_ids: set[int] = set()
        for raw_id in self.telegram_allowed_user_ids.split(","):
            raw_id = raw_id.strip()
            if raw_id and raw_id.lstrip("-").isdigit():
                user_ids.add(int(raw_id))
        return user_ids

    @property
    def rss_feed_url_list(self) -> list[str]:
        urls: list[str] = []
        for raw_url in self.rss_feed_urls.replace("\n", ",").split(","):
            url = raw_url.strip()
            if url and url not in urls:
                urls.append(url)
        return urls[: self.rss_max_feeds]

    def context_window_for_profile(self, profile: str | None = None) -> int:
        if not profile:
            return max(1024, int(self.ollama_context_window_tokens))

        normalized = str(profile).strip().lower()
        if normalized.isdigit():
            return max(1024, int(normalized))

        profile_tokens = {
            "short": self.ollama_context_window_short_tokens,
            "long": self.ollama_context_window_long_tokens,
            "large": self.ollama_context_window_large_tokens,
        }
        return max(1024, int(profile_tokens.get(normalized, self.ollama_context_window_tokens)))


@lru_cache
def get_settings() -> Settings:
    return Settings()
