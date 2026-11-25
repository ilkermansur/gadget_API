"""Katalog: portalın tanıdığı endpoint'ler ve kimin neyi görüp çalıştırabileceği.

Parçaların birleştiği yer:
  şemayı çek (fetch_schema) → sakla, endpoint'lere kalıcı kimlik ver (store_schema)
  → admin seçer (save_selection) → geliştirici düzeltir (save_overlay / revert)
  → kullanıcının menüsü (menu_for) → yetki kontrolüyle çalıştır (run_endpoint)

Erişim kuralı (tasarim-v1 §1): endpoint seçili olmalı ve kullanıcının (resource, grup) atamalarından birine
düşmeli. Grup = portal katmanından sonraki grup (yani geliştirici bir endpoint'i başka gruba taşırsa erişim de
taşınır). Admin bütün grupları görür.

Custom endpoint: elle tanımlanır, tanımı bir OpenAPI işlemi olarak saklanır (custom_json). Böylece şemadan
gelen endpoint'lerle aynı yoldan (parse_openapi → build_view) forma dönüşür. Kendi tam adresi vardır;
kimliği olmaz ya da var olan bir resource'tan ödünç alınır (auth_from).
"""
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone

from core.errors import Msg, UserError
from core import history, resources
from core.caller import TokenCache, build_request, send_with_auth
from core.models import ApiDoc, CatalogEntry, EndpointView, Group, MenuItem, Overlay, Request, Resource, Result, User
from core.openapi import parse_openapi
from core.overlay import apply_overlay, build_view
from core.resources import SecretBox


# =============================================================================
# Şemayı çekmek ve saklamak
# =============================================================================

async def fetch_schema(resource: Resource, tokens: TokenCache) -> dict:
    """Resource'un openapi.json'ını kendi kimliğiyle çeker. Olmazsa ekranda gösterilecek ValueError."""
    result = await send_with_auth(Request("GET", resource.openapi_url), resource, tokens)
    if result.outcome != "ok" or not isinstance(result.body, dict) or "paths" not in result.body:
        reason = (result.error.msg or result.error.message) if result.error else Msg("errors.not_openapi")
        raise UserError("errors.schema_failed", reason=reason)
    return result.body


def store_schema(conn: sqlite3.Connection, resource_id: int, doc: dict) -> None:
    """Şemanın kopyasını saklar ve ilk kez görülen endpoint'lere kalıcı kimlik verir (seçilmemiş, kararsız).

    Var olan endpoint'lere dokunulmaz: seçim, portal katmanı ve kimlik korunur.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    parsed = parse_openapi(doc)
    with conn:
        conn.execute("INSERT OR REPLACE INTO schemas (resource_id, doc_json, fetched_at) VALUES (?, ?, ?)",
                     (resource_id, json.dumps(doc), now))
        conn.execute("UPDATE resources SET last_fetched = ? WHERE id = ?", (now, resource_id))
        conn.executemany("INSERT OR IGNORE INTO endpoints (resource_id, method, path) VALUES (?, ?, ?)",
                         [(resource_id, e.method, e.path) for e in parsed.endpoints])


def load_doc(conn: sqlite3.Connection, resource_id: int) -> ApiDoc | None:
    row = conn.execute("SELECT doc_json FROM schemas WHERE resource_id = ?", (resource_id,)).fetchone()
    return parse_openapi(json.loads(row["doc_json"])) if row else None


# =============================================================================
# Endpoint kayıtları, seçim, portal katmanı
# =============================================================================

def list_endpoints(conn: sqlite3.Connection, resource_id: int) -> list[CatalogEntry]:
    rows = conn.execute("SELECT * FROM endpoints WHERE resource_id = ? ORDER BY id", (resource_id,)).fetchall()
    return [to_entry(row) for row in rows]


def get_entry(conn: sqlite3.Connection, endpoint_id: int) -> CatalogEntry | None:
    row = conn.execute("SELECT * FROM endpoints WHERE id = ?", (endpoint_id,)).fetchone()
    return to_entry(row) if row else None


def to_entry(row: sqlite3.Row) -> CatalogEntry:
    return CatalogEntry(id=row["id"], resource_id=row["resource_id"], method=row["method"], path=row["path"],
                        selected=bool(row["selected"]), decided=bool(row["decided"]), removed=bool(row["removed"]),
                        custom=bool(row["custom_json"]))


def selection_counts(entries: list[CatalogEntry]) -> tuple[int, int, int]:
    """(seçili, hariç tutulan, karar bekleyen) — şemadan kalkmış olanlar sayılmaz. Resource tablosundaki özet."""
    live = [e for e in entries if not e.removed]
    selected = sum(e.selected for e in live)
    excluded = sum(e.decided and not e.selected for e in live)
    return selected, excluded, len(live) - selected - excluded


def save_selection(conn: sqlite3.Connection, resource_id: int, selected_ids: list[int]) -> None:
    """Menüye girecek endpoint'ler. Listede olmayanlar hariç tutulur; hepsi "karar verildi" sayılır."""
    with conn:
        conn.execute("UPDATE endpoints SET selected = 0, decided = 1 WHERE resource_id = ?", (resource_id,))
        conn.executemany("UPDATE endpoints SET selected = 1 WHERE id = ? AND resource_id = ?",
                         [(endpoint_id, resource_id) for endpoint_id in selected_ids])


def get_overlay(conn: sqlite3.Connection, endpoint_id: int) -> Overlay:
    row = conn.execute("SELECT overlay_json FROM endpoints WHERE id = ?", (endpoint_id,)).fetchone()
    data = json.loads(row["overlay_json"]) if row else {}
    return Overlay(endpoint=data.get("endpoint", {}), fields=data.get("fields", {}))


def save_overlay(conn: sqlite3.Connection, endpoint_id: int, overlay: Overlay) -> None:
    data = {"endpoint": overlay.endpoint, "fields": {path: changes for path, changes in overlay.fields.items() if changes}}
    with conn:
        conn.execute("UPDATE endpoints SET overlay_json = ? WHERE id = ?", (json.dumps(data), endpoint_id))


def revert(conn: sqlite3.Connection, endpoint_id: int, key: str, field_path: str | None = None) -> None:
    """"FastAPI'ye dön": portal katmanından tek bir düzeltmeyi kaldırır."""
    overlay = get_overlay(conn, endpoint_id)
    if field_path is None:
        overlay.endpoint.pop(key, None)
    else:
        overlay.fields.get(field_path, {}).pop(key, None)
    save_overlay(conn, endpoint_id, overlay)


# =============================================================================
# Custom endpoint
# =============================================================================

def create_custom_endpoint(conn: sqlite3.Connection, custom_resource_id: int, method: str, path: str, *,
                           base_url: str, operation: dict, auth_from: int | None = None,
                           schemas: dict | None = None) -> int:
    """Elle tanımlı endpoint ekler; hemen seçili olur (menüye girer). Döner: kalıcı kimlik."""
    method = method.upper()
    exists = conn.execute("SELECT 1 FROM endpoints WHERE resource_id = ? AND method = ? AND path = ?",
                          (custom_resource_id, method, path)).fetchone()
    if exists:
        raise UserError("errors.endpoint_exists", method=method, path=path)
    definition = {"base_url": base_url.rstrip("/"), "auth_from": auth_from, "operation": operation,
                  "schemas": schemas or {}}
    with conn:
        cursor = conn.execute(
            "INSERT INTO endpoints (resource_id, method, path, selected, decided, custom_json) VALUES (?, ?, ?, 1, 1, ?)",
            (custom_resource_id, method, path, json.dumps(definition)),
        )
    return cursor.lastrowid


def custom_definition(conn: sqlite3.Connection, endpoint_id: int) -> dict:
    row = conn.execute("SELECT custom_json FROM endpoints WHERE id = ?", (endpoint_id,)).fetchone()
    return json.loads(row["custom_json"]) if row and row["custom_json"] else {}


def custom_doc(entry: CatalogEntry, definition: dict) -> ApiDoc:
    """Custom tanımı tek işlemlik bir OpenAPI belgesine çevirir: geri kalan her şey aynı yoldan gider."""
    return parse_openapi({
        "openapi": "3.1.0", "info": {"title": "Custom", "version": ""},
        "paths": {entry.path: {entry.method.lower(): definition["operation"]}},
        "components": {"schemas": definition.get("schemas", {})},
    })


# =============================================================================
# Görünüm, menü, erişim
# =============================================================================

def endpoint_view(conn: sqlite3.Connection, entry: CatalogEntry, doc: ApiDoc | None = None) -> EndpointView | None:
    """Ekranda görünen hali: şemadaki (ya da Custom) tanım + portal katmanı. Şemada yoksa None."""
    if entry.custom:
        doc = custom_doc(entry, custom_definition(conn, entry.id))
    elif doc is None:
        doc = load_doc(conn, entry.resource_id)
    endpoint = next((e for e in doc.endpoints if e.key == entry.key), None) if doc else None
    if endpoint is None:
        return None
    return apply_overlay(build_view(endpoint, doc.schemas), get_overlay(conn, entry.id))


def has_access(user: User, resource_id: int, group: str) -> bool:
    return user.role == "admin" or (resource_id, group) in user.groups


def menu_for(conn: sqlite3.Connection, user: User, resource_id: int) -> list[MenuItem]:
    """Kullanıcının bu resource'ta görebildiği endpoint'ler (seçili, şemada var, grubuna erişimi var)."""
    doc = load_doc(conn, resource_id)
    items = []
    for entry in list_endpoints(conn, resource_id):
        if not entry.selected or entry.removed:
            continue
        view = endpoint_view(conn, entry, doc)
        if view is not None and has_access(user, resource_id, view.endpoint.group):
            items.append(MenuItem(endpoint_id=entry.id, resource_id=resource_id, view=view))
    return items


def assignable_groups(conn: sqlite3.Connection, resource_id: int) -> list[Group]:
    """Kullanıcılara atanabilecek gruplar: menüdeki (seçili) endpoint'lerin grupları, şemadaki sırayla.

    Grup portal katmanından sonraki gruptur: geliştiricinin taşıdığı yeni grup da burada çıkar.
    """
    doc = load_doc(conn, resource_id)
    known = {g.name: g for g in doc.groups} if doc else {}
    found: list[str] = []
    for entry in list_endpoints(conn, resource_id):
        if entry.selected and not entry.removed:
            view = endpoint_view(conn, entry, doc)
            if view is not None and view.endpoint.group not in found:
                found.append(view.endpoint.group)
    order = list(known)
    found.sort(key=lambda name: order.index(name) if name in order else len(order))
    return [known.get(name, Group(name=name)) for name in found]


def can_run(conn: sqlite3.Connection, user: User, endpoint_id: int) -> bool:
    entry = get_entry(conn, endpoint_id)
    if entry is None or not entry.selected or entry.removed:
        return False
    view = endpoint_view(conn, entry)
    return view is not None and has_access(user, entry.resource_id, view.endpoint.group)


# =============================================================================
# Çalıştırma
# =============================================================================

async def run_endpoint(conn: sqlite3.Connection, box: SecretBox, tokens: TokenCache, user: User,
                       endpoint_id: int, values: dict, json_body: object = None) -> Result:
    """Yetkiyi kontrol eder, isteği kurar, resource'un kimliğiyle gönderir ve geçmişe yazar.

    Yetki yoksa PermissionError: ekranda görünmeyen bir endpoint elle çağrılmaya çalışılsa bile reddedilir.
    Geçmişe kimlik EKLENMEDEN önceki istek yazılır (Authorization vb. kayda hiç girmez).
    """
    if not can_run(conn, user, endpoint_id):
        raise PermissionError("Bu endpoint'i çalıştırma yetkin yok.")
    entry = get_entry(conn, endpoint_id)
    view = endpoint_view(conn, entry)
    target = target_resource(conn, box, entry)
    request = build_request(view, values, target.base_url, json_body)
    result = await send_with_auth(request, target, tokens)
    result.history_id = history.record(conn, user, resource_id=entry.resource_id, endpoint_id=entry.id, endpoint_key=entry.key,
                                       endpoint_name=view.endpoint.summary, values=values, request=request,
                                       secret_names=history.secret_field_names(view.fields), result=result)
    return result


def preview_request(conn: sqlite3.Connection, box: SecretBox, endpoint_id: int, values: dict,
                    json_body: object = None) -> Request:
    """Gönderilecek isteğin kimlik eklenmemiş hali (onay penceresinde gösterilir). Hatalı girdide ValueError."""
    entry = get_entry(conn, endpoint_id)
    return build_request(endpoint_view(conn, entry), values, target_resource(conn, box, entry).base_url, json_body)


def target_resource(conn: sqlite3.Connection, box: SecretBox, entry: CatalogEntry) -> Resource:
    """İsteğin gideceği adres ve kullanılacak kimlik.

    Şemadan gelen endpoint: kendi resource'u. Custom: tanımdaki adres + ödünç kimlik (yoksa kimliksiz).
    """
    if not entry.custom:
        return resources.get_resource(conn, box, entry.resource_id)
    definition = custom_definition(conn, entry.id)
    lender = resources.get_resource(conn, box, definition["auth_from"]) if definition.get("auth_from") else None
    if lender is None:
        return Resource(id=entry.resource_id, name="Custom", kind="custom", base_url=definition["base_url"])
    # Ödünç kimlik: token önbelleği de ödünç verenin kimliğiyle paylaşılır (id aynı kalır).
    return replace(lender, base_url=definition["base_url"])
