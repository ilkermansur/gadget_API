"""Senkron: FastAPI'nin şeması değişince portalın ne yapacağı (tasarim-v1 §9.1).

İki adım, iki fonksiyon:
  plan_sync  → saklı şema ile yenisini karşılaştırır, HİÇBİR ŞEYİ değiştirmeden önizleme (SyncPlan) döner
  apply_sync → admin'in kararlarıyla uygular; her yeni endpoint ve her çakışma için karar yoksa reddeder

Kurallar:
  - Seçim korunur. Sadece HİÇ görülmemiş endpoint'ler için "Ekle / Ekleme" sorulur; verilen karar
    kaydedilir, ikinci senkronda aynı şey sorulmaz.
  - Seçili endpoint'lerdeki değişiklikler listelenir ve uygulanır.
  - FastAPI'de artık olmayan endpoint menüden kalkar (kimliği, düzeltmeleri, geçmişi silinmez);
    geri gelirse menüye döner.
  - Portalda düzeltilmiş bir değeri FastAPI de değiştirdiyse ÇAKIŞMA: sessizce ezilmez, sorulur.
  - Eşleştirme metot + path ile yapılır: path değişirse "silinen + yeni" görünür.
"""
import sqlite3
from dataclasses import fields as dataclass_fields

from core.errors import UserError
from core import catalog
from core.models import Conflict, EndpointView, Field, SyncChange, SyncPlan
from core.openapi import parse_openapi
from core.overlay import EDITABLE_ENDPOINT_KEYS, build_view, find_field

# Karşılaştırmada bakılmayan Field anahtarları (portal katmanına ait, şemadan gelmez).
IGNORED_FIELD_KEYS = {"overridden", "hidden"}


def plan_sync(conn: sqlite3.Connection, resource_id: int, new_doc: dict) -> SyncPlan:
    old = catalog.load_doc(conn, resource_id)
    new = parse_openapi(new_doc)
    old_by_key = {e.key: e for e in old.endpoints} if old else {}
    new_by_key = {e.key: e for e in new.endpoints}
    entries = [e for e in catalog.list_endpoints(conn, resource_id) if not e.custom]
    known = {e.key for e in entries}

    plan = SyncPlan(
        new=[e for e in new.endpoints if e.key not in known],
        changed=[], conflicts=[],
        removed=[e for e in entries if not e.removed and e.key not in new_by_key],
        returned=[e for e in entries if e.removed and e.key in new_by_key],
        kept_selected=sum(1 for e in entries if e.selected and not e.removed),
        kept_excluded=sum(1 for e in entries if not e.selected and not e.removed),
    )

    for entry in entries:
        if not entry.selected or entry.removed or entry.key not in new_by_key or entry.key not in old_by_key:
            continue
        old_view = build_view(old_by_key[entry.key], old.schemas)
        new_view = build_view(new_by_key[entry.key], new.schemas)
        changes = describe_changes(old_view, new_view)
        if changes:
            plan.changed.append(SyncChange(entry.id, entry.key, changes))
            plan.conflicts += find_conflicts(conn, entry.id, entry.key, old_view, new_view)
    return plan


def apply_sync(conn: sqlite3.Connection, resource_id: int, new_doc: dict, *, add: dict[str, bool],
               conflicts: dict[str, str]) -> SyncPlan:
    """Kararlarla uygular.

    add:       yeni endpoint anahtarı ("GET /alarms") → True (menüye ekle) / False (ekleme)
    conflicts: Conflict.id → "portal" (düzeltme kalsın) / "fastapi" (düzeltmeyi kaldır, FastAPI'deki geçerli)
    """
    plan = plan_sync(conn, resource_id, new_doc)
    missing = undecided(plan, add, conflicts)
    if missing:
        raise UserError("errors.sync_undecided", items=", ".join(missing))

    catalog.store_schema(conn, resource_id, new_doc)       # yeni endpoint'ler kimlik alır
    with conn:
        for endpoint in plan.new:
            conn.execute("UPDATE endpoints SET decided = 1, selected = ? WHERE resource_id = ? AND method = ? AND path = ?",
                         (int(add[endpoint.key]), resource_id, endpoint.method, endpoint.path))
        conn.executemany("UPDATE endpoints SET removed = 1 WHERE id = ?", [(e.id,) for e in plan.removed])
        conn.executemany("UPDATE endpoints SET removed = 0 WHERE id = ?", [(e.id,) for e in plan.returned])
    for conflict in plan.conflicts:
        if conflicts[conflict.id] == "fastapi":
            catalog.revert(conn, conflict.endpoint_id, conflict.key, conflict.field_path)
    return plan


def undecided(plan: SyncPlan, add: dict[str, bool], conflicts: dict[str, str]) -> list[str]:
    """Kararı verilmemiş yeni endpoint'ler ve çakışmalar. Boş değilse uygulanamaz (ekranda "Uygula" kapalı)."""
    return [e.key for e in plan.new if e.key not in add] + \
           [c.id for c in plan.conflicts if conflicts.get(c.id) not in ("portal", "fastapi")]


# =============================================================================
# Karşılaştırma
# =============================================================================

def describe_changes(old: EndpointView, new: EndpointView) -> list[str]:
    """İki görünüm arasındaki farklar: endpoint anahtarı ("description") ya da "field_added:ad" biçiminde."""
    changes = [key for key in sorted(EDITABLE_ENDPOINT_KEYS)
               if getattr(old.endpoint, key) != getattr(new.endpoint, key)]
    old_fields = {f.name: f for f in old.fields}
    new_fields = {f.name: f for f in new.fields}
    # Dil ekranda seçilir: "description" (endpoint anahtarı) ya da "field_changed:limit" ("" = gövdenin tamamı).
    changes += [f"field_added:{name}" for name in new_fields if name not in old_fields]
    changes += [f"field_removed:{name}" for name in old_fields if name not in new_fields]
    changes += [f"field_changed:{name}" for name in old_fields
                if name in new_fields and comparable(old_fields[name]) != comparable(new_fields[name])]
    return changes


def comparable(field: Field) -> dict:
    """Bir alanın şemadan gelen bilgileri (portal katmanına ait anahtarlar hariç)."""
    return {f.name: getattr(field, f.name) for f in dataclass_fields(field) if f.name not in IGNORED_FIELD_KEYS}


def find_conflicts(conn: sqlite3.Connection, endpoint_id: int, endpoint_key: str,
                   old: EndpointView, new: EndpointView) -> list[Conflict]:
    """Portal katmanında düzeltilmiş olup FastAPI'de de değişen değerler."""
    overlay = catalog.get_overlay(conn, endpoint_id)
    found = []
    for key, portal_value in overlay.endpoint.items():
        before, after = getattr(old.endpoint, key, None), getattr(new.endpoint, key, None)
        if before != after:
            found.append(Conflict(endpoint_id, endpoint_key, key, None, before, after, portal_value))
    for path, changes in overlay.fields.items():
        old_field, new_field = find_field(old.fields, path), find_field(new.fields, path)
        if old_field is None or new_field is None:
            continue                                    # alan silindiyse/yeniyse "alan silindi/eklendi" yeter
        for key, portal_value in changes.items():
            before, after = getattr(old_field, key, None), getattr(new_field, key, None)
            if before != after:
                found.append(Conflict(endpoint_id, endpoint_key, key, path, before, after, portal_value))
    return found
