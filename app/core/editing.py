"""Kayıt düzenleme akışının mantığı (tasarim-v1 §6): hangi PATCH, hangi okuma GET'i, hangi kayıt, ne değişti.

Akış: liste satırındaki "Düzenle" ya da PATCH formundaki "Getir ve düzenle"
  → EditLink (PATCH endpoint'i + okuma GET'i + path parametresinin adı)
  → GET ile mevcut değerler → kullanıcı değiştirir → changed_values → PATCH sadece değişenlerle

Bağlantı önce x-gadget'tan (ya da portal katmanından) gelir: liste için `row_action`, PATCH için `read_from`.
Yoksa path'ten tahmin edilir: GET /x listesi → PATCH /x/{…}; PATCH /x/{id} → GET /x/{id}.
Kullanıcının çalıştıramadığı endpoint bağlantıya girmez (yetki atlanamaz).

İç içe kaynakta (/switches/{switch}/interfaces/{name}) kaydı belirten, yoldaki SON parametredir; üstündekiler
(switch) bağlamdır: listeyi çekerken kullanılan değerleriyle okuma GET'ine ve PATCH'e aynen taşınır.
"""
import re
import sqlite3
from dataclasses import dataclass, field

from core import catalog
from core.models import Field, MenuItem, User


@dataclass
class EditLink:
    patch_id: int           # gönderilecek PATCH (ya da PUT) endpoint'inin kimliği
    read_id: int            # mevcut değerlerin okunduğu GET endpoint'inin kimliği
    param: str              # kaydı belirten path parametresi (yoldaki son), ör. "device_id"
    context: list[str] = field(default_factory=list)   # yolda ondan önceki parametreler, ör. ["switch"]


def link_for_list(conn: sqlite3.Connection, user: User, listing: MenuItem) -> EditLink | None:
    """Liste endpoint'inin satır eylemi: hangi PATCH ile düzenlenir."""
    menu = catalog.menu_for(conn, user, listing.resource_id)
    endpoint = listing.view.endpoint
    patch = find(menu, endpoint.row_action) or next(
        (m for m in menu if m.view.endpoint.method in ("PATCH", "PUT")
         and parent_path(m.view.endpoint.path) == endpoint.path.rstrip("/")), None)
    return link_for_patch(conn, user, patch) if patch else None


def link_for_patch(conn: sqlite3.Connection, user: User, patch: MenuItem) -> EditLink | None:
    """PATCH endpoint'inin okuma GET'i ve path parametresi."""
    menu = catalog.menu_for(conn, user, patch.resource_id)
    endpoint = patch.view.endpoint
    read = find(menu, endpoint.read_from) or find(menu, f"GET {endpoint.path}")
    params = path_params(endpoint.path)
    if read is None or not params:
        return None
    return EditLink(patch_id=patch.endpoint_id, read_id=read.endpoint_id, param=params[-1], context=params[:-1])


def path_params(path: str) -> list[str]:
    """Yoldaki parametre adları, yoldaki sırayla: "/s/{switch}/i/{name}" → ["switch", "name"]."""
    return re.findall(r"\{([^}:]+)", path)


def context_values(link: EditLink, values: dict) -> dict:
    """Bağlam parametrelerinin değerleri (listeyi ya da formu çalıştırırken girilenler); boş olanlar alınmaz."""
    return {name: values[name] for name in link.context if values.get(name) not in (None, "")}


def find(menu: list[MenuItem], key: str) -> MenuItem | None:
    return next((m for m in menu if key and m.view.endpoint.key == key), None)


def parent_path(path: str) -> str:
    """"/devices/{device_id}" → "/devices" (son parça bir {parametre} ise)."""
    head, _, last = path.rstrip("/").rpartition("/")
    return head if last.startswith("{") else ""


def record_id(row: dict, param: str) -> object:
    """Tablo satırından kaydın kimliği: önce parametrenin adı, yoksa "id"."""
    return row.get(param, row.get("id"))


def changed_values(before: dict, after: dict) -> dict:
    """Sadece değişen alanlar (PATCH gövdesi). Önceden olmayan alanın boş kalması değişiklik sayılmaz."""
    return {key: value for key, value in after.items()
            if before.get(key) != value and not (key not in before and value in (None, ""))}


def prefill_values(fields: list[Field], current: dict) -> dict:
    """Düzenleme formunun başlangıç değerleri: GET'in döndürdüğü değer alana uymuyorsa (sayı alanında "trunk",
    seçenekte olmayan değer) boş kalır. Karşılaştırma da bu değerlerle yapılır; boş bırakılan alan değişiklik sayılmaz."""
    return {f.name: current.get(f.name) if fits(f, current.get(f.name)) else None for f in fields}


def fits(field: Field, value: object) -> bool:
    if value is None:
        return True
    if field.kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if field.kind == "toggle":
        return isinstance(value, bool)
    if field.kind in ("select", "search"):
        return value in field.options
    if field.kind in ("text", "textarea", "date", "time", "datetime"):
        return isinstance(value, str)
    return True
