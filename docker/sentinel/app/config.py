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
    # Token compartido únicamente con corp-tools-api (Nodo 2) para las acciones
    # operativas seguras de HomeLab Corp. Nunca se versiona ni se expone en la UI.
    corp_token: str = _get('CORP_ACTION_TOKEN', '')
    threadfin_logs_dir: str = _get('THREADFIN_LOGS_DIR', '/threadfin_logs')
    threadfin_base: str = _get('THREADFIN_BASE', '/homelab/docker/threadfin')
    hostfs: str = _get('HOSTFS', '/hostfs')
    # Node 2 (activo 24/7)
    node2_host: str = _get('NODE2_HOST', '192.168.1.4')
    node2_name: str = _get('NODE2_NAME', 'homelab-node2')
    node2_mac: str = _get('NODE2_MAC', 'e0:d5:5e:02:70:aa')
    node2_ssh_user: str = _get('NODE2_SSH_USER', 'mrdavid')
    node2_ssh_key: str = _get('NODE2_SSH_KEY', '/home/mrdavid/.ssh/node2_bot')
    node2_storage_path: str = _get('NODE2_STORAGE_PATH', '/mnt/storage')
    node2_ml_url: str = _get('NODE2_ML_URL', 'http://192.168.1.4:3003')
    node2_portainer_url: str = _get('NODE2_PORTAINER_URL', 'https://192.168.1.4:9001')

settings = Settings()
