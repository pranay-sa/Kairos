from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM (Groq is OpenAI-compatible for chat completions)
    groq_api_key: str = ""
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_chat_model: str = "llama-3.1-8b-instant"

    # Local embeddings (no API key required)
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    fastembed_cache_dir: str = "data/fastembed_cache"

    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "kairos_documents"

    confidence_threshold: float = 0.05
    vector_top_k: int = 8

    github_token: str = ""
    github_owner: str = ""
    github_repo: str = ""
    github_default_branch: str = "main"

    slack_signing_secret: str = ""
    jira_webhook_secret: str = ""

    # Jira (optional) - used for backfill + proper browse links
    jira_base_url: str = "https://example.atlassian.net"
    jira_email: str = ""
    jira_api_token: str = ""
    jira_backfill_on_startup: bool = False
    jira_backfill_jql: str = "order by updated desc"
    jira_backfill_max_issues: int = 200

    # Azure Monitor / Log Analytics (optional)
    azure_monitor_enabled: bool = False
    azure_poll_interval_minutes: int = 20
    azure_state_path: str = "data/azure_monitor_state.json"

    azure_tenant_id: str = ""
    azure_client_id: str = ""
    azure_client_secret: str = ""

    # Log Analytics workspace + query (KQL)
    azure_log_analytics_workspace_id: str = ""
    azure_log_analytics_endpoint: str = "https://api.loganalytics.io"
    azure_log_analytics_query: str = (
        "AppTraces | where TimeGenerated > ago(20m) | project TimeGenerated, SeverityLevel, Message"
    )

    # Microsoft Teams (Graph API) -> Qdrant (optional)
    teams_sync_enabled: bool = False
    teams_poll_interval_minutes: int = 10
    teams_state_path: str = "data/teams_graph_state.json"

    # App Registration (client credentials)
    ms_tenant_id: str = ""
    ms_client_id: str = ""
    ms_client_secret: str = ""

    # Users to sync (comma-separated UPNs or user IDs)
    teams_user_ids: str = ""


settings = Settings()
