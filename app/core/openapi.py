"""openapi.json sözlüğünü okuyup ApiDoc'a (gruplar + endpoint'ler) çevirir.

Ağa çıkmaz: şemayı çekmek başka yerin işi, bu dosya sadece okur.
`x-gadget` anahtarlarının portal davranışına eşlemesi TEK yerde, aşağıdaki `read_x_gadget` içindedir:
"bu anahtar neyi değiştirir?" sorusunun cevabı orada.
"""
from core.models import ApiDoc, Body, Endpoint, Group, Param

HTTP_METHODS = ["get", "post", "put", "patch", "delete"]

# Tag'i olmayan endpoint'lerin toplandığı grup; ekrandaki adı locales'ten gelir.
UNTAGGED = "_untagged"

# Gövde birden çok türde tanımlıysa tercih sırası.
BODY_MEDIA_TYPES = ["application/json", "multipart/form-data", "application/x-www-form-urlencoded"]


def parse_openapi(doc: dict) -> ApiDoc:
    info = doc.get("info", {})
    endpoints = []
    for path, path_item in doc.get("paths", {}).items():
        path_params = path_item.get("parameters", [])
        for method in HTTP_METHODS:
            if method in path_item:
                endpoints.append(read_operation(method, path, path_item[method], path_params))

    return ApiDoc(
        title=info.get("title", ""),
        version=info.get("version", ""),
        groups=read_groups(doc.get("tags", []), endpoints),
        endpoints=endpoints,
        schemas=doc.get("components", {}).get("schemas", {}),
        security_schemes=doc.get("components", {}).get("securitySchemes", {}),
    )


def read_groups(tags: list[dict], endpoints: list[Endpoint]) -> list[Group]:
    # Sıra: önce openapi_tags'te tanımlananlar, sonra tanımsız ama kullanılan tag'ler, en sonda tag'siz grup.
    groups = []
    for tag in tags:
        x = tag.get("x-gadget", {})
        groups.append(Group(
            name=tag["name"],
            description=tag.get("description", ""),
            name_en=x.get("name_en", ""),
            icon=x.get("icon", ""),
        ))

    known = {g.name for g in groups}
    for ep in endpoints:
        if ep.group not in known and ep.group != UNTAGGED:
            groups.append(Group(name=ep.group))
            known.add(ep.group)

    if any(ep.group == UNTAGGED for ep in endpoints):
        groups.append(Group(name=UNTAGGED))
    return groups


def read_operation(method: str, path: str, op: dict, path_params: list[dict]) -> Endpoint:
    tags = op.get("tags", [])
    endpoint = Endpoint(
        method=method.upper(),
        path=path,
        group=tags[0] if tags else UNTAGGED,
        summary=op.get("summary", ""),
        description=op.get("description", ""),
        operation_id=op.get("operationId", ""),
        params=read_params(path_params, op.get("parameters", [])),
        body=read_body(op.get("requestBody")),
    )
    read_x_gadget(endpoint, op.get("x-gadget", {}))
    return endpoint


def read_params(path_params: list[dict], op_params: list[dict]) -> list[Param]:
    # Path düzeyindeki parametreler önce gelir; işlem aynı (ad, konum) parametreyi tanımlarsa onunki geçerli.
    merged = {}
    for raw in path_params + op_params:
        merged[(raw["name"], raw["in"])] = raw

    params = []
    for raw in merged.values():
        params.append(Param(
            name=raw["name"],
            location=raw["in"],
            required=raw["in"] == "path" or raw.get("required", False),   # path parametresi her zaman zorunlu
            schema=raw.get("schema", {}),
            description=raw.get("description", ""),
        ))
    return params


def read_body(request_body: dict | None) -> Body | None:
    if not request_body:
        return None
    content = request_body.get("content", {})
    media_type = next((m for m in BODY_MEDIA_TYPES if m in content), None)
    if media_type is None and content:
        media_type = next(iter(content))   # tanımadığımız bir tür: olduğu gibi taşı
    if media_type is None:
        return None
    return Body(
        media_type=media_type,
        required=request_body.get("required", False),
        schema=content[media_type].get("schema", {}),
    )


def read_x_gadget(endpoint: Endpoint, x: dict) -> None:
    """x-gadget anahtarı → Endpoint alanı. Rehber: docs/fastapi-rehberi.md §5."""
    endpoint.summary_en = x.get("summary_en", "")
    endpoint.description_en = x.get("description_en", "")
    endpoint.view = x.get("view", "auto")
    endpoint.read_from = x.get("read_from", "")
    endpoint.row_action = x.get("row_action", "")
    endpoint.list_path = x.get("list_path", "")
    endpoint.dry_run = x.get("dry_run", False)
    # DELETE her zaman onaylıdır (tasarim-v1 §7); diğerleri x-gadget.confirm ile.
    endpoint.confirm = endpoint.method == "DELETE" or x.get("confirm", False)
