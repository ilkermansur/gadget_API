"""Resource'lar: takılan API'lerin kaydı ve kimlik bilgileri.

Kimlik bilgileri (şifre, token, anahtar) veritabanına SECRET_KEY ile şifrelenerek yazılır (SecretBox).
Ekranda hiçbir zaman açık gösterilmez; tablolarda sadece `auth_summary` (tür + kullanıcı adı / header adı) görünür.
Kimliğin isteğe nasıl eklendiği core/caller.py `apply_auth` içindedir.

Kimlik türleri ve istedikleri bilgiler: AUTH_FIELDS. Süreli token alma/yenileme core/caller.py TokenCache'tedir.
"""
import base64
import hashlib
import json
import sqlite3

from cryptography.fernet import Fernet, InvalidToken

from core.errors import Term, UserError
from core.models import ApiDoc, Resource

CUSTOM_NAME = "Custom"   # elle tanımlı endpoint'lerin biriktiği sabit resource

# Her kimlik türünün zorunlu bilgileri.
AUTH_FIELDS = {
    "none": [],
    "basic": ["username", "password"],
    "bearer": ["token"],
    "apikey": ["key_name", "key_value"],       # + isteğe bağlı key_in: "header" (varsayılan) | "query"
    "token": ["token_url"],                      # + grant'e göre: TOKEN_GRANT_FIELDS; isteğe bağlı scope
}
AUTH_LABELS = {"none": "Yok", "basic": "Basic", "bearer": "Bearer", "apikey": "API anahtarı", "token": "Süreli token"}

# Süreli token'da hangi bilgilerle token alınacağı (OAuth2 grant türü).
TOKEN_GRANT_FIELDS = {
    "password": ["username", "password"],
    "client_credentials": ["client_id", "client_secret"],
}

# Gizli değerler: ekranda hiç gösterilmez; düzenlemede boş bırakılırsa eskisi korunur (merge_auth).
SECRET_KEYS = {"password", "token", "key_value", "client_secret"}

# Zorunlu olmayan ama formda sorulan bilgiler.
OPTIONAL_FIELDS = {"apikey": ["key_in"], "token": ["grant", "scope"]}

# OpenAPI securitySchemes → bizim kimlik türümüz (öneri için).
SCHEME_TO_AUTH = {("http", "basic"): "basic", ("http", "bearer"): "bearer", ("apiKey", ""): "apikey",
                  ("oauth2", ""): "token"}


# =============================================================================
# Şifreleme
# =============================================================================

class SecretBox:
    """Sözlüğü şifreleyip metne çevirir ve geri açar. Anahtar SECRET_KEY'den türetilir."""

    def __init__(self, secret_key: str):
        key = base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode()).digest())
        self.fernet = Fernet(key)

    def seal(self, data: dict) -> str:
        return self.fernet.encrypt(json.dumps(data).encode()).decode() if data else ""

    def open(self, sealed: str) -> dict:
        if not sealed:
            return {}
        try:
            return json.loads(self.fernet.decrypt(sealed.encode()))
        except InvalidToken:
            raise UserError("errors.secret_unreadable") from None


# =============================================================================
# Okuma
# =============================================================================

def get_resource(conn: sqlite3.Connection, box: SecretBox, resource_id: int) -> Resource | None:
    row = conn.execute("SELECT * FROM resources WHERE id = ?", (resource_id,)).fetchone()
    return to_resource(box, row) if row else None


def list_resources(conn: sqlite3.Connection, box: SecretBox) -> list[Resource]:
    # Custom en sonda, diğerleri ada göre.
    rows = conn.execute("SELECT * FROM resources ORDER BY kind = 'custom', name COLLATE NOCASE").fetchall()
    return [to_resource(box, row) for row in rows]


def to_resource(box: SecretBox, row: sqlite3.Row) -> Resource:
    return Resource(
        id=row["id"], name=row["name"], kind=row["kind"], openapi_url=row["openapi_url"],
        base_url=row["base_url"], auth_type=row["auth_type"], auth=box.open(row["auth_sealed"]),
        timeout=row["timeout"], last_fetched=row["last_fetched"],
    )


def auth_summary(resource: Resource) -> str:
    """Tabloda gösterilecek, gizli değer içermeyen özet: "Basic · svc"."""
    label = AUTH_LABELS.get(resource.auth_type, resource.auth_type)
    if resource.auth_type in ("basic", "token"):
        who = resource.auth.get("username") or resource.auth.get("client_id", "")   # token: kullanıcı ya da istemci
        return f"{label} · {who}"
    if resource.auth_type == "apikey":
        return f"{label} · {resource.auth.get('key_name', '')} ({resource.auth.get('key_in', 'header')})"
    return label


def suggest_auth_types(doc: ApiDoc) -> list[str]:
    """Şemanın securitySchemes bölümüne göre önerilen kimlik türleri (yeni resource formunda)."""
    suggested = []
    for scheme in doc.security_schemes.values():
        key = (scheme.get("type", ""), scheme.get("scheme", "").lower() if scheme.get("type") == "http" else "")
        auth_type = SCHEME_TO_AUTH.get(key)
        if auth_type and auth_type not in suggested:
            suggested.append(auth_type)
    return suggested


def auth_keys(auth_type: str, grant: str = "password") -> list[str]:
    """Formda sorulacak kimlik bilgileri, sırasıyla (token'da grant türüne göre değişir)."""
    keys = list(AUTH_FIELDS.get(auth_type, [])) + OPTIONAL_FIELDS.get(auth_type, [])
    if auth_type == "token":
        keys += TOKEN_GRANT_FIELDS.get(grant, [])
    return keys


def merge_auth(current: Resource | None, auth_type: str, entered: dict) -> dict:
    """Formdan gelen kimlik bilgisini kaydedilecek hale getirir.

    Gizli alanlar ekrana hiç gelmediği için düzenlemede boş görünür: tür aynı kaldıysa boş bırakılan gizli
    alan eskisini korur. Tür değiştiyse eski bilgiler taşınmaz. Boş değerler atılır.
    """
    keep_old = current is not None and current.auth_type == auth_type
    auth = {}
    for key, value in entered.items():
        value = value.strip() if isinstance(value, str) else value
        if not value and key in SECRET_KEYS and keep_old:
            value = current.auth.get(key, "")
        if value:
            auth[key] = value
    return auth


# =============================================================================
# Yazma
# =============================================================================

def ensure_custom_resource(conn: sqlite3.Connection, box: SecretBox) -> Resource:
    """Custom resource yoksa oluşturur (açılışta çağrılır)."""
    row = conn.execute("SELECT * FROM resources WHERE kind = 'custom'").fetchone()
    if row:
        return to_resource(box, row)
    with conn:
        cursor = conn.execute("INSERT INTO resources (name, kind) VALUES (?, 'custom')", (CUSTOM_NAME,))
    return get_resource(conn, box, cursor.lastrowid)


def create_resource(conn: sqlite3.Connection, box: SecretBox, name: str, openapi_url: str, *,
                    base_url: str = "", auth_type: str = "none", auth: dict | None = None,
                    timeout: float = 30.0) -> Resource:
    resource = Resource(id=0, name=name.strip(), kind="fastapi", openapi_url=openapi_url.strip(),
                        base_url=(base_url.strip() or base_from_openapi_url(openapi_url)).rstrip("/"),
                        auth_type=auth_type, auth=auth or {}, timeout=timeout)
    check_resource(conn, resource)
    with conn:
        cursor = conn.execute(
            "INSERT INTO resources (name, kind, openapi_url, base_url, auth_type, auth_sealed, timeout) "
            "VALUES (?, 'fastapi', ?, ?, ?, ?, ?)",
            (resource.name, resource.openapi_url, resource.base_url, resource.auth_type,
             box.seal(resource.auth), resource.timeout),
        )
    return get_resource(conn, box, cursor.lastrowid)


def update_resource(conn: sqlite3.Connection, box: SecretBox, resource_id: int, *, name: str | None = None,
                    openapi_url: str | None = None, base_url: str | None = None, auth_type: str | None = None,
                    auth: dict | None = None, timeout: float | None = None) -> Resource:
    """Verilen alanları değiştirir; verilmeyenler (kimlik bilgileri dahil) olduğu gibi kalır."""
    current = get_resource(conn, box, resource_id)
    if name is not None:
        current.name = name.strip()
    if openapi_url is not None:
        current.openapi_url = openapi_url.strip()
    if base_url is not None:
        current.base_url = base_url.strip().rstrip("/")
    if auth_type is not None:
        current.auth_type = auth_type
    if auth is not None:
        current.auth = auth
    if timeout is not None:
        current.timeout = timeout
    check_resource(conn, current)
    with conn:
        conn.execute(
            "UPDATE resources SET name = ?, openapi_url = ?, base_url = ?, auth_type = ?, auth_sealed = ?, "
            "timeout = ? WHERE id = ?",
            (current.name, current.openapi_url, current.base_url, current.auth_type, box.seal(current.auth),
             current.timeout, resource_id),
        )
    return get_resource(conn, box, resource_id)


def delete_resource(conn: sqlite3.Connection, resource_id: int) -> None:
    kind = conn.execute("SELECT kind FROM resources WHERE id = ?", (resource_id,)).fetchone()
    if kind and kind["kind"] == "custom":
        raise UserError("errors.custom_not_deletable")
    with conn:
        conn.execute("DELETE FROM user_groups WHERE resource_id = ?", (resource_id,))
        conn.execute("DELETE FROM resources WHERE id = ?", (resource_id,))


def check_resource(conn: sqlite3.Connection, resource: Resource) -> None:
    """Kaydetmeden önce kontrol: hatalıysa ekranda gösterilecek mesajla ValueError."""
    if not resource.name:
        raise UserError("errors.name_empty")
    if resource.kind == "fastapi" and not resource.openapi_url:
        raise UserError("errors.openapi_url_empty")
    if resource.auth_type not in AUTH_FIELDS:
        raise UserError("errors.bad_auth_type", auth_type=resource.auth_type)
    required = list(AUTH_FIELDS[resource.auth_type])
    if resource.auth_type == "token":
        grant = resource.auth.get("grant", "password")
        if grant not in TOKEN_GRANT_FIELDS:
            raise UserError("errors.bad_grant", grant=grant)
        required += TOKEN_GRANT_FIELDS[grant]
    missing = [key for key in required if not resource.auth.get(key)]
    if missing:
        raise UserError("errors.auth_missing", auth_type=Term("admin.auth_type.", [resource.auth_type]),
                        fields=Term("admin.auth_key.", missing))
    if resource.timeout <= 0:
        raise UserError("errors.timeout_positive")
    same_name = conn.execute("SELECT id FROM resources WHERE name = ? AND id != ?",
                             (resource.name, resource.id)).fetchone()
    if same_name:
        raise UserError("errors.resource_exists", name=resource.name)


def base_from_openapi_url(openapi_url: str) -> str:
    """"http://x:8000/openapi.json" → "http://x:8000" (şemanın durduğu klasör)."""
    url = openapi_url.strip()
    return url.rsplit("/", 1)[0] if url.endswith(".json") else url
