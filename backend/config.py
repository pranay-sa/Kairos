from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM (Groq is OpenAI-compatible for chat completions)
    groq_api_key: str = ""
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_chat_model: str = "llama-3.1-8b-instant"

    # Local embeddings (no API key required)
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "kairos_documents"

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password"

    confidence_threshold: float = 0.05
    vector_top_k: int = 8

    github_token: str = ""
    github_owner: str = ""
    github_repo: str = ""
    github_default_branch: str = "main"

    slack_signing_secret: str = ""
    jira_webhook_secret: str = ""

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


settings = Settings()
