"""Uygulamanın veri tipleri: hepsi burada, hepsi düz dataclass.

Bir yapının neye benzediğini merak eden buraya bakar. Mantık yoktur; sadece "ne taşınıyor" yazar.
Görevler ilerledikçe (Field, Overlay, Result, User, Resource...) bu dosya büyür.
"""
from dataclasses import dataclass, field


@dataclass
class User:
    """Portal kullanıcısı. Şifre hash'i bu nesnede taşınmaz (sadece veritabanında durur)."""
    id: int
    username: str
    name: str
    role: str                   # "operator" | "developer" | "admin"  (Operatör ⊂ Geliştirici ⊂ Admin)
    email: str = ""
    active: bool = True
    last_login: str = ""        # ISO zaman, UTC
    groups: list[tuple[int, str]] = field(default_factory=list)   # erişebildiği (resource_id, grup) çiftleri


@dataclass
class Resource:
    """Takılan bir API (genelde bir FastAPI). Kimlik bilgileri veritabanında şifreli durur.

    auth içeriği türe göre:
      none   → {}
      basic  → {"username": ..., "password": ...}
      bearer → {"token": ...}
      apikey → {"key_name": "X-API-Key", "key_value": ..., "key_in": "header" | "query"}
      token  → süreli token (Görev 11)
    """
    id: int
    name: str
    kind: str                   # "fastapi" | "custom" (elle tanımlı endpoint'lerin sabit resource'u)
    openapi_url: str = ""
    base_url: str = ""          # isteklerin gittiği kök adres
    auth_type: str = "none"
    auth: dict = field(default_factory=dict)
    timeout: float = 30.0       # saniye
    last_fetched: str = ""      # şemanın son çekildiği zaman (ISO, UTC)


@dataclass
class Group:
    """Menüdeki bir grup = OpenAPI tag'i."""
    name: str                   # tag adı, ör. "records"
    description: str = ""       # tag açıklaması (TR), menüde görünen ad olarak kullanılabilir
    name_en: str = ""           # x-gadget.name_en
    icon: str = ""              # x-gadget.icon


@dataclass
class Param:
    """Path / query / header parametresi. Şema ham taşınır; forma çevirmek schema_form.py'nin işi."""
    name: str
    location: str               # "path" | "query" | "header" | "cookie"
    required: bool
    schema: dict
    description: str = ""


@dataclass
class Body:
    """İstek gövdesi. Şema ham taşınır ($ref çözümü schema_form.py'de)."""
    media_type: str             # "application/json", "multipart/form-data", ...
    required: bool
    schema: dict


@dataclass
class Field:
    """Formdaki bir alan: JSON Schema'dan üretilir (core/schema_form.py), ekrana ui/fields.py çizer.

    `kind` formda hangi bileşenin çizileceğini söyler; tablo: docs/tasarim-v1.md §4.2.
    """
    name: str                   # API'ye giden anahtar (kilitli)
    kind: str                   # temel:   "text" | "textarea" | "number" | "toggle" | "select" | "search"
                                #          | "date" | "time" | "datetime" | "file" | "const"
                                # bileşik: "object" | "multi" | "tags" | "objlist" | "kv" | "union"
                                # yedek:   "json" (tanınmayan yapı: kullanıcı JSON olarak yazar)
    label: str = ""             # formdaki etiket (TR) — şemadaki title, yoksa name
    label_en: str = ""
    help: str = ""              # alanın altındaki yardım (TR) — şemadaki description
    help_en: str = ""
    location: str = "body"      # "path" | "query" | "header" | "body"
    required: bool = False
    default: object = None
    placeholder: str = ""       # şemadaki example
    format: str = ""            # string için: "email" | "uri" | "uuid" | "ipv4" | "ipv6" | "password"
    options: list = field(default_factory=list)   # select / search seçenekleri (enum)
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None   # multipleOf; tam sayıda yoksa 1
    integer: bool = False       # sayı tam sayı mı
    max_length: int | None = None
    pattern: str = ""
    deprecated: bool = False
    write_only: bool = False    # yanıtta gösterilmez
    nullable: bool = False      # "boş gönder (null)" seçeneği
    accept: str = ""            # file: kabul edilen içerik türü (contentMediaType)
    hidden: bool = False        # portal katmanı: formda gösterme, `default` sabit değer olarak gider
    overridden: set = field(default_factory=set)   # portal katmanında değiştirilen anahtarlar ("portalda düzenlendi")

    # --- bileşik tipler ---
    children: list["Field"] = field(default_factory=list)   # object: alt alanlar · objlist: tablo sütunları
    item: "Field | None" = None                             # tags: eleman tipi · kv: değer tipi
    variants: list["Field"] = field(default_factory=list)   # union: seçilebilecek türler
    min_items: int | None = None                            # objlist / tags / multi

    def label_for(self, lang: str) -> str:
        """Ekran diline göre etiket: EN'de x-title-en varsa o, yoksa TR etiket."""
        return self.label_en if lang == "en" and self.label_en else self.label

    def range_text(self) -> str:
        """Sayı alanının izin verilen aralığı, ipucu olarak: "1 – 4094", "≥ 0", "≤ 10"; sınır yoksa boş."""
        low, high = (number_text(n) for n in (self.minimum, self.maximum))
        if low and high:
            return f"{low} – {high}"
        return f"≥ {low}" if low else f"≤ {high}" if high else ""

    def help_for(self, lang: str) -> str:
        """Ekran diline göre yardım metni: EN'de x-description-en varsa o, yoksa TR metin."""
        return self.help_en if lang == "en" and self.help_en else self.help


@dataclass
class Endpoint:
    """OpenAPI'deki bir işlem (metot + path), x-gadget ipuçlarıyla birlikte."""
    method: str                 # "GET", "POST", ...
    path: str                   # "/records/{record_id}"
    group: str                  # Group.name
    summary: str = ""           # menüdeki ad (TR)
    description: str = ""       # Docs metni (TR, markdown)
    operation_id: str = ""
    params: list[Param] = field(default_factory=list)
    body: Body | None = None

    # --- x-gadget ipuçları (eşlemesi core/openapi.py'de) ---
    summary_en: str = ""
    description_en: str = ""
    view: str = "auto"          # "auto" | "table" | "json" | "raw"
    confirm: bool = False       # çalıştırmadan önce onay
    read_from: str = ""         # PATCH/PUT için mevcut değerin okunduğu GET, ör. "GET /records/{record_id}"
    row_action: str = ""        # liste tablosunda satır "Düzenle" → hangi endpoint
    list_path: str = ""         # tablonun kurulacağı dizinin yanıttaki yolu, ör. "data.items"
    dry_run: bool = False       # ?dry_run=true destekliyor mu

    overridden: set = field(default_factory=set)   # portal katmanında değiştirilen anahtarlar

    @property
    def key(self) -> str:
        """Şemadaki kimlik: senkronda eşleştirme bununla yapılır (portal kimliği ayrıdır)."""
        return f"{self.method} {self.path}"


@dataclass
class Overlay:
    """Portal katmanı: geliştirici/admin'in bir endpoint'e yaptığı düzeltmeler. SADECE farklar tutulur.

    endpoint: {"summary": "...", "confirm": True, ...}
    fields:   {"alan_yolu": {"label": "...", "kind": "select", "options": [...]}, ...}
              alan yolu iç içe alanlar için noktalı: "location.city"; gövdenin tamamı: ""
    Hangi anahtarların düzeltilebildiği core/overlay.py'de yazılıdır (tasarim-v1 §13).
    """
    endpoint: dict = field(default_factory=dict)
    fields: dict = field(default_factory=dict)


@dataclass
class EndpointView:
    """Ekranda görünen endpoint: OpenAPI + x-gadget + portal katmanı birleşmiş hali, form alanlarıyla."""
    endpoint: Endpoint
    fields: list[Field]


@dataclass
class Request:
    """Gönderilmeye hazır istek (core/caller.py build_request üretir). Geçmişe de bu yazılır."""
    method: str
    url: str                                    # path parametreleri doldurulmuş tam adres
    params: dict = field(default_factory=dict)  # query
    headers: dict = field(default_factory=dict)
    json: object = None                         # JSON gövde (None = gövde yok)
    data: dict = field(default_factory=dict)    # multipart / form alanları
    files: dict = field(default_factory=dict)   # multipart dosyaları: ad → (dosya adı, içerik)


@dataclass
class FieldError:
    field: str                  # alan yolu: "limit", "items[1].name"
    message: str


@dataclass
class ErrorSummary:
    """Hata yanıtının özeti: standart format, FastAPI varsayılanı ya da ham metinden çıkarılır."""
    code: str                   # "validation_error", "not_found", "timeout", ...
    message: str
    fields: list[FieldError] = field(default_factory=list)
    request_id: str = ""
    msg: object = None          # portalın kendi mesajıysa çevrilebilir hali (core.errors.Msg); API'den geleni None


@dataclass
class Result:
    """Bir isteğin sonucu."""
    outcome: str                # "ok" (2xx) | "http_error" | "timeout" | "connection_error"
    status: int | None          # HTTP durum kodu; yanıt hiç gelmediyse None
    elapsed_ms: int
    size: int = 0               # yanıt gövdesi, bayt
    body: object = None         # JSON ise ayrıştırılmış hali, değilse metin
    text: str = ""              # ham gövde
    headers: dict = field(default_factory=dict)
    error: ErrorSummary | None = None
    history_id: int | None = None   # geçmişe yazıldıysa kaydın numarası (sayfa yenilenince sonucu geri getirmek için)


@dataclass
class CatalogEntry:
    """Portalın tanıdığı bir endpoint (core/catalog.py). `id` portalın verdiği kalıcı kimliktir:
    atamalar, portal katmanı ve geçmiş buna bağlıdır; endpoint'in adı değişse de aynı kalır."""
    id: int
    resource_id: int
    method: str
    path: str
    selected: bool = False      # menüde mi
    decided: bool = False       # admin seçim kararını verdi mi (hariç tutulan tekrar sorulmaz)
    removed: bool = False       # şemada artık yok (senkron)
    custom: bool = False        # elle tanımlı (Custom resource)

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"


@dataclass
class MenuItem:
    """Kullanıcının menüsündeki bir endpoint: kalıcı kimlik + ekranda görünen hali."""
    endpoint_id: int
    resource_id: int
    view: EndpointView


@dataclass
class Conflict:
    """Senkronda: portalda düzeltilmiş bir değeri FastAPI de değiştirmiş. Hangisinin geçerli olacağı sorulur."""
    endpoint_id: int
    endpoint_key: str           # "GET /devices"
    key: str                    # düzeltilen anahtar: "summary", "label"...
    field_path: str | None      # alan düzeyindeyse alan yolu ("limit"), endpoint düzeyindeyse None
    fastapi_old: object         # FastAPI'nin önceki değeri
    fastapi_new: object         # FastAPI'nin yeni değeri
    portal_value: object        # portal katmanındaki değer

    @property
    def id(self) -> str:
        """Kararın anahtarı (ekranda ve apply_sync'te)."""
        return f"{self.endpoint_id}:{self.field_path or ''}:{self.key}"


@dataclass
class SyncChange:
    """Seçili bir endpoint'in FastAPI'de değişen yönleri (insan okuyacak)."""
    endpoint_id: int
    endpoint_key: str
    changes: list[str]          # ör. ["description", "field_added:note", "field_changed:limit"] (ekran çevirir)


@dataclass
class SyncPlan:
    """Senkronun önizlemesi: admin görür, kararlarını verir, sonra apply_sync uygular."""
    new: list[Endpoint]                 # hiç görülmemiş endpoint'ler → Ekle / Ekleme kararı şart
    changed: list[SyncChange]           # seçili ve değişmiş → güncellenecek
    removed: list[CatalogEntry]         # FastAPI'de artık yok → menüden kalkacak
    returned: list[CatalogEntry]        # daha önce kalkmıştı, geri geldi
    conflicts: list[Conflict]           # portal düzeltmesi ↔ FastAPI değişikliği → karar şart
    kept_selected: int = 0              # "Seçimin korunuyor: N seçili, M hariç"
    kept_excluded: int = 0

    @property
    def nothing_to_do(self) -> bool:
        return not (self.new or self.changed or self.removed or self.returned or self.conflicts)


@dataclass
class HistoryItem:
    """Bir çalıştırmanın kaydı (core/history.py). Silinmez; kişinin ve endpoint'in adı kayıtta saklanır,
    kişi ya da endpoint sonradan silinse/yeniden adlandırılsa da kayıt okunur kalır."""
    id: int
    at: str                     # ISO zaman, UTC
    user_id: int
    username: str
    user_name: str
    user_deleted: bool          # kişi artık yok → ekranda "(silindi)"
    resource_id: int
    endpoint_id: int
    endpoint_key: str           # "GET /devices"
    endpoint_name: str          # o anki görünen ad
    values: dict                # formdan gönderilen değerler (gizliler maskeli) — "Tekrar çalıştır" için
    request: dict               # {method, url, params, json, data} — gizliler maskeli, kimlik başlıkları hiç yok
    outcome: str
    status: int | None
    elapsed_ms: int
    size: int
    response_text: str          # en fazla MAX_BODY bayt
    truncated: bool             # yanıt kesildi mi
    error_code: str = ""


@dataclass
class Branding:
    name: str                   # ürün adı: giriş ekranı, menü başlığı, CSV dosya adı
    palette: str                # 9 hazır paletten birinin kimliği (renkler ui/theme.py'de)
    logo_png: bytes | None = None


@dataclass
class ApiDoc:
    """Bir openapi.json'ın okunmuş hali."""
    title: str
    version: str
    groups: list[Group]
    endpoints: list[Endpoint]
    schemas: dict = field(default_factory=dict)   # components.schemas: $ref'lerin çözüldüğü yer
    security_schemes: dict = field(default_factory=dict)   # components.securitySchemes: kimlik türü önerisi için


@dataclass
class CustomParam:
    """Custom endpoint'in bir parametresi (tanım ekranının "Parametreler" bölümü)."""
    name: str                   # API'ye giden anahtar
    location: str = "query"     # "path" | "query" | "header" | "body"
    kind: str = "text"          # core/custom.py PARAM_KINDS
    label: str = ""
    required: bool = False
    default: object = None
    options: list = field(default_factory=list)   # kind = "select" için
    help: str = ""


@dataclass
class CustomSpec:
    """Elle tanımlanan endpoint (tasarim-v1 §8, 6 bölüm). core/custom.py bunu bir OpenAPI işlemine çevirir."""
    method: str                 # 1 Genel
    path: str
    base_url: str               # tam adres = base_url + path
    summary: str
    group: str = "custom"
    description: str = ""       # 4 Docs metni (markdown)
    params: list[CustomParam] = field(default_factory=list)   # 2 Parametreler
    view: str = "auto"          # 3 Yanıt: ilk görünüm
    list_path: str = ""         #          liste yolu
    confirm: bool = False       # 5 Erişim: onay iste
    read_from: str = ""         # 6 Düzenleme bağlantısı
    row_action: str = ""
    auth_from: int | None = None   # kimliği ödünç alınan resource (yoksa kimliksiz)


def number_text(n: float | None) -> str:
    """4094.0 → "4094", 2.5 → "2.5", None → ""."""
    if n is None:
        return ""
    return str(int(n)) if float(n).is_integer() else str(n)
