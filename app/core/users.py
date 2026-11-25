"""Kullanıcılar: oluşturma, düzenleme, şifre, giriş, roller ve endpoint grupları.

Erişim iki eksenlidir (tasarim-v1 §1):
  rol            = NE yapabilir   → "operator" ⊂ "developer" ⊂ "admin"
  endpoint grubu = NEREDE yapabilir → (resource_id, tag) çiftleri; menü buna göre süzülür
Hatalı girdi ValueError ile reddedilir; mesaj doğrudan ekranda gösterilebilir.
Not: giriş şimdilik yerel kullanıcılarla. İleride LDAP/AD eklenecekse `login` tek kapıdır.
"""
import hashlib
import hmac
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from core.errors import UserError
from core.models import User

ROLES = ["operator", "developer", "admin"]       # sıra = yetki sırası (sonraki öncekini kapsar)

# scrypt ayarları: (n, r, p). Değişirse eski hash'ler kendi ayarlarıyla doğrulanmaya devam eder.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1


# =============================================================================
# Şifre
# =============================================================================

def hash_password(password: str) -> str:
    """"scrypt$n$r$p$tuz$hash" biçiminde, her seferinde farklı tuzla."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    _, n, r, p, salt, digest = stored.split("$")
    candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p))
    return hmac.compare_digest(candidate.hex(), digest)     # sabit süreli karşılaştırma


# =============================================================================
# Roller
# =============================================================================

def has_role(role: str, minimum: str) -> bool:
    """Bu rol en az `minimum` kadar yetkili mi? (admin ⊃ developer ⊃ operator)"""
    return ROLES.index(role) >= ROLES.index(minimum)


def visible_profiles(role: str) -> list[str]:
    """Üst bardaki profil sekmeleri: kişinin rolü ve altındakiler."""
    return ROLES[: ROLES.index(role) + 1]


# =============================================================================
# Okuma
# =============================================================================

def get_user(conn: sqlite3.Connection, user_id: int) -> User | None:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return to_user(conn, row) if row else None


def find_by_username(conn: sqlite3.Connection, username: str) -> User | None:
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return to_user(conn, row) if row else None


def list_users(conn: sqlite3.Connection) -> list[User]:
    rows = conn.execute("SELECT * FROM users ORDER BY name COLLATE NOCASE").fetchall()
    return [to_user(conn, row) for row in rows]


def to_user(conn: sqlite3.Connection, row: sqlite3.Row) -> User:
    groups = conn.execute(
        "SELECT resource_id, group_name FROM user_groups WHERE user_id = ? ORDER BY resource_id, rowid",
        (row["id"],),
    ).fetchall()
    return User(
        id=row["id"], username=row["username"], name=row["name"], role=row["role"], email=row["email"],
        active=bool(row["active"]), last_login=row["last_login"],
        groups=[(g["resource_id"], g["group_name"]) for g in groups],
    )


# =============================================================================
# Yazma
# =============================================================================

def create_user(conn: sqlite3.Connection, username: str, name: str, password: str, role: str,
                email: str = "") -> User:
    username = username.strip()
    if not username:
        raise UserError("errors.username_empty")
    if not password:
        raise UserError("errors.password_empty")
    check_role(role)
    if find_by_username(conn, username):
        raise UserError("errors.username_exists", username=username)
    with conn:
        cursor = conn.execute(
            "INSERT INTO users (username, name, email, role, password_hash) VALUES (?, ?, ?, ?, ?)",
            (username, name.strip() or username, email.strip(), role, hash_password(password)),
        )
    return get_user(conn, cursor.lastrowid)


def update_user(conn: sqlite3.Connection, user_id: int, *, name: str, email: str, role: str,
                acting_user_id: int | None = None) -> User:
    """Kullanıcı adı değişmez (giriş kimliği ve geçmiş kayıtları ona bağlı).

    Admin kendi rolünü düşüremez: yoksa yönetime kimse giremeyebilir.
    """
    check_role(role)
    if user_id == acting_user_id and role != "admin" and get_user(conn, user_id).role == "admin":
        raise UserError("errors.own_admin_role")
    with conn:
        conn.execute("UPDATE users SET name = ?, email = ?, role = ? WHERE id = ?",
                     (name.strip(), email.strip(), role, user_id))
    return get_user(conn, user_id)


def set_password(conn: sqlite3.Connection, user_id: int, password: str) -> None:
    if not password:
        raise UserError("errors.password_empty")
    with conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user_id))


def set_active(conn: sqlite3.Connection, user_id: int, active: bool, acting_user_id: int | None = None) -> None:
    if not active and user_id == acting_user_id:
        raise UserError("errors.own_deactivate")
    with conn:
        conn.execute("UPDATE users SET active = ? WHERE id = ?", (int(active), user_id))


def delete_user(conn: sqlite3.Connection, user_id: int, acting_user_id: int) -> None:
    """Kalıcı siler. Geçmiş kayıtları silinmez (geçmiş kullanıcının adını kendi içinde saklar)."""
    if user_id == acting_user_id:
        raise UserError("errors.own_delete")
    with conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def set_groups(conn: sqlite3.Connection, user_id: int, groups: list[tuple[int, str]]) -> None:
    """Kullanıcının erişebildiği grupları bütünüyle değiştirir."""
    with conn:
        conn.execute("DELETE FROM user_groups WHERE user_id = ?", (user_id,))
        conn.executemany("INSERT OR IGNORE INTO user_groups (user_id, resource_id, group_name) VALUES (?, ?, ?)",
                         [(user_id, resource_id, group) for resource_id, group in groups])


def check_role(role: str) -> None:
    if role not in ROLES:
        raise UserError("errors.bad_role", role=role)


# =============================================================================
# Giriş
# =============================================================================

@dataclass
class LoginResult:
    ok: bool
    user: User | None = None
    reason: str = ""            # "" | "unknown_user" | "wrong_password" | "inactive"


def login(conn: sqlite3.Connection, username: str, password: str) -> LoginResult:
    row = conn.execute("SELECT id, password_hash, active FROM users WHERE username = ?",
                       (username.strip(),)).fetchone()
    if row is None:
        return LoginResult(ok=False, reason="unknown_user")
    if not verify_password(password, row["password_hash"]):
        return LoginResult(ok=False, reason="wrong_password")
    if not row["active"]:
        return LoginResult(ok=False, reason="inactive")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with conn:
        conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (now, row["id"]))
    return LoginResult(ok=True, user=get_user(conn, row["id"]))


def needs_first_admin(conn: sqlite3.Connection) -> bool:
    """Veritabanında hiç kullanıcı yok mu? (Öyleyse ve ADMIN_USER/ADMIN_PASSWORD boşsa kimse giremez.)"""
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def ensure_first_admin(conn: sqlite3.Connection, username: str, password: str) -> bool:
    """Veritabanında hiç kullanıcı yoksa ilk admin'i oluşturur (ADMIN_USER / ADMIN_PASSWORD).

    Döner: oluşturulduysa True. Kullanıcı varsa ya da bilgiler boşsa hiçbir şey yapmaz.
    """
    has_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0
    if has_users or not username or not password:
        return False
    create_user(conn, username, "Admin", password, "admin")
    return True
