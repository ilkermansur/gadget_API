"""JSON Schema alanını formdaki bir alana (Field) çevirir.

Hangi şemanın hangi form bileşenine dönüştüğü burada karar verilir; tablo: docs/tasarim-v1.md §4.2.
Ekrana çizmek bu dosyanın işi değil (ui/fields.py). Doğrulama da yapılmaz: kısıtlar (min, max, pattern...)
sadece taşınır, asıl kontrolü FastAPI yapar.

Bir şema alana dönüşürken dört adımdan geçer (field_from_schema):
  1. resolve          → $ref'leri ve allOf'u çöz, tek düz şema elde et
  2. unwrap_nullable  → "X ya da null" yapısını aç: alan X'tir, ama boş gönderilebilir
  3. choose_kind      → formda hangi bileşen çizilecek
  4. doldurma         → kısıtlar, seçenekler, alt alanlar (object / liste / union)

Tanınmayan ya da sonsuza giden (kendini içeren) yapı "json" alanına düşer: kullanıcı değeri JSON olarak yazar.
Uygulama hiçbir şemada hata verip durmaz.
"""
from core.models import Body, Field, Param

ENUM_SELECT_LIMIT = 8       # bu sayıya kadar açılır liste, fazlası yazarak aranan liste
TEXTAREA_MIN_LENGTH = 200   # maxLength bunu aşarsa çok satırlı metin
DATE_FORMATS = {"date": "date", "time": "time", "date-time": "datetime"}


# --- Giriş noktaları: endpoint'in parametreleri ve gövdesi -----------------------------

def fields_from_params(params: list[Param], schemas: dict | None = None) -> list[Field]:
    """Path / query / header parametrelerinden form alanları (sıra korunur)."""
    fields = []
    for param in params:
        field = field_from_schema(param.name, param.schema, required=param.required,
                                  location=param.location, schemas=schemas)
        if field is not None:
            if not field.help:
                field.help = param.description
            fields.append(field)
    return fields


def fields_from_body(body: Body | None, schemas: dict | None = None) -> list[Field]:
    """Gövdeden form alanları.

    Gövde bir nesneyse her özelliği formda ayrı bir alan olur. Nesne değilse (ör. bir liste)
    gövdenin tamamı adı boş ("") tek bir alandır.
    """
    if body is None:
        return []
    schema = resolve(body.schema, schemas or {})
    if schema is not None and schema.get("properties"):
        return fields_from_object(schema, schemas or {}, frozenset(), location="body")
    whole = field_from_schema("", body.schema, required=body.required, location="body", schemas=schemas)
    return [whole] if whole is not None else []


def fields_from_object(schema: dict, schemas: dict, seen: frozenset, location: str = "body") -> list[Field]:
    """Bir nesnenin özelliklerinden alanlar; readOnly olanlar atlanır."""
    required = set(schema.get("required", []))
    fields = []
    for name, prop in schema.get("properties", {}).items():
        field = field_from_schema(name, prop, required=name in required, location=location,
                                  schemas=schemas, seen=seen)
        if field is not None:
            fields.append(field)
    return fields


# --- Tek bir alan ---------------------------------------------------------------------

def field_from_schema(name: str, schema: dict, required: bool = False, location: str = "body",
                      schemas: dict | None = None, seen: frozenset = frozenset()) -> Field | None:
    """Tek bir şema → Field. Formda yeri olmayan alan (readOnly) için None döner.

    `seen`: bu alana gelene kadar çözülen $ref'ler; aynı $ref tekrar gelirse yapı kendini içeriyordur.
    """
    schemas = schemas or {}

    # 1. $ref / allOf
    resolved = resolve(schema, schemas, seen)
    if resolved is None:
        return json_field(name, schema, required, location)   # bulunamayan ya da kendini içeren $ref

    # 2. "X ya da null"
    resolved, nullable = unwrap_nullable(resolved)
    resolved = resolve(resolved, schemas, seen)
    if resolved is None:
        return json_field(name, schema, required, location)

    # Bu alanın çözdüğü $ref'ler alt alanlara aktarılır: içeride aynısı gelirse yapı kendini içeriyordur.
    seen = seen | refs_in(schema)

    if resolved.get("readOnly"):
        return None   # sunucunun ürettiği alan (id, created_at...): sadece yanıtta görünür

    # 3. bileşen
    items = resolve(resolved.get("items", {}), schemas, seen) if resolved.get("type") == "array" else {}
    kind = choose_kind(resolved, items)

    # 4. doldurma
    field = Field(
        name=name,
        kind=kind,
        label=resolved.get("title") or name,
        label_en=resolved.get("x-title-en", ""),
        help=resolved.get("description", ""),
        help_en=resolved.get("x-description-en", ""),
        location=location,
        required=required,
        default=resolved.get("default"),
        placeholder=read_example(resolved),
        deprecated=resolved.get("deprecated", False),
        write_only=resolved.get("writeOnly", False),
        nullable=nullable,
    )
    read_constraints(field, resolved)
    read_children(field, resolved, items, schemas, seen, location)
    return field


def choose_kind(schema: dict, items: dict | None = None) -> str:
    """Şemaya bakıp formda hangi bileşenin çizileceğine karar verir. Sıra önemlidir: ilk uyan kazanır.

    `items`: dizi ise elemanların çözülmüş şeması (None = çözülemedi).
    """
    schema_type = schema.get("type")
    fmt = schema.get("format", "")

    if "const" in schema:
        return "const"
    if "enum" in schema:
        return "select" if len(schema["enum"]) <= ENUM_SELECT_LIMIT else "search"
    if schema.get("oneOf") or schema.get("anyOf"):
        return "union"
    if schema_type == "array":
        if items is None:
            return "json"
        if "enum" in items:
            return "multi"
        if items.get("properties"):
            return "objlist"
        return "tags"
    if schema_type == "object" or "properties" in schema:
        if schema.get("properties"):
            return "object"
        if isinstance(schema.get("additionalProperties"), dict):
            return "kv"
        return "json"   # serbest nesne: ne içereceği belli değil
    if schema_type == "boolean":
        return "toggle"
    if schema_type in ("integer", "number"):
        return "number"
    if schema_type == "string" and (fmt == "binary" or "contentMediaType" in schema):
        return "file"
    if schema_type == "string" and fmt in DATE_FORMATS:
        return DATE_FORMATS[fmt]
    if schema_type == "string" and schema.get("maxLength", 0) > TEXTAREA_MIN_LENGTH:
        return "textarea"
    return "text"   # string ve tanımadığımız her şey: düz metin kutusu


def read_constraints(field: Field, schema: dict) -> None:
    """Kısıtları alana taşır (kontrol etmez)."""
    if field.kind == "text":
        field.format = schema.get("format", "")
    if field.kind in ("select", "search"):
        field.options = list(schema["enum"])
    if field.kind == "number":
        field.integer = schema.get("type") == "integer"
        field.minimum = schema.get("minimum")
        field.maximum = schema.get("maximum")
        field.step = schema.get("multipleOf", 1 if field.integer else None)
    if field.kind == "const":
        field.default = schema["const"]
    if field.kind == "file":
        field.accept = schema.get("contentMediaType", "")
    field.max_length = schema.get("maxLength")
    field.pattern = schema.get("pattern", "")
    field.min_items = schema.get("minItems")


def read_children(field: Field, schema: dict, items: dict | None, schemas: dict, seen: frozenset,
                  location: str) -> None:
    """Bileşik alanların içini doldurur: alt alanlar, eleman tipi, seçenekler, türler."""
    if field.kind == "object":
        field.children = fields_from_object(schema, schemas, seen, location)
    elif field.kind == "objlist":
        field.children = fields_from_object(items, schemas, seen | refs_in(schema.get("items", {})), location)
    elif field.kind == "multi":
        field.options = list(items["enum"])
    elif field.kind == "tags":
        field.item = field_from_schema("", schema.get("items", {}), schemas=schemas, seen=seen)
    elif field.kind == "kv":
        field.item = field_from_schema("", schema["additionalProperties"], schemas=schemas, seen=seen)
    elif field.kind == "union":
        options = schema.get("oneOf") or schema.get("anyOf")
        variants = [field_from_schema(field.name, option, schemas=schemas, seen=seen) for option in options]
        field.variants = [v for v in variants if v is not None]


def json_field(name: str, schema: dict, required: bool, location: str) -> Field:
    """Çözülemeyen yapı için yedek: değer JSON olarak yazılır."""
    return Field(name=name, kind="json", label=schema.get("title") or name, location=location, required=required)


# --- Şema çözümü -----------------------------------------------------------------------

def resolve(schema: dict, schemas: dict, seen: frozenset = frozenset()) -> dict | None:
    """$ref'i hedefiyle değiştirir, allOf parçalarını birleştirir. Düz bir şema döner.

    $ref'in yanındaki anahtarlar (title, default...) hedeftekileri ezer: alanın kendi bilgisi önceliklidir.
    Hedef yoksa ya da $ref daha önce görüldüyse (kendini içeren yapı) None döner.
    """
    if "$ref" in schema:
        ref = schema["$ref"]
        target = schemas.get(ref.split("/")[-1])
        if target is None or ref in seen:
            return None
        siblings = {k: v for k, v in schema.items() if k != "$ref"}
        return resolve({**target, **siblings}, schemas, seen | {ref})

    if "allOf" in schema:
        merged = {k: v for k, v in schema.items() if k != "allOf"}
        properties, required = {}, []
        for part in schema["allOf"]:
            part = resolve(part, schemas, seen)
            if part is None:
                return None
            properties.update(part.get("properties", {}))
            required += part.get("required", [])
            for key, value in part.items():
                merged.setdefault(key, value)       # parçanın bilgisi, dıştakini ezmez
        if properties:
            merged["properties"], merged["required"] = properties, required
        return merged

    return schema


def unwrap_nullable(schema: dict) -> tuple[dict, bool]:
    """"X ya da null" → (X, True). Üç yazılış da tanınır:
    OpenAPI 3.1 / Pydantic: anyOf: [X, {type: null}] · JSON Schema: type: [x, "null"] · OpenAPI 3.0: nullable: true
    """
    for key in ("anyOf", "oneOf"):
        options = schema.get(key)
        if options and any(o.get("type") == "null" for o in options):
            rest = [o for o in options if o.get("type") != "null"]
            outer = {k: v for k, v in schema.items() if k != key}
            inner = rest[0] if len(rest) == 1 else {key: rest}
            return {**inner, **outer}, True          # dıştaki title/default, içtekini ezer

    schema_type = schema.get("type")
    if isinstance(schema_type, list) and "null" in schema_type:
        rest = [t for t in schema_type if t != "null"]
        return {**schema, "type": rest[0] if len(rest) == 1 else rest}, True

    return schema, bool(schema.get("nullable"))


def refs_in(schema: dict) -> frozenset:
    """Bir şemanın doğrudan ya da nullable kabuğu içinde işaret ettiği $ref'ler."""
    refs = {schema["$ref"]} if "$ref" in schema else set()
    for option in schema.get("anyOf", []) + schema.get("oneOf", []):
        if "$ref" in option:
            refs.add(option["$ref"])
    return frozenset(refs)


def read_example(schema: dict) -> str:
    # OpenAPI 3.0 "example", 3.1 / JSON Schema "examples" (liste) kullanır.
    if "example" in schema:
        return str(schema["example"])
    examples = schema.get("examples")
    if isinstance(examples, list) and examples:
        return str(examples[0])
    return ""
