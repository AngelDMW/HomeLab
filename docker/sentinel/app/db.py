import json, sqlite3, threading, time
from contextlib import contextmanager
from .config import settings

_lock = threading.RLock()

def _conn():
    c = sqlite3.connect(settings.db_path, check_same_thread=False, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA synchronous=NORMAL')
    return c

_DB = None

def get_db():
    global _DB
    if _DB is None:
        _DB = _conn()
    return _DB

def init_db():
    with _lock:
        db = get_db()
        db.executescript("""
        CREATE TABLE IF NOT EXISTS health_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            component TEXT NOT NULL,
            status TEXT,
            payload TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_health_ts ON health_samples(ts);
        CREATE INDEX IF NOT EXISTS idx_health_comp ON health_samples(component, ts);

        CREATE TABLE IF NOT EXISTS incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            component TEXT NOT NULL,
            severity TEXT NOT NULL,
            summary TEXT NOT NULL,
            details TEXT,
            resolved INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_incidents_ts ON incidents(ts);

        CREATE TABLE IF NOT EXISTS repairs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            component TEXT NOT NULL,
            action TEXT NOT NULL,
            tier INTEGER NOT NULL,
            status TEXT NOT NULL,
            details TEXT,
            diagnosis TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_repairs_ts ON repairs(ts);

        CREATE TABLE IF NOT EXISTS pending_actions (
            id TEXT PRIMARY KEY,
            ts REAL NOT NULL,
            component TEXT NOT NULL,
            action TEXT NOT NULL,
            params TEXT,
            diagnosis TEXT,
            status TEXT NOT NULL DEFAULT 'pending'
        );

        CREATE TABLE IF NOT EXISTS action_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            component TEXT NOT NULL,
            action TEXT NOT NULL,
            tier INTEGER NOT NULL,
            status TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_action_log ON action_log(component, ts);

        CREATE TABLE IF NOT EXISTS kv (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """)
        db.commit()

def kv_get(key, default=None):
    with _lock:
        row = get_db().execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row['value'])
        except Exception:
            return row['value']

def kv_set(key, value):
    with _lock:
        get_db().execute('INSERT OR REPLACE INTO kv(key,value) VALUES(?,?)', (key, json.dumps(value)))
        get_db().commit()

def add_sample(component, status, payload):
    with _lock:
        get_db().execute('INSERT INTO health_samples(ts,component,status,payload) VALUES(?,?,?,?)',
                         (time.time(), component, status, json.dumps(payload, ensure_ascii=False, default=str)))
        get_db().commit()

def add_incident(component, severity, summary, details=None):
    with _lock:
        get_db().execute('INSERT INTO incidents(ts,component,severity,summary,details) VALUES(?,?,?,?,?)',
                         (time.time(), component, severity, summary, json.dumps(details, ensure_ascii=False, default=str) if details is not None else None))
        get_db().commit()
        return get_db().execute('SELECT last_insert_rowid() as id').fetchone()['id']

def add_repair(component, action, tier, status, details=None, diagnosis=None):
    with _lock:
        get_db().execute('INSERT INTO repairs(ts,component,action,tier,status,details,diagnosis) VALUES(?,?,?,?,?,?,?)',
                         (time.time(), component, action, tier, status,
                          json.dumps(details, ensure_ascii=False, default=str) if details is not None else None,
                          diagnosis))
        get_db().execute('INSERT INTO action_log(ts,component,action,tier,status) VALUES(?,?,?,?,?)',
                         (time.time(), component, action, tier, status))
        get_db().commit()
        return get_db().execute('SELECT last_insert_rowid() as id').fetchone()['id']

def count_actions(component, window_sec=1800):
    since = time.time() - window_sec
    with _lock:
        row = get_db().execute('SELECT COUNT(*) as c FROM action_log WHERE component=? AND ts>=? AND status IN ("ok","executed","approved")',
                               (component, since)).fetchone()
        return row['c'] if row else 0

def recent_repairs(limit=50):
    with _lock:
        rows = get_db().execute('SELECT * FROM repairs ORDER BY ts DESC LIMIT ?', (limit,)).fetchall()
        return [dict(r) for r in rows]

def recent_incidents(limit=50):
    with _lock:
        rows = get_db().execute('SELECT * FROM incidents ORDER BY ts DESC LIMIT ?', (limit,)).fetchall()
        return [dict(r) for r in rows]

def recent_samples(component=None, limit=200):
    with _lock:
        if component:
            rows = get_db().execute('SELECT * FROM health_samples WHERE component=? ORDER BY ts DESC LIMIT ?', (component, limit)).fetchall()
        else:
            rows = get_db().execute('SELECT * FROM health_samples ORDER BY ts DESC LIMIT ?', (limit,)).fetchall()
        return [dict(r) for r in rows]

def save_pending_action(action_id, component, action, params, diagnosis):
    with _lock:
        get_db().execute('INSERT OR REPLACE INTO pending_actions(id,ts,component,action,params,diagnosis,status) VALUES(?,?,?,?,?,?,?)',
                         (action_id, time.time(), component, action, json.dumps(params, ensure_ascii=False, default=str), diagnosis, 'pending'))
        get_db().commit()

def get_pending_action(action_id):
    with _lock:
        row = get_db().execute('SELECT * FROM pending_actions WHERE id=?', (action_id,)).fetchone()
        return dict(row) if row else None

def set_pending_status(action_id, status):
    with _lock:
        get_db().execute('UPDATE pending_actions SET status=? WHERE id=?', (status, action_id))
        get_db().commit()

def find_active_tier2(component, action, cooldown_sec=1800):
    """Return a pending action if one exists for (component,action), else a recently ignored one within cooldown."""
    since = time.time() - cooldown_sec
    with _lock:
        row = get_db().execute(
            "SELECT * FROM pending_actions WHERE component=? AND action=? AND status='pending' ORDER BY ts DESC LIMIT 1",
            (component, action)
        ).fetchone()
        if row:
            return {'state': 'pending', 'row': dict(row)}
        row = get_db().execute(
            "SELECT * FROM pending_actions WHERE component=? AND action=? AND status='ignored' AND ts>=? ORDER BY ts DESC LIMIT 1",
            (component, action, since)
        ).fetchone()
        if row:
            return {'state': 'ignored_cooldown', 'row': dict(row)}
        row = get_db().execute(
            "SELECT * FROM repairs WHERE component=? AND action=? AND tier=2 AND status IN ('pending_approval','notified') AND ts>=? ORDER BY ts DESC LIMIT 1",
            (component, action, since)
        ).fetchone()
        if row:
            return {'state': 'recent_tier2', 'row': dict(row)}
    return None
