"""Custom endpoint: şemada olmayan bir isteği elle tanımlamak (tasarim-v1 §8, SPEC §8 madde 9).

Tanım ekranı sade bir `CustomSpec` doldurur; burası onu bir OpenAPI işlemine çevirir (to_operation).
Saklanan şey o işlemdir (catalog.create_custom_endpoint): böylece Custom endpoint de şemadan gelenlerle aynı
yoldan forma, Docs'a ve isteğe dönüşür. Ekranın tekrar açılabilmesi için CustomSpec de yanında saklanır ("form").

Silmek kaydı yok etmez: endpoint menüden kalkar (removed), geçmiş kayıtları ona bağlı kalır.
"""
import json
import re
import sqlite3
from dataclasses import asdict

from core.errors import UserError
from core import catalog
from core.models import CustomParam, CustomSpec

METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]
LOCATIONS = ["path", "query", "header", "body"]

# Parametre tipi → JSON Schema (schema_form.py bunu yine aynı form bileşenine çevirir).
PARAM_KINDS = {
    "text": {"type": "string"},
    "textarea": {"type": "string", "maxLength": 10000},
    "number": {"type": "number"},
    "integer": {"type": "integer"},
    "toggle": {"type": "boolean"},
    "date": {"type": "string", "format": "date"},
    "datetime": {"type": "string", "format": "date-time"},
    "select": {"type": "string"},           # + enum: options
}

PATH_PARAM = re.compile(r"\{(\w+)\}")


def path_params(path: str) -> list[str]:
    """"/devices/{device_id}" → ["device_id"]"""
    return PATH_PARAM.findall(path)


def check_spec(spec: CustomSpec) -> None:
    """Kaydetmeden önce; hatalıysa ekranda gösterilecek ValueError."""
    if spec.method not in METHODS:
        raise UserError("errors.bad_method", method=spec.method)
    if not spec.path.startswith("/"):
        raise UserError("errors.path_slash")
    if not spec.base_url.startswith(("http://", "https://")):
        raise UserError("errors.address_scheme")
    if not spec.summary.strip():
        raise UserError("errors.name_empty")
    names = [p.name for p in spec.params]
    if any(not name.strip() for name in names) or len(names) != len(set(names)):
        raise UserError("errors.param_keys")
    for param in spec.params:
        if param.location not in LOCATIONS or param.kind not in PARAM_KINDS:
            raise UserError("errors.param_invalid", name=param.name)
        if param.kind == "select" and not param.options:
            raise UserError("errors.select_needs_options", name=param.name)
    if spec.method == "GET" and any(p.location == "body" for p in spec.params):
        raise UserError("errors.get_no_body")


def with_path_params(spec: CustomSpec) -> list[CustomParam]:
    """Path'teki {ad}'lar için parametre yoksa zorunlu metin olarak eklenir (unutulursa istek kurulamaz)."""
    params = list(spec.params)
    known = {p.name for p in params}
    for name in path_params(spec.path):
        if name not in known:
            params.insert(0, CustomParam(name=name, location="path", required=True))
    return params


def param_schema(param: CustomParam) -> dict:
    schema = dict(PARAM_KINDS[param.kind])
    if param.kind == "select":
        schema["enum"] = list(param.options)
    if param.label:
        schema["title"] = param.label
    if param.help:
        schema["description"] = param.help
    if param.default not in (None, ""):
        schema["default"] = param.default
    return schema


def to_operation(spec: CustomSpec) -> dict:
    """CustomSpec → OpenAPI işlemi (+ x-gadget ipuçları)."""
    params = with_path_params(spec)
    operation = {
        "tags": [spec.group or "custom"],
        "summary": spec.summary.strip(),
        "description": spec.description,
        "parameters": [{"name": p.name, "in": p.location, "required": p.location == "path" or p.required,
                        "schema": param_schema(p)} for p in params if p.location != "body"],
        "responses": {},
        "x-gadget": {key: value for key, value in {
            "view": spec.view, "list_path": spec.list_path, "confirm": spec.confirm,
            "read_from": spec.read_from, "row_action": spec.row_action}.items() if value not in ("", False, "auto")},
    }
    body = [p for p in params if p.location == "body"]
    if body:
        operation["requestBody"] = {"required": True, "content": {"application/json": {"schema": {
            "type": "object",
            "properties": {p.name: param_schema(p) for p in body},
            "required": [p.name for p in body if p.required],
        }}}}
    return operation


# =============================================================================
# Saklama
# =============================================================================

def save_custom(conn: sqlite3.Connection, custom_resource_id: int, spec: CustomSpec,
                endpoint_id: int | None = None) -> int:
    """Yeni ise ekler (hemen menüde), değilse günceller. Döner: kalıcı kimlik."""
    spec.method, spec.path = spec.method.upper(), spec.path.strip()
    check_spec(spec)
    if endpoint_id is None:
        endpoint_id = catalog.create_custom_endpoint(conn, custom_resource_id, spec.method, spec.path,
                                                     base_url=spec.base_url, operation=to_operation(spec),
                                                     auth_from=spec.auth_from)
    else:
        same = conn.execute("SELECT id FROM endpoints WHERE resource_id = ? AND method = ? AND path = ? AND id != ?",
                            (custom_resource_id, spec.method, spec.path, endpoint_id)).fetchone()
        if same:
            raise UserError("errors.endpoint_exists", method=spec.method, path=spec.path)
        definition = {"base_url": spec.base_url.rstrip("/"), "auth_from": spec.auth_from,
                      "operation": to_operation(spec), "schemas": {}}
        with conn:
            conn.execute("UPDATE endpoints SET method = ?, path = ?, custom_json = ? WHERE id = ?",
                         (spec.method, spec.path, json.dumps(definition), endpoint_id))
    store_form(conn, endpoint_id, spec)
    return endpoint_id


def store_form(conn: sqlite3.Connection, endpoint_id: int, spec: CustomSpec) -> None:
    definition = catalog.custom_definition(conn, endpoint_id)
    definition["form"] = asdict(spec)
    with conn:
        conn.execute("UPDATE endpoints SET custom_json = ? WHERE id = ?", (json.dumps(definition), endpoint_id))


def load_spec(conn: sqlite3.Connection, endpoint_id: int) -> CustomSpec:
    form = dict(catalog.custom_definition(conn, endpoint_id)["form"])
    form["params"] = [CustomParam(**p) for p in form.get("params", [])]
    return CustomSpec(**form)


def delete_custom(conn: sqlite3.Connection, endpoint_id: int) -> None:
    """Menüden kaldırır; kayıt ve geçmiş bağlantısı kalır."""
    with conn:
        conn.execute("UPDATE endpoints SET removed = 1, selected = 0 WHERE id = ? AND custom_json != ''",
                     (endpoint_id,))
