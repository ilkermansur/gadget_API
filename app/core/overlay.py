"""Ekranda görünen endpoint'i üç katmandan kurar (tasarim-v1 §13).

    1. OpenAPI şeması      → temel (core/openapi.py, core/schema_form.py)
    2. x-gadget ipuçları   → FastAPI yazarının ipuçları (core/openapi.py içinde zaten uygulanmış)
    3. Portal katmanı      → geliştirici/admin düzeltmeleri; son söz bunda (bu dosya)

Portal katmanı sadece aşağıdaki listelerde adı geçen anahtarları değiştirebilir. Listede olmayan her şey
kilitlidir (metot, path, alan adı, alan konumu...): bunlar değişirse hedef API'ye giden istek bozulur.
Bilinmeyen anahtar ya da artık var olmayan alan sessizce yok sayılır (senkron bunları ayrıca gösterir).
Değiştirilen her anahtar `overridden` kümesine yazılır: ekranda "portalda düzenlendi" etiketi buradan gelir.
"""
import copy
import re

from core.errors import UserError
from core.models import Endpoint, EndpointView, Field, Overlay
from core.schema_form import fields_from_body, fields_from_params

# Portalda düzeltilebilen endpoint anahtarları. Gerisi kilitli.
EDITABLE_ENDPOINT_KEYS = {
    "summary", "summary_en", "description", "description_en", "group",
    "view", "confirm", "read_from", "row_action", "list_path", "dry_run",
}

# Portalda düzeltilebilen alan anahtarları. `name` ve `location` kilitli.
EDITABLE_FIELD_KEYS = {
    "label", "label_en", "help", "help_en", "placeholder", "default",
    "kind", "options", "minimum", "maximum", "step", "max_length", "pattern", "required", "hidden",
}

# Alanın bileşeni sadece bu "düz" türler arasında değiştirilebilir. Nesne, liste gibi yapılar
# değiştirilemez: içlerindeki alanlar şemadan gelir, türü değişirse gönderilen JSON'un şekli bozulur.
SIMPLE_KINDS = {"text", "textarea", "number", "toggle", "select", "search", "date", "time", "datetime"}


def build_view(endpoint: Endpoint, schemas: dict) -> EndpointView:
    """Overlay'siz görünüm: endpoint + form alanları (önce parametreler, sonra gövde)."""
    fields = fields_from_params(endpoint.params, schemas) + fields_from_body(endpoint.body, schemas)
    return EndpointView(endpoint=endpoint, fields=fields)


def apply_overlay(base: EndpointView, overlay: Overlay) -> EndpointView:
    """Portal katmanını uygular. Gelen görünümü değiştirmez, yeni bir kopya döner."""
    view = copy.deepcopy(base)

    for key, value in overlay.endpoint.items():
        if key in EDITABLE_ENDPOINT_KEYS:
            setattr(view.endpoint, key, value)
            view.endpoint.overridden.add(key)

    for path, changes in overlay.fields.items():
        target = find_field(view.fields, path)
        if target is not None:
            apply_field_changes(target, changes)

    return view


def apply_field_changes(target: Field, changes: dict) -> None:
    for key, value in changes.items():
        if key not in EDITABLE_FIELD_KEYS:
            continue
        if key == "kind" and not (target.kind in SIMPLE_KINDS and value in SIMPLE_KINDS):
            continue
        setattr(target, key, value)
        target.overridden.add(key)


# =============================================================================
# Tanım ekranı: taslak portal katmanını düzenlemek
# =============================================================================

def set_override(overlay: Overlay, base: EndpointView, key: str, value, field_path: str | None = None) -> None:
    """Taslak portal katmanına bir değer yazar. Değer FastAPI'dekiyle aynıysa farkı siler: katman sadece
    farkları tutar, "FastAPI'ye dön" de böylece değeri FastAPI'dekine eşitlemek demektir.

    field_path None → endpoint anahtarı; değilse alan anahtarı (alan yolu: "limit", "location.city").
    Kilitli anahtar (listede olmayan) ValueError: ekran zaten sunmaz, bu son güvence.
    """
    if field_path is None:
        if key not in EDITABLE_ENDPOINT_KEYS:
            raise UserError("errors.locked_endpoint_key", name=key)
        base_value, target = getattr(base.endpoint, key), overlay.endpoint
    else:
        base_field = find_field(base.fields, field_path)
        if key not in EDITABLE_FIELD_KEYS or base_field is None:
            raise UserError("errors.locked_field_key", name=f"{field_path}.{key}")
        base_value, target = getattr(base_field, key), overlay.fields.setdefault(field_path, {})
    if value == base_value:
        target.pop(key, None)
    else:
        target[key] = value
    if field_path is not None and not target:
        overlay.fields.pop(field_path)


def coerce_default(field: Field, text: str) -> object:
    """Tanım ekranında yazılan varsayılan değeri alanın tipine çevirir. Boş → None (varsayılan yok)."""
    text = (text or "").strip()
    if not text:
        return None
    if field.kind == "number":
        number = float(text.replace(",", "."))
        return int(number) if field.integer or number.is_integer() else number
    if field.kind == "toggle":
        return text.lower() in ("true", "1", "evet", "yes", "açık")
    return text


def parse_options(text: str) -> list[str]:
    """"a, b, c" → ["a", "b", "c"] (seçenek listesi; boşlar atılır)."""
    return [part.strip() for part in (text or "").split(",") if part.strip()]


# =============================================================================
# Portal katmanında eklenen kısıtların gönderim öncesi kontrolü
# =============================================================================

# Bu anahtarlar portal katmanında değiştirildiyse gönderimden önce kontrol edilir. Şemadan gelen kısıtlar
# kontrol EDİLMEZ: onları FastAPI kontrol eder ve 422 ile söyler (tasarim-v1 §4.1).
CHECKED_KEYS = {"required", "minimum", "maximum", "pattern", "options"}


def portal_errors(view: EndpointView, values: dict) -> dict[str, tuple[str, dict]]:
    """Formun üst düzey alanları için: alan adı → (metin anahtarı, değerler). Hata yoksa boş sözlük.

    Mesaj metne çevrilmez (dil ekranın işi): ("form.min_value", {"n": 10}) → t("form.min_value", lang, n=10).
    """
    errors = {}
    for field in view.fields:
        if field.hidden or not (field.overridden & CHECKED_KEYS):
            continue
        problem = check_field(field, values.get(field.name))
        if problem:
            errors[field.name] = problem
    return errors


def check_field(field: Field, value: object) -> tuple[str, dict] | None:
    keys = field.overridden
    if value is None or value == "" or value == []:
        return ("form.required_missing", {}) if "required" in keys and field.required else None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in keys and field.minimum is not None and value < field.minimum:
            return "form.min_value", {"n": field.minimum}
        if "maximum" in keys and field.maximum is not None and value > field.maximum:
            return "form.max_value", {"n": field.maximum}
    if "options" in keys and field.options and value not in field.options:
        return "form.not_in_options", {}
    if "pattern" in keys and field.pattern and isinstance(value, str) and not re.search(field.pattern, value):
        return "form.pattern_mismatch", {"pattern": field.pattern}
    return None


def find_field(fields: list[Field], path: str) -> Field | None:
    """Noktalı yol ile alan bulur: "limit", "location.city". Gövdenin tamamı: ""."""
    if path == "":
        return next((f for f in fields if f.name == ""), None)

    current = None
    for name in path.split("."):
        current = next((f for f in fields if f.name == name), None)
        if current is None:
            return None
        fields = current.children
    return current
