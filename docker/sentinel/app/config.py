import os
from dataclasses import dataclass

def _get(name, default=''):
    v = os.getenv(name)
    return v if v not in (None, '') else default

@dataclass
class Settings:
    port: int = int(_get('SENTINEL_PORT', '9090'))
    host: str = _get('SENTINEL_HOST', '0.0.0.0')
    db_path: str = _get('SENTINEL_DB', '/app/data/sentinel.db')
    public_url: str = _get('SENTINEL_PUBLIC_URL', 'http://192.168.1.3:9090').rstrip('/')
    tz: str = _get('SENTINEL_TZ', 'America/Santo_Domingo')
    llm_provider: str = _get('SENTINEL_LLM_PROVIDER', '').strip().lower()
    api_key: str = _get('SENTINEL_API_KEY', '').strip()
    llm_model: str = _get('SENTINEL_LLM_MODEL', '').strip()
    ntfy_topic: str = _get('NTFY_TOPIC', '').strip()
    ntfy_server: str = _get('NTFY_SERVER', 'https://ntfy.sh').rstrip('/')
    action_secret: str = _get('SENTINEL_ACTION_SECRET', 'change-me')
    threadfin_logs_dir: str = _get('THREADFIN_LOGS_DIR', '/threadfin_logs')
    threadfin_base: str = _get('THREADFIN_BASE', '/homelab/docker/threadfin')
    hostfs: str = _get('HOSTFS', '/hostfs')

settings = Settings()
