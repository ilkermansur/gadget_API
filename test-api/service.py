"""Test için dummy FastAPI servisi: portalın bağlanacağı "takılabilir" bir API örneği.

Ürün değildir; veri bellekte durur, yanıtların gerçek bir anlamı yoktur. Amaç portalın her davranışını
gerçek isteklerle denemektir. Aynı zamanda docs/fastapi-rehberi.md'nin çalışan örneğidir.

Gruplar (tag'ler) ve neyi denemek için oldukları:
  devices  → liste (tablo), detay, ekle, PATCH ile düzenleme, DELETE onayı
  metrics  → listenin yanıtta kabuk içinde gelmesi (x-gadget.list_path = "data.list")
  demo     → bütün form alan tipleri, dosya yükleme, zaman aşımı, hata kodları
  system   → sağlık
  secure   → kimlik türleri (Basic, Bearer, API anahtarı, süreli token)
  (tag'siz) /legacy/... → BİLEREK kötü tanımlanmış endpoint'ler: portal katmanıyla düzeltmeyi denemek için

Şema sürümü: TEST_API_SCHEMA=v2 ile açılırsa şema değişmiş gibi görünür (schema_v2): portalın senkron
ekranını gerçek bir değişiklikle denemek için. Varsayılan v1.

Hatalar rehberdeki standart formatta döner: {"error": {code, message, fields, request_id}}.
"""
import asyncio
import copy
import math
import os
import time as time_module   # "time" adı aşağıda form alanı tipi olarak kullanılıyor
import uuid
from datetime import date, datetime, time
from ipaddress import IPv4Address
from typing import Literal
from uuid import UUID

from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Path, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import (APIKeyHeader, APIKeyQuery, HTTPAuthorizationCredentials, HTTPBasic,
                              HTTPBasicCredentials, HTTPBearer)
from pydantic import AnyUrl, BaseModel, Field, SecretStr
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI(
    title="Gadget Test API",
    version="0.2.0",
    openapi_tags=[
        {"name": "devices", "description": "Cihazlar", "x-gadget": {"icon": "plug", "name_en": "Devices"}},
        {"name": "metrics", "description": "Metrikler", "x-gadget": {"icon": "activity", "name_en": "Metrics"}},
        {"name": "demo", "description": "Örnekler", "x-gadget": {"icon": "flask", "name_en": "Demo"}},
        {"name": "system", "description": "Sistem", "x-gadget": {"icon": "server", "name_en": "System"}},
        {"name": "secure", "description": "Korumalı", "x-gadget": {"icon": "lock", "name_en": "Secure"}},
    ],
)

SITES = Literal["İstanbul DC", "Ankara DC", "İzmir Lab"]
DEVICE_TYPES = Literal["Gateway", "Sunucu", "Uygulama", "Cihaz"]
STATUSES = Literal["Aktif", "Pasif", "Arızalı"]


# =============================================================================
# Hata formatı
# =============================================================================

ERROR_CODES = {401: "not_authenticated", 403: "forbidden", 404: "not_found", 500: "internal_error"}


def error_response(status: int, code: str, message: str, fields: list | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {
        "code": code,
        "message": message,
        "fields": fields or [],
        "request_id": "req_" + uuid.uuid4().hex[:10],
    }})


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return error_response(exc.status_code, ERROR_CODES.get(exc.status_code, "error"), str(exc.detail))


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    fields = [{"field": field_path(e["loc"]), "message": e["msg"]} for e in exc.errors()]
    return error_response(422, "validation_error", "Gönderilen değerlerde hata var.", fields)


def field_path(loc: tuple) -> str:
    """("body", "items", 1, "name") → "items[1].name"  (konum öneki atılır)."""
    parts = list(loc[1:]) if loc and loc[0] in ("body", "query", "path", "header") else list(loc)
    path = ""
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else (f".{part}" if path else str(part))
    return path


# =============================================================================
# Bellekteki veri
# =============================================================================

DEVICES: dict[str, dict] = {}


def reset_data() -> None:
    """Örnek cihazları yeniden oluşturur (testler her seferinde temiz veriyle başlar)."""
    DEVICES.clear()
    types = ["Gateway", "Sunucu", "Uygulama", "Cihaz"]
    statuses = ["Aktif", "Aktif", "Aktif", "Pasif", "Aktif", "Arızalı"]
    sites = ["İstanbul DC", "Ankara DC", "İzmir Lab"]
    for i in range(23):
        device_id = f"EP-{1040 + i}"
        DEVICES[device_id] = {
            "id": device_id,
            "name": f"{['IST', 'ANK', 'IZM'][i % 3]}-{types[i % 4][:3].upper()}-{1 + i % 12:02d}",
            "type": types[i % 4],
            "site": sites[i % 3],
            "latency_ms": 12 + (i * 7) % 60,
            "status": statuses[i % 6],
            "threshold_min": 18,
            "threshold_max": 27,
        }


reset_data()


# =============================================================================
# devices
# =============================================================================

class DeviceIn(BaseModel):
    name: str = Field(title="Cihaz adı", max_length=64, examples=["IST-GW-07"],
                      json_schema_extra={"x-title-en": "Device name"})
    type: DEVICE_TYPES = Field(title="Tip")
    site: SITES = Field(title="Lokasyon")
    threshold_max: int | None = Field(None, title="Üst limit", ge=1, le=500, description="Aşılırsa alarm üretir")


class DevicePatch(BaseModel):
    name: str | None = Field(None, title="Cihaz adı", max_length=64)
    site: SITES | None = Field(None, title="Lokasyon")
    status: STATUSES | None = Field(None, title="Durum")
    threshold_min: int | None = Field(None, title="Alt limit")
    threshold_max: int | None = Field(None, title="Üst limit")


def find_device(device_id: str) -> dict:
    if device_id not in DEVICES:
        raise HTTPException(404, f"{device_id} bulunamadı.")
    return DEVICES[device_id]


@app.get("/devices", tags=["devices"], summary="Cihazları listele",
         description="Kayıtlı cihazları lokasyon ve duruma göre filtreleyerek döndürür.",
         openapi_extra={"x-gadget": {"summary_en": "List devices", "view": "table",
                                     "row_action": "PATCH /devices/{device_id}"}})
def list_devices(site: SITES | None = Query(None, title="Lokasyon"),
                 status: STATUSES | None = Query(None, title="Durum"),
                 limit: int = Query(50, ge=1, le=500, title="Kayıt sayısı")):
    items = [d for d in DEVICES.values()
             if (site is None or d["site"] == site) and (status is None or d["status"] == status)][:limit]
    return {"count": len(items), "items": items}


@app.get("/devices/{device_id}", tags=["devices"], summary="Cihaz detayı",
         openapi_extra={"x-gadget": {"summary_en": "Device detail"}})
def get_device(device_id: str = Path(title="Cihaz ID", examples=["EP-1042"])):
    return find_device(device_id)


@app.post("/devices", tags=["devices"], summary="Cihaz ekle", status_code=201,
          description="Yeni cihaz kaydı oluşturur. İlk bağlantıya kadar \"Pasif\" görünür.",
          openapi_extra={"x-gadget": {"summary_en": "Add device"}})
def create_device(body: DeviceIn):
    device_id = f"EP-{1040 + len(DEVICES) + 100}"
    DEVICES[device_id] = {"id": device_id, **body.model_dump(), "latency_ms": None, "status": "Pasif",
                          "threshold_min": 18}
    return DEVICES[device_id]


@app.patch("/devices/{device_id}", tags=["devices"], summary="Cihazı güncelle",
           description="Sadece gönderilen alanlar güncellenir; gönderilmeyenlere dokunulmaz.",
           openapi_extra={"x-gadget": {"summary_en": "Update device", "read_from": "GET /devices/{device_id}"}})
def update_device(body: DevicePatch, device_id: str = Path(title="Cihaz ID")):
    device = find_device(device_id)
    device.update(body.model_dump(exclude_unset=True))
    return device


@app.delete("/devices/{device_id}", tags=["devices"], summary="Cihazı sil",
            openapi_extra={"x-gadget": {"summary_en": "Delete device"}})
def delete_device(device_id: str = Path(title="Cihaz ID")):
    find_device(device_id)
    del DEVICES[device_id]
    return {"id": device_id, "deleted": True}


# =============================================================================
# metrics
# =============================================================================

@app.get("/metrics", tags=["metrics"], summary="Metrikleri getir",
         description="Bir cihazın saatlik gecikme metrikleri. Liste yanıtta `data.list` içinde döner.",
         openapi_extra={"x-gadget": {"summary_en": "Get metrics", "list_path": "data.list"}})
def get_metrics(device_id: str = Query(title="Cihaz ID"),
                start: datetime | None = Query(None, title="Başlangıç"),
                end: datetime | None = Query(None, title="Bitiş"),
                interval: Literal["1 dk", "15 dk", "1 saat"] = Query("1 saat", title="Sıklık")):
    rows = [{"time": f"{h:02d}:00", "device_id": device_id, "value": round(24 + math.sin(h / 3) * 9),
             "unit": "ms"} for h in range(24)]
    return {"data": {"list": rows, "interval": interval}}


# =============================================================================
# demo: bütün alan tipleri (docs/tasarim-v1.md §4.2)
# =============================================================================

class Location(BaseModel):
    city: str = Field(title="Şehir")
    room: str | None = Field(None, title="Oda")
    rack: str | None = Field(None, title="Kabin")


class Item(BaseModel):
    name: str = Field(title="Ad")
    count: int = Field(1, ge=1, title="Adet")


TIMEZONES = Literal["Europe/Istanbul", "Europe/London", "Europe/Berlin", "Europe/Paris", "Europe/Moscow",
                    "Asia/Dubai", "Asia/Tokyo", "Asia/Singapore", "Asia/Kolkata", "America/New_York",
                    "America/Chicago", "America/Los_Angeles", "America/Sao_Paulo", "Australia/Sydney", "UTC"]


class AllTypes(BaseModel):
    id: str = Field("DF-001", json_schema_extra={"readOnly": True})                       # readOnly: formda yok
    name: str = Field(title="Ad", max_length=64, pattern="^[A-Z]{3}-", examples=["IST-GW-01"])
    email: str | None = Field(None, title="E-posta", json_schema_extra={"format": "email"})
    url: AnyUrl | None = Field(None, title="Web adresi")
    uuid: UUID | None = Field(None, title="Kayıt UUID")
    ip: IPv4Address | None = Field(None, title="IP adresi")
    secret: SecretStr | None = Field(None, title="Gizli anahtar")                          # password + writeOnly
    note: str = Field("", title="Açıklama", max_length=500)                               # uzun metin
    limit: int = Field(50, title="Gecikme limiti", ge=1, le=500, multiple_of=5)
    enabled: bool = Field(True, title="İzlemeye al")
    level: Literal["Düşük", "Orta", "Yüksek"] = Field("Orta", title="Seviye")              # küçük enum
    tz: TIMEZONES = Field("Europe/Istanbul", title="Saat dilimi")                          # büyük enum
    day: date | None = Field(None, title="Tarih")
    at: time | None = Field(None, title="Saat")
    starts_at: datetime | None = Field(None, title="Başlangıç")
    days: set[Literal["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]] = Field(default_factory=set,
                                                                                title="Günler")
    tags: list[str] = Field(default_factory=list, title="Etiketler")
    location: Location = Field(title="Konum bilgisi")
    items: list[Item] = Field(min_length=1, title="Bağlı kalemler")
    labels: dict[str, str] = Field(default_factory=dict, title="Ek özellikler")
    target: int | str | None = Field(None, title="Hedef")
    parent: str | None = Field(None, title="Üst kayıt")                                   # nullable
    schema_version: Literal["v1"] = Field("v1", title="Şema sürümü")                      # const
    legacy_code: str | None = Field(None, title="Eski kod", deprecated=True)


@app.post("/demo/all-types", tags=["demo"], summary="Alan tipleri (örnek)",
          description="Her OpenAPI alan tipinin formdaki karşılığını gösterir. Aldığı gövdeyi geri döndürür.",
          openapi_extra={"x-gadget": {"summary_en": "Field types (sample)", "confirm": True}})
def all_types(body: AllTypes):
    return {"id": "DF-001", "accepted": True, "received": body.model_dump(mode="json", exclude_unset=True)}


@app.post("/demo/upload", tags=["demo"], summary="Dosya yükle",
          openapi_extra={"x-gadget": {"summary_en": "Upload file"}})
async def upload(config: UploadFile = File(title="Yapılandırma dosyası"), note: str = Form("", title="Not")):
    return {"filename": config.filename, "size": len(await config.read()), "note": note}


@app.get("/demo/slow", tags=["demo"], summary="Yavaş yanıt",
         description="Verilen süre kadar bekler; zaman aşımını denemek için.",
         openapi_extra={"x-gadget": {"summary_en": "Slow response"}})
async def slow(seconds: float = Query(1, ge=0, le=120, title="Bekleme (sn)")):
    await asyncio.sleep(seconds)
    return {"waited": seconds}


@app.get("/demo/error/{code}", tags=["demo"], summary="Hata üret",
         description="Verilen HTTP hata kodunu standart hata formatında döndürür.",
         openapi_extra={"x-gadget": {"summary_en": "Raise error"}})
def raise_error(code: int = Path(title="Hata kodu", ge=400, le=599, examples=[404])):
    raise HTTPException(code, f"Örnek {code} hatası.")


# =============================================================================
# secure: kimlik türlerini denemek için korumalı endpoint'ler
# Bilgiler sabittir (sadece test içindir): Basic svc / svc-pass, Bearer test-token,
# API anahtarı k-123 (header X-API-Key ya da query api_key).
# FastAPI güvenlik sınıfları kullanıldığı için şemada securitySchemes görünür.
# =============================================================================

TEST_BASIC = ("svc", "svc-pass")
TEST_BEARER = "test-token"
TEST_API_KEY = "k-123"

basic_scheme = HTTPBasic(auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)
key_header_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)
key_query_scheme = APIKeyQuery(name="api_key", auto_error=False)


def unauthorized() -> HTTPException:
    return HTTPException(401, "Kimlik doğrulanamadı.")


@app.get("/secure/basic", tags=["secure"], summary="Basic korumalı",
         openapi_extra={"x-gadget": {"summary_en": "Basic protected"}})
def secure_basic(credentials: HTTPBasicCredentials | None = Depends(basic_scheme)):
    if credentials is None or (credentials.username, credentials.password) != TEST_BASIC:
        raise unauthorized()
    return {"ok": True, "user": credentials.username}


@app.get("/secure/bearer", tags=["secure"], summary="Bearer korumalı",
         openapi_extra={"x-gadget": {"summary_en": "Bearer protected"}})
def secure_bearer(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)):
    if credentials is None or credentials.credentials != TEST_BEARER:
        raise unauthorized()
    return {"ok": True}


@app.get("/secure/apikey", tags=["secure"], summary="API anahtarı (başlıkta) korumalı",
         openapi_extra={"x-gadget": {"summary_en": "API key (header) protected"}})
def secure_apikey(key: str | None = Depends(key_header_scheme)):
    if key != TEST_API_KEY:
        raise unauthorized()
    return {"ok": True}


@app.get("/secure/apikey-query", tags=["secure"], summary="API anahtarı (sorguda) korumalı",
         openapi_extra={"x-gadget": {"summary_en": "API key (query) protected"}})
def secure_apikey_query(key: str | None = Depends(key_query_scheme)):
    if key != TEST_API_KEY:
        raise unauthorized()
    return {"ok": True}


# --- süreli token (OAuth2 benzeri) ---------------------------------------------------------
# POST /auth/token: form ile grant_type=password (username/password) ya da
# grant_type=client_credentials (client_id/client_secret) → {access_token, token_type, expires_in}
# Token TOKEN_TTL saniye geçerlidir. Testler TOKEN_TTL'i kısaltabilir, ISSUED'ı sayabilir, TOKENS'ı silebilir.

TEST_CLIENT = ("portal", "portal-secret")
TOKEN_TTL = 60.0
TOKENS: dict[str, float] = {}       # token → bitiş zamanı (time.monotonic)
ISSUED = {"count": 0}


@app.post("/auth/token", tags=["secure"], summary="Belirteç al",
          openapi_extra={"x-gadget": {"summary_en": "Get token"}})
def issue_token(grant_type: str = Form("password"), username: str = Form(""), password: str = Form(""),
                client_id: str = Form(""), client_secret: str = Form("")):
    if grant_type == "password":
        valid = (username, password) == TEST_BASIC
    elif grant_type == "client_credentials":
        valid = (client_id, client_secret) == TEST_CLIENT
    else:
        valid = False
    if not valid:
        raise unauthorized()
    token = "tok_" + uuid.uuid4().hex[:16]
    TOKENS[token] = time_module.monotonic() + TOKEN_TTL
    ISSUED["count"] += 1
    return {"access_token": token, "token_type": "bearer", "expires_in": TOKEN_TTL}


@app.get("/secure/token", tags=["secure"], summary="Süreli belirteç korumalı",
         openapi_extra={"x-gadget": {"summary_en": "Expiring token protected"}})
def secure_token(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)):
    expires = TOKENS.get(credentials.credentials) if credentials else None
    if expires is None or expires < time_module.monotonic():
        raise unauthorized()
    return {"ok": True}


# =============================================================================
# system
# =============================================================================

@app.get("/health", tags=["system"], summary="Sistem sağlığı",
         description="Servisin ayakta olduğunu döndürür.",
         openapi_extra={"x-gadget": {"summary_en": "System health"}})
def health():
    return {"status": "ok"}


# =============================================================================
# legacy: BİLEREK kötü tanımlanmış (tag yok, açıklama yok, tip yok)
# Fonksiyonlara docstring yazılmaz: FastAPI onu açıklama olarak şemaya koyar.
# =============================================================================

@app.get("/legacy/search")
def legacy_search(request: Request):
    q = request.query_params.get("q", "")
    return [d for d in DEVICES.values() if q.lower() in d["name"].lower()]


@app.post("/legacy/save")
def legacy_save(payload: dict = Body(...)):
    return {"saved": True, "keys": sorted(payload)}


@app.get("/legacy/list")
def legacy_list():
    return [{"thr_max_val": d["threshold_max"], "dev_nm": d["name"], "st": d["status"]} for d in DEVICES.values()]


# =============================================================================
# Şema sürümü v2: senkron denemesi için "FastAPI güncellendi" taklidi
# =============================================================================

def schema_v2(v1: dict) -> dict:
    """v1 şemasının değiştirilmiş kopyası (SPEC §8 madde 10 senaryosu):
      + yeni endpoint GET /alarms  - POST /legacy/save kalktı
      ~ GET /devices açıklaması ve limit alanının başlığı, GET /health özeti değişti
    Sadece şema değişir; /alarms gerçekten servis edilmez (senkron şemaya bakar).
    """
    v2 = copy.deepcopy(v1)
    v2["paths"]["/alarms"] = {"get": {"tags": ["alarms"], "summary": "Alarmları listele", "responses": {}}}
    del v2["paths"]["/legacy/save"]
    v2["paths"]["/devices"]["get"]["description"] = "Yeni açıklama"
    limit = next(p for p in v2["paths"]["/devices"]["get"]["parameters"] if p["name"] == "limit")
    limit["schema"]["title"] = "Kayıt adedi"
    v2["paths"]["/health"]["get"]["summary"] = "Sağlık durumu"
    return v2


if os.environ.get("TEST_API_SCHEMA") == "v2":
    app.openapi_schema = schema_v2(app.openapi())     # FastAPI şemayı bu alanda önbellekler
