"""İşlem geçmişi ve denetim kaydı: kim, hangi endpoint'i, hangi istekle çalıştırdı, ne döndü.

Kurallar (tasarim-v1 §5.2, SPEC §10):
  - Kayıtlar SİLİNMEZ. Bu dosyada silen bir işlev yoktur (bir test bunu korur).
  - Gizli değerler (format: password, writeOnly alanlar) yazılmadan önce MASK ile değiştirilir.
  - Kimlik başlıkları hiç yazılmaz: kayda kimlik eklenmeden ÖNCEKİ istek yazılır.
  - Yanıt gövdesi en fazla MAX_BODY bayt saklanır; fazlası kesilir, kayıt "kesildi" diye işaretlenir.
  - Kişinin adı kayıtta saklanır; kişi silinse de kayıt okunur, ekranda "(silindi)" görünür.
"""
import csv
import io
import json
import sqlite3
from datetime import datetime, timezone

from core.caller import extract_error
from core.models import ErrorSummary, Field, HistoryItem, Request, Result, User

MASK = "••••••"
MAX_BODY = 1_000_000            # 1 MB

# Dönüş kodu filtresi → SQL koşulu.
STATUS_CLASSES = {
    "2xx": "h.status BETWEEN 200 AND 299",
    "4xx": "h.status BETWEEN 400 AND 499",
    "5xx": "h.status BETWEEN 500 AND 599",
    "timeout": "h.outcome = 'timeout'",
    "connection_error": "h.outcome = 'connection_error'",
}


# =============================================================================
# Yazma
# =============================================================================

def record(conn: sqlite3.Connection, user: User, *, resource_id: int, endpoint_id: int, endpoint_key: str,
           endpoint_name: str, values: dict, request: Request, secret_names: set[str], result: Result,
           max_body: int = MAX_BODY) -> int:
    """Bir çalıştırmayı yazar ve kaydın numarasını döner. `request` kimlik EKLENMEMİŞ istek olmalı."""
    stored_request = {
        "method": request.method,
        "url": request.url,
        "params": mask(request.params, secret_names),
        "headers": mask(request.headers, secret_names),
        "json": mask(request.json, secret_names),
        "data": mask(request.data, secret_names),
        "files": {name: file[0] if isinstance(file, tuple) else "…" for name, file in request.files.items()},
    }
    body = result.text.encode()
    text = body[:max_body].decode(errors="ignore")
    with conn:
        cursor = conn.execute(
            "INSERT INTO history (at, user_id, username, user_name, resource_id, endpoint_id, endpoint_key, "
            "endpoint_name, values_json, request_json, outcome, status, elapsed_ms, size, response_text, truncated, "
            "error_code) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now(), user.id, user.username, user.name, resource_id, endpoint_id, endpoint_key, endpoint_name,
             json.dumps(mask(values, secret_names), default=str), json.dumps(stored_request, default=str),
             result.outcome, result.status, result.elapsed_ms, result.size, text, int(len(body) > max_body),
             result.error.code if result.error else ""),
        )
    return cursor.lastrowid


def mask(value: object, secret_names: set[str]) -> object:
    """Sözlükte (iç içe olanlar dahil) adı gizli olan anahtarların değerini MASK yapar."""
    if isinstance(value, dict):
        return {k: MASK if k in secret_names and v not in (None, "") else mask(v, secret_names) for k, v in value.items()}
    if isinstance(value, list):
        return [mask(item, secret_names) for item in value]
    return value


def secret_field_names(fields: list[Field]) -> set[str]:
    """Formdaki gizli alanların adları (iç içe alanlar dahil): şifre biçimli ya da writeOnly."""
    names = set()
    for field in fields:
        if field.format == "password" or field.write_only:
            names.add(field.name)
        names |= secret_field_names(field.children)
    return names


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# =============================================================================
# Okuma
# =============================================================================

def list_history(conn: sqlite3.Connection, *, user_id: int | None = None, endpoint_id: int | None = None,
                 status_class: str | None = None, limit: int = 500) -> list[HistoryItem]:
    """En yeniden eskiye. user_id verilmezse herkesinki (admin denetimi)."""
    conditions, params = [], []
    if user_id is not None:
        conditions.append("h.user_id = ?")
        params.append(user_id)
    if endpoint_id is not None:
        conditions.append("h.endpoint_id = ?")
        params.append(endpoint_id)
    if status_class:
        conditions.append(STATUS_CLASSES[status_class])
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    rows = conn.execute(
        f"SELECT h.*, u.id IS NULL AS user_deleted FROM history h LEFT JOIN users u ON u.id = h.user_id "
        f"{where} ORDER BY h.id DESC LIMIT ?",
        (*params, limit),
    ).fetchall()
    return [to_item(row) for row in rows]


def to_item(row: sqlite3.Row) -> HistoryItem:
    return HistoryItem(
        id=row["id"], at=row["at"], user_id=row["user_id"], username=row["username"], user_name=row["user_name"],
        user_deleted=bool(row["user_deleted"]), resource_id=row["resource_id"], endpoint_id=row["endpoint_id"],
        endpoint_key=row["endpoint_key"], endpoint_name=row["endpoint_name"], values=json.loads(row["values_json"]),
        request=json.loads(row["request_json"]), outcome=row["outcome"], status=row["status"],
        elapsed_ms=row["elapsed_ms"], size=row["size"], response_text=row["response_text"],
        truncated=bool(row["truncated"]), error_code=row["error_code"],
    )


# =============================================================================
# CSV
# =============================================================================

CSV_HEADER = ["Zaman", "Kullanıcı", "Metot", "Endpoint", "Ad", "Parametreler", "Sonuç", "Süre (ms)"]


def get_own_item(conn: sqlite3.Connection, item_id: int, user_id: int) -> HistoryItem | None:
    """Kişinin kendi kaydı; başkasınınsa ya da yoksa None (sayfa yenilenince son sonucu geri getirmek için)."""
    row = conn.execute("SELECT *, 0 AS user_deleted FROM history WHERE id = ? AND user_id = ?",
                       (item_id, user_id)).fetchone()
    return to_item(row) if row else None


def to_result(item: HistoryItem) -> Result:
    """Kayıttan ekranda gösterilecek sonucu yeniden kurar. Başlıklar saklanmadığı için boştur;
    zaman aşımı / bağlantı hatası mesajının ayrıntısı da saklanmaz, sadece kodu."""
    try:
        body = json.loads(item.response_text)
    except ValueError:
        body = item.response_text
    error = None
    if item.outcome != "ok":
        error = extract_error(item.status, body, item.response_text) if item.status is not None \
            else ErrorSummary(code=item.error_code or item.outcome, message="")
    return Result(outcome=item.outcome, status=item.status, elapsed_ms=item.elapsed_ms, size=item.size, body=body,
                  text=item.response_text, error=error, history_id=item.id)


def restorable_values(values: dict) -> dict:
    """Forma geri doldurulabilecek değerler: maskelenmiş (gizli) bir şey içerenler atlanır."""
    return {name: value for name, value in values.items() if MASK not in json.dumps(value, default=str, ensure_ascii=False)}


def to_csv(items: list[HistoryItem]) -> str:
    """Görünen listeyi CSV'ye çevirir. UTF-8 BOM + ";" ayırıcı: Excel Türkçe karakterleri doğru açar."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\n")
    writer.writerow(CSV_HEADER)
    for item in items:
        method, path = item.endpoint_key.split(" ", 1)
        who = item.user_name + (" (silindi)" if item.user_deleted else "")
        writer.writerow([item.at, who, method, path, item.endpoint_name,
                         json.dumps(item.values, ensure_ascii=False, default=str),
                         item.status if item.status is not None else item.outcome, item.elapsed_ms])
    return "﻿" + out.getvalue()
