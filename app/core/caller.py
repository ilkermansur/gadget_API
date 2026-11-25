"""Hedef API'ye istek gönderir ve sonucu yorumlar.

Adımlar:
  build_request  → görünüm (EndpointView) + form değerleri → Request      (saf, ağsız)
  send           → Request → httpx ile gerçek gönderim → Result         (async: arayüzü kilitlemez)
  send_with_auth → send + resource kimliği (süreli token: al, sakla, yenile)  ← arayüz bunu kullanır
  extract_error / table_rows → yanıtı yorumla: hata özeti, tablo satırları

Form değerleri sözlüğü (values) kuralı:
  - anahtar yok  → o alan gönderilmez
  - değer None   → JSON'da null gönderilir (sadece gövdede; query/header'da boş değer gönderilmez)
  - gizli alan (portal katmanı) her zaman kendi sabit değerini gönderir, formdan geleni değil
Kimlik (Basic, Bearer, API anahtarı, süreli token) isteğe apply_auth ile eklenir; bilgiler resource'tan gelir.
"""
import base64
import json
import time
from dataclasses import replace
from urllib.parse import quote

import httpx

from core.errors import Msg, UserError, render
from core.i18n import DEFAULT_LANG
from core.models import EndpointView, ErrorSummary, FieldError, Request, Resource, Result

# Standart hata formatında kod gelmezse durum kodundan üretilir.
CODE_BY_STATUS = {401: "not_authenticated", 403: "forbidden", 404: "not_found",
                  422: "validation_error", 500: "internal_error"}


# =============================================================================
# İstek kurma
# =============================================================================

def build_request(view: EndpointView, values: dict, base_url: str, json_body: object = None) -> Request:
    """json_body verilirse (geliştiricinin "JSON gövde" modu) gövde olarak olduğu gibi gider; formdaki gövde
    alanları yok sayılır, path/query/header yine formdan gelir."""
    endpoint = view.endpoint
    multipart = endpoint.body is not None and endpoint.body.media_type != "application/json"
    path = endpoint.path
    request = Request(method=endpoint.method, url="")
    body = {}

    for field in view.fields:
        if json_body is not None and field.location == "body":
            continue
        if field.hidden:
            value = field.default
        elif field.name in values:
            value = values[field.name]
        else:
            continue
        if field.kind == "json" and isinstance(value, str):
            value = parse_json_text(field.label, value)

        if field.location == "path":
            if value in (None, ""):
                raise UserError("errors.path_param_empty", name=field.name)
            path = path.replace("{" + field.name + "}", quote(str(value), safe=""))
        elif field.location == "query":
            if value not in (None, ""):
                request.params[field.name] = value
        elif field.location == "header":
            if value not in (None, ""):
                request.headers[field.name] = str(value)
        elif field.name == "":
            request.json = value                         # gövdenin tamamı tek alan
        elif multipart and field.kind == "file":
            request.files[field.name] = value
        elif multipart:
            request.data[field.name] = "" if value is None else str(value)
        else:
            body[field.name] = value

    missing = [f.name for f in view.fields if f.location == "path" and "{" + f.name + "}" in path]
    if missing:
        raise UserError("errors.path_param_missing", names=", ".join(missing))

    if json_body is not None:
        request.json = json_body
    elif body:
        request.json = body
    elif endpoint.body is not None and endpoint.body.required and not multipart and request.json is None:
        # Zorunlu gövde ama form boş: {} gönderilir ki API eksik alanları tek tek bildirsin
        # (hiç gövde gitmezse sadece "gövde yok" der, formda hangi alanın eksik olduğu görünmez).
        request.json = {}
    request.url = base_url.rstrip("/") + path
    return request


def apply_auth(request: Request, auth_type: str, auth: dict) -> Request:
    """Resource'un kimliğini isteğe ekler; yeni bir Request döner (gelen değişmez).

    Kimlik bilgisi formdaki değerlerin ÜSTÜNE yazılır: operatör aynı adlı header/query'yi ezemez.
    """
    request = replace(request, headers=dict(request.headers), params=dict(request.params))
    if auth_type == "basic":
        pair = f"{auth['username']}:{auth['password']}".encode()
        request.headers["Authorization"] = "Basic " + base64.b64encode(pair).decode()
    elif auth_type == "bearer":
        request.headers["Authorization"] = "Bearer " + auth["token"]
    elif auth_type == "apikey":
        if auth.get("key_in", "header") == "query":
            request.params[auth["key_name"]] = auth["key_value"]
        else:
            request.headers[auth["key_name"]] = auth["key_value"]
    return request


def parse_json_text(label: str, text: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise UserError("errors.invalid_json", label=label, reason=exc.msg) from None


# =============================================================================
# Gönderim
# =============================================================================

async def send(request: Request, timeout: float = 30.0) -> Result:
    """İsteği gönderir. Ağ hataları istisna olarak değil, Result.outcome olarak döner."""
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(
                request.method, request.url,
                params=request.params or None,
                headers=request.headers or None,
                json=request.json,
                data=request.data or None,
                files=request.files or None,
            )
    except httpx.TimeoutException:
        return failure("timeout", Msg("errors.timeout", {"seconds": f"{timeout:g}"}), started)
    except httpx.TransportError as exc:
        return failure("connection_error", Msg("errors.connection", {"reason": str(exc)}), started)

    body = parse_body(response)
    ok = response.is_success
    return Result(
        outcome="ok" if ok else "http_error",
        status=response.status_code,
        elapsed_ms=elapsed_ms(started),
        size=len(response.content),
        body=body,
        text=response.text,
        headers=dict(response.headers),
        error=None if ok else extract_error(response.status_code, body, response.text),
    )


# =============================================================================
# Resource kimliğiyle gönderim (süreli token dahil)
# =============================================================================

class TokenCache:
    """Resource başına alınmış süreli token'ları bellekte tutar.

    Uygulama yeniden başlarsa önbellek boşalır, ilk istekte yeniden alınır (token veritabanına yazılmaz).
    `clock` testler içindir: sahte saatle "süre doldu" anı beklemeden denenir.
    """
    EXPIRY_MARGIN = 10.0   # sn: süre dolmadan biraz önce yenile, yolda geçersiz olmasın

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.tokens: dict[int, tuple[str, float | None]] = {}   # resource_id → (token, bitiş zamanı)

    def get(self, resource_id: int) -> str | None:
        entry = self.tokens.get(resource_id)
        if entry is None:
            return None
        token, expires_at = entry
        if expires_at is not None and self.clock() >= expires_at:
            self.drop(resource_id)
            return None
        return token

    def put(self, resource_id: int, token: str, expires_in: float | None) -> None:
        expires_at = self.clock() + max(expires_in - self.EXPIRY_MARGIN, 0) if expires_in else None
        self.tokens[resource_id] = (token, expires_at)

    def drop(self, resource_id: int) -> None:
        self.tokens.pop(resource_id, None)


async def send_with_auth(request: Request, resource: Resource, tokens: TokenCache) -> Result:
    """İsteği resource'un kimliğiyle gönderir. Arayüzün kullandığı tek giriş noktası budur.

    Süreli token'da: önbellekte yoksa ya da süresi dolduysa alınır; sunucu 401 derse token bir kez
    yenilenip istek tekrarlanır (ikinci 401 olduğu gibi döner, sonsuz döngü yok).
    """
    if resource.auth_type != "token":
        return await send(apply_auth(request, resource.auth_type, resource.auth), resource.timeout)

    for attempt in (1, 2):
        token = tokens.get(resource.id)
        if token is None:
            token, error = await fetch_token(resource, tokens)
            if error is not None:
                return error
        result = await send(apply_auth(request, "bearer", {"token": token}), resource.timeout)
        if result.status != 401 or attempt == 2:
            return result
        tokens.drop(resource.id)        # sunucu token'ı reddetti: yenile ve bir kez daha dene
    return result


async def fetch_token(resource: Resource, tokens: TokenCache) -> tuple[str | None, Result | None]:
    """Token adresine form gönderir (OAuth2 password / client_credentials). Döner: (token, hata sonucu)."""
    auth = resource.auth
    url = auth["token_url"]
    if not url.startswith(("http://", "https://")):
        url = resource.base_url.rstrip("/") + "/" + url.lstrip("/")     # göreli adres: kök adrese eklenir
    if auth.get("grant", "password") == "client_credentials":
        form = {"grant_type": "client_credentials", "client_id": auth["client_id"],
                "client_secret": auth["client_secret"]}
    else:
        form = {"grant_type": "password", "username": auth["username"], "password": auth["password"]}
    if auth.get("scope"):
        form["scope"] = auth["scope"]

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=resource.timeout) as client:
            response = await client.post(url, data=form)
    except httpx.TimeoutException:
        return None, token_failure("timeout", None, Msg("errors.token_timeout"), started)
    except httpx.TransportError as exc:
        return None, token_failure("connection_error", None, Msg("errors.token_connection", {"reason": str(exc)}), started)

    body = parse_body(response)
    if not response.is_success or not isinstance(body, dict) or "access_token" not in body:
        reason = extract_error(response.status_code, body, response.text).message if not response.is_success \
            else Msg("errors.token_missing")
        return None, token_failure("http_error", response.status_code,
                                   Msg("errors.token_failed", {"status": response.status_code, "reason": reason}), started)
    tokens.put(resource.id, body["access_token"], body.get("expires_in"))
    return body["access_token"], None


def token_failure(outcome: str, status: int | None, msg: Msg, started: float) -> Result:
    return Result(outcome=outcome, status=status, elapsed_ms=elapsed_ms(started), error=own_error("token_error", msg))


def failure(code: str, msg: Msg, started: float) -> Result:
    return Result(outcome=code, status=None, elapsed_ms=elapsed_ms(started), error=own_error(code, msg))


def own_error(code: str, msg: Msg) -> ErrorSummary:
    """Portalın kendi hatası: Türkçe metin (geçmiş ve log için) + ekranda dile çevrilecek mesaj."""
    return ErrorSummary(code=code, message=render(msg.key, msg.values, DEFAULT_LANG), msg=msg)


def parse_body(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return response.text


def elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


# =============================================================================
# Yanıtı yorumlama
# =============================================================================

def extract_error(status: int, body: object, text: str) -> ErrorSummary:
    """Hata gövdesini özetler. Tanınan üç yazılış:
    1. Rehberdeki standart format: {"error": {code, message, fields, request_id}}
    2. FastAPI varsayılanı 422:    {"detail": [{"loc": [...], "msg": "..."}]}
    3. FastAPI varsayılanı diğer:  {"detail": "Not Found"}
    Hiçbiri değilse ham metin mesaj olur.
    """
    default_code = CODE_BY_STATUS.get(status, f"http_{status}")

    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        error = body["error"]
        return ErrorSummary(
            code=error.get("code") or default_code,
            message=error.get("message", ""),
            fields=[FieldError(f.get("field", ""), f.get("message", "")) for f in error.get("fields", [])],
            request_id=error.get("request_id", ""),
        )

    if isinstance(body, dict) and isinstance(body.get("detail"), list):
        fields = [FieldError(loc_to_path(d.get("loc", [])), d.get("msg", "")) for d in body["detail"]]
        error = own_error(default_code, Msg("errors.validation"))
        error.fields = fields
        return error

    if isinstance(body, dict) and isinstance(body.get("detail"), str):
        return ErrorSummary(code=default_code, message=body["detail"])

    return ErrorSummary(code=default_code, message=text[:500])


def loc_to_path(loc: list) -> str:
    """["body", "items", 1, "name"] → "items[1].name"  (konum öneki atılır)."""
    parts = loc[1:] if loc and loc[0] in ("body", "query", "path", "header") else loc
    path = ""
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else (f".{part}" if path else str(part))
    return path


def table_rows(body: object, list_path: str) -> list | None:
    """Yanıtta tabloya dökülecek diziyi bulur. Dizi yoksa None (o zaman JSON görünümü kullanılır).

    list_path verilmişse ("data.list") o yol izlenir; verilmemişse yanıtın kendisi dizi mi,
    değilse {count, items} biçiminde mi diye bakılır.
    """
    if list_path:
        current = body
        for key in list_path.split("."):
            if not isinstance(current, dict) or key not in current:
                return None
            current = current[key]
        return current if isinstance(current, list) else None
    if isinstance(body, list):
        return body
    if isinstance(body, dict) and isinstance(body.get("items"), list):
        return body["items"]
    return None
