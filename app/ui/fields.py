"""Field → NiceGUI bileşeni. Formdaki alan tiplerinin ekrandaki karşılığı TEK burada (tasarim-v1 §4.2).

İki parça:
  değer kuralları (saf, ekransız test edilir):
      read_value   → düz bir bileşenin ham değeri → API'ye gidecek değer
      object_value / list_value / kv_value → bileşik alanların değeri, parçalarından
      NOT_SENT     → "bu alanı gönderme" (None'dan farklı: None = null gönder)
  FieldInput → alanı çizer (tipine göre build_* metodu), değerini okur (read), altında hata gösterir

Bileşik alanlar (nesne, nesne listesi, anahtar=değer, oneOf) içlerinde yine FieldInput kullanır: aynı kurallar
her seviyede geçerlidir. Doğrulama yapılmaz (FastAPI yapar); sadece hatalı JSON metni burada yakalanır.
"""
import json
from dataclasses import replace

from nicegui import events, ui

from core.errors import UserError
from core.i18n import t
from core.models import Field

TEXT_KINDS = {"text", "textarea", "date", "time", "datetime"}
HTML_INPUT_TYPES = {"date": "date", "time": "time", "datetime": "datetime-local"}
FORMAT_INPUT_TYPES = {"email": "email", "uri": "url", "password": "password"}
MONO_FORMATS = {"uuid", "ipv4", "ipv6", "uri"}       # kimlik/adres: eş genişlikli yazı
WIDE_KINDS = {"object", "objlist", "kv", "textarea", "json", "tags", "multi", "union"}   # formda tam satır


# =============================================================================
# Değer kuralları (saf)
# =============================================================================

class NotSent:
    """read_value'nun "bu alanı gönderme" cevabı (None'dan farklı: None = null gönder)."""


NOT_SENT = NotSent()


def read_value(field: Field, raw: object) -> object:
    """Düz bir bileşenden okunan ham değer → API'ye gidecek değer. Gönderilmeyecekse NOT_SENT.

    Boş bırakılan alan gönderilmez (FastAPI varsayılanı uygular). Hatalı JSON ValueError fırlatır.
    """
    if field.kind == "const":
        return field.default
    if field.kind == "toggle":
        return bool(raw)
    if raw is None or raw == "":
        return NOT_SENT
    if field.kind == "number":
        number = float(raw)
        return int(number) if field.integer and number.is_integer() else number
    if field.kind in TEXT_KINDS or field.kind in ("select", "search"):
        return raw
    try:                                  # json (ve metin olarak verilen bileşik değer)
        return json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise UserError("errors.value_not_json", reason=exc.msg) from None


def object_value(children: dict) -> object:
    """Nesne: gönderilen alt alanlar. Hiçbiri gönderilmediyse nesnenin kendisi de gönderilmez."""
    values = {name: value for name, value in children.items() if value is not NOT_SENT}
    return values if values else NOT_SENT


def list_value(items: list) -> object:
    """Liste: boş satırlar atılır. Hiç dolu satır yoksa liste gönderilmez."""
    values = [item for item in items if item is not NOT_SENT]
    return values if values else NOT_SENT


def kv_value(pairs: list[tuple[str, object]]) -> object:
    """Anahtar = değer: anahtarı boş satırlar atılır."""
    values = {key.strip(): value for key, value in pairs if key and key.strip() and value is not NOT_SENT}
    return values if values else NOT_SENT


def needs_null_box(field: Field) -> bool:
    """"Boş gönder (null)" kutusu sadece fark yarattığı yerde: alan nullable VE (zorunlu ya da varsayılanı null değil).

    Varsayılanı zaten null olan opsiyonel alanda boş bırakmak ile null göndermek aynı sonucu verir; kutu gürültü olur.
    """
    return field.nullable and (field.required or field.default is not None)


def initial_text(field: Field) -> str:
    """JSON kutusunda başlangıç metni: varsayılan değer varsa onun JSON hali."""
    return "" if field.default is None else json.dumps(field.default, ensure_ascii=False)


# =============================================================================
# Ekrandaki alan
# =============================================================================

class FieldInput:
    """Formdaki tek bir alan: etiket + bileşen + (null kutusu) + yardım + hata satırı."""

    def __init__(self, field: Field, lang: str = "tr", compact: bool = False):
        self.field, self.lang, self.compact = field, lang, compact
        self.null_box = None
        with ui.column().classes("w-full gap-1"):
            self.build()
            if needs_null_box(field):
                self.null_box = ui.checkbox(t("form.send_null", lang)).classes("text-xs")
            if field.help_for(lang) and not compact:
                ui.label(field.help_for(lang)).classes("text-xs gx-muted")
            self.error_label = ui.label("").classes("text-xs").style("color:#B91C1C")
            self.error_label.set_visibility(False)

    @property
    def label(self) -> str:
        return self.field.label_for(self.lang) + (" *" if self.field.required else "")

    # --- çizim ------------------------------------------------------------------------------

    def build(self) -> None:
        kind = self.field.kind
        if kind in ("object", "objlist", "kv", "tags", "multi", "union", "file", "json"):
            self.title()
        getattr(self, f"build_{kind}", self.build_json)()

    def title(self) -> None:
        with ui.row().classes("items-center gap-2"):
            ui.label(self.label).classes("text-[13px] font-semibold")
            if self.field.deprecated:
                self.deprecated_badge()

    def deprecated_badge(self) -> None:
        ui.label(t("form.deprecated", self.lang)).classes("gx-pill").style("background:#FBEBC2;color:#854D0E")

    def simple_label(self) -> str:
        return self.label + (f"  · {t('form.deprecated', self.lang)}" if self.field.deprecated else "")

    def build_text(self) -> None:
        field = self.field
        self.element = ui.input(self.simple_label(), placeholder=field.placeholder,
                                value="" if field.default is None else str(field.default)).classes("w-full")
        if field.format in FORMAT_INPUT_TYPES:
            self.element.props(f"type={FORMAT_INPUT_TYPES[field.format]}")
        if field.format in MONO_FORMATS:
            self.element.props("input-class=gx-mono")

    def build_textarea(self) -> None:
        self.element = ui.textarea(self.simple_label(), placeholder=self.field.placeholder,
                                   value=self.field.default or "").classes("w-full")

    def build_date(self) -> None:
        self.element = ui.input(self.simple_label(), value=self.field.default or "").classes("w-full") \
            .props(f"type={HTML_INPUT_TYPES[self.field.kind]} stack-label")

    build_time = build_datetime = build_date

    def build_number(self) -> None:
        field = self.field
        self.element = ui.number(self.simple_label(), value=field.default, min=field.minimum, max=field.maximum,
                                 step=field.step, placeholder=field.placeholder).classes("w-full")

    def build_toggle(self) -> None:
        self.element = ui.switch(self.simple_label(), value=bool(self.field.default))

    def build_select(self) -> None:
        field = self.field
        self.element = ui.select(field.options, label=self.simple_label(), value=field.default,
                                 with_input=field.kind == "search", clearable=not field.required).classes("w-full")

    build_search = build_select

    def build_const(self) -> None:
        self.element = ui.input(self.simple_label(), value=str(self.field.default)).classes("w-full").props("readonly")

    def build_json(self) -> None:
        self.element = ui.textarea(placeholder="{ }", value=initial_text(self.field)).classes("w-full") \
            .props("input-class=gx-mono autogrow")

    def build_multi(self) -> None:
        """array<enum>: çoklu seçim (seçilenler çip olarak görünür)."""
        self.element = ui.select(self.field.options, multiple=True, value=list(self.field.default or [])) \
            .props("use-chips").classes("w-full")

    def build_tags(self) -> None:
        """array<string|number>: yaz + Enter, ✕ ile sil."""
        self.element = ui.select([], multiple=True, new_value_mode="add-unique", with_input=True,
                                 value=[str(v) for v in self.field.default or []]) \
            .props(f'use-chips hide-dropdown-icon input-debounce=0 hint="{t("form.tag_hint", self.lang)}"') \
            .classes("w-full")

    def build_object(self) -> None:
        """object: çerçeveli alt grup; alt alanlar yan yana ızgarada."""
        with ui.element("div").classes("w-full p-3").style(
                "border:1px solid var(--gx-border);border-radius:8px;display:grid;"
                "grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px"):
            self.children = [FieldInput(child, self.lang) for child in self.field.children]

    def build_objlist(self) -> None:
        """array<object>: satır ekle/sil tablosu. Satır eklemek mevcut satırları yeniden çizmez."""
        self.rows: list[tuple[ui.element, list[FieldInput]]] = []
        self.rows_box = ui.column().classes("w-full gap-2")
        for _ in range(max(self.field.min_items or 0, 1)):
            self.add_row()
        ui.button(t("form.add_row", self.lang), on_click=self.add_row).props("flat dense no-caps")

    def add_row(self) -> None:
        with self.rows_box:
            with ui.row().classes("w-full items-start no-wrap gap-2 p-2").style(
                    "border:1px solid var(--gx-border);border-radius:8px") as row:
                with ui.element("div").classes("flex-1").style(
                        "display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:8px"):
                    inputs = [FieldInput(child, self.lang, compact=True) for child in self.field.children]
                ui.button(icon="close", on_click=lambda r=row: self.remove_row(r)) \
                    .props(f'flat round dense aria-label="{t("form.remove_row", self.lang)}"')
        self.rows.append((row, inputs))

    def remove_row(self, row) -> None:
        self.rows = [(r, inputs) for r, inputs in self.rows if r is not row]
        row.delete()

    def build_kv(self) -> None:
        """additionalProperties: anahtar = değer satırları."""
        self.pairs: list[tuple[ui.element, ui.input, FieldInput]] = []
        self.pairs_box = ui.column().classes("w-full gap-2")
        for key, value in (self.field.default or {}).items():
            self.add_pair(key, value)
        if not self.pairs:
            self.add_pair()                 # boş başlamasın: bir satır hazır olsun
        ui.button(t("form.add_pair", self.lang), on_click=lambda: self.add_pair()).props("flat dense no-caps")

    def add_pair(self, key: str = "", value: object = None) -> None:
        item = self.field.item or Field(name="", kind="text")
        with self.pairs_box:
            with ui.row().classes("w-full items-center no-wrap gap-2") as row:
                key_input = ui.input(t("form.key", self.lang), value=key).classes("flex-1").props("input-class=gx-mono")
                ui.label("=")
                with ui.element("div").classes("flex-1"):
                    value_field = Field(name="", kind=item.kind, label=t("form.value", self.lang),
                                        options=item.options, default=value, integer=item.integer)
                    value_input = FieldInput(value_field, self.lang, compact=True)
                ui.button(icon="close", on_click=lambda r=row: self.remove_pair(r)).props("flat round dense")
        self.pairs.append((row, key_input, value_input))

    def remove_pair(self, row) -> None:
        self.pairs = [p for p in self.pairs if p[0] is not row]
        row.delete()

    def build_union(self) -> None:
        """oneOf/anyOf: önce tür seçilir, sonra o türün bileşeni görünür."""
        # Türün adı: şemada kendi başlığı varsa o (ör. "Konum"), yoksa bileşen türünün okunur adı (ör. "Sayı").
        names = [variant.label if variant.label not in (self.field.name, self.field.label) else t(f"kinds.{variant.kind}", self.lang)
                 for variant in self.field.variants]
        self.variant = ui.select(dict(enumerate(names)), value=0, label=t("form.variant", self.lang),
                                 on_change=lambda: self.show_variant()).classes("w-48")
        self.variant_inputs = []
        for variant, name in zip(self.field.variants, names):
            with ui.column().classes("w-full") as box:
                self.variant_inputs.append((box, FieldInput(replace(variant, label=name), self.lang, compact=True)))
        self.show_variant()

    def show_variant(self) -> None:
        for index, (box, _) in enumerate(self.variant_inputs):
            box.set_visibility(index == self.variant.value)

    def build_file(self) -> None:
        """format: binary → dosya yükleme. Dosya tarayıcıdan sunucuya gelir, istekte multipart olarak gider."""
        self.file: tuple[str, bytes] | None = None
        ui.upload(on_upload=self.on_upload, auto_upload=True, max_files=1, label=t("form.file_pick", self.lang)) \
            .props(f'accept="{self.field.accept}"' if self.field.accept else "").classes("w-full")

    async def on_upload(self, event: events.UploadEventArguments) -> None:
        self.file = (event.file.name, await event.file.read())

    # --- okuma ------------------------------------------------------------------------------

    def read(self) -> object:
        """Alanın API'ye gidecek değeri. Hatalı girişte ValueError (mesajı alanın altında gösterilir)."""
        if self.null_box is not None and self.null_box.value:
            return None
        kind = self.field.kind
        if kind == "object":
            return object_value({child.field.name: child.read() for child in self.children})
        if kind == "objlist":
            rows = [object_value({i.field.name: i.read() for i in inputs}) for _, inputs in self.rows]
            return list_value(rows)
        if kind == "kv":
            return kv_value([(key.value or "", value.read()) for _, key, value in self.pairs])
        if kind == "multi":
            return list(self.element.value) if self.element.value else NOT_SENT
        if kind == "tags":
            item = self.field.item or Field(name="", kind="text")
            return list_value([read_value(item, raw) for raw in self.element.value or []])
        if kind == "union":
            return self.variant_inputs[self.variant.value][1].read()
        if kind == "file":
            return self.file if self.file else NOT_SENT
        return read_value(self.field, self.element.value)

    def show_error(self, message: str) -> None:
        self.error_label.set_text(message)
        self.error_label.set_visibility(bool(message))
