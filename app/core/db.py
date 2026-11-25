"""Portalın veritabanı: tek bir SQLite dosyası (DATA_DIR/portal.db).

Bütün tablolar aşağıdaki SCHEMA metninde, düz SQL olarak yazılır: "hangi veri nerede tutuluyor?"
sorusunun cevabı burada. Tablolar yoksa açılışta oluşturulur; varsa dokunulmaz (veri korunur).
Yeni tablo gerektiren her görev kendi tablosunu buraya ekler.
"""
import sqlite3
from pathlib import Path

SCHEMA = """
-- Kullanıcılar. Şifre sadece hash olarak (scrypt) durur.
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL DEFAULT '',
    role          TEXT NOT NULL CHECK (role IN ('operator', 'developer', 'admin')),
    password_hash TEXT NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1,
    last_login    TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Takılan API'ler. Kimlik bilgileri (auth_sealed) SECRET_KEY ile şifrelenmiş JSON'dur.
-- kind = 'custom' olan tek satır elle tanımlı endpoint'lerin sabit resource'udur.
CREATE TABLE IF NOT EXISTS resources (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    kind         TEXT NOT NULL CHECK (kind IN ('fastapi', 'custom')),
    openapi_url  TEXT NOT NULL DEFAULT '',
    base_url     TEXT NOT NULL DEFAULT '',
    auth_type    TEXT NOT NULL DEFAULT 'none',
    auth_sealed  TEXT NOT NULL DEFAULT '',
    timeout      REAL NOT NULL DEFAULT 30,
    last_fetched TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Resource'un son çekilen openapi.json'ı (olduğu gibi). Senkron bunu yenisiyle karşılaştırır.
CREATE TABLE IF NOT EXISTS schemas (
    resource_id INTEGER PRIMARY KEY,
    doc_json    TEXT NOT NULL,
    fetched_at  TEXT NOT NULL
);

-- Portalın tanıdığı endpoint'ler. id = kalıcı kimlik (atama, portal katmanı ve geçmiş buna bağlı).
-- Şemadaki endpoint'le eşleşme (resource_id, method, path) ile yapılır.
--   overlay_json: portal katmanı (sadece farklar)
--   custom_json : elle tanımlı endpoint'in tanımı (Custom resource'ta); diğerlerinde boş
CREATE TABLE IF NOT EXISTS endpoints (
    id           INTEGER PRIMARY KEY,
    resource_id  INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    method       TEXT NOT NULL,
    path         TEXT NOT NULL,
    selected     INTEGER NOT NULL DEFAULT 0,
    decided      INTEGER NOT NULL DEFAULT 0,
    removed      INTEGER NOT NULL DEFAULT 0,
    overlay_json TEXT NOT NULL DEFAULT '{}',
    custom_json  TEXT NOT NULL DEFAULT '',
    UNIQUE (resource_id, method, path)
);

-- İşlem geçmişi ve denetim kaydı. SİLİNMEZ (silen bir işlev yazılmaz).
-- Kişinin ve endpoint'in adı satırda saklanır: kişi silinse de kayıt okunur kalır (yabancı anahtar yok).
-- Gizli değerler yazılmadan önce maskelenir; kimlik başlıkları (Authorization, API anahtarı) hiç yazılmaz.
CREATE TABLE IF NOT EXISTS history (
    id            INTEGER PRIMARY KEY,
    at            TEXT NOT NULL,
    user_id       INTEGER NOT NULL,
    username      TEXT NOT NULL,
    user_name     TEXT NOT NULL,
    resource_id   INTEGER NOT NULL,
    endpoint_id   INTEGER NOT NULL,
    endpoint_key  TEXT NOT NULL,
    endpoint_name TEXT NOT NULL,
    values_json   TEXT NOT NULL,
    request_json  TEXT NOT NULL,
    outcome       TEXT NOT NULL,
    status        INTEGER,
    elapsed_ms    INTEGER NOT NULL,
    size          INTEGER NOT NULL,
    response_text TEXT NOT NULL,
    truncated     INTEGER NOT NULL DEFAULT 0,
    error_code    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS history_by_user ON history (user_id, id);

-- Portalın kendi ayarları (anahtar → değer): branding (ad, logo, palet), ileride işlev bandı vb.
-- Logo base64 metin olarak burada durur: yedek tek dosyadan ibaret kalır.
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Kullanıcının erişebildiği endpoint grupları: (resource, grup) çiftleri. Grup = OpenAPI tag'i.
-- Resource silinince satırları core/resources.py temizler.
CREATE TABLE IF NOT EXISTS user_groups (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    resource_id INTEGER NOT NULL,
    group_name  TEXT NOT NULL,
    PRIMARY KEY (user_id, resource_id, group_name)
);
"""


def connect(path: Path) -> sqlite3.Connection:
    """Veritabanını açar (klasör ve tablolar yoksa oluşturur)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: NiceGUI aynı bağlantıyı farklı iş parçacıklarından kullanabilir.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row                 # satırlar sütun adıyla okunur: row["name"]
    conn.execute("PRAGMA foreign_keys = ON")       # ON DELETE CASCADE çalışsın
    conn.executescript(SCHEMA)
    return conn
