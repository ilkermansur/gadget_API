"""Geliştirici · Tanım (tasarim-v1 §8, §13): FastAPI'den gelen endpoint'in SUNUMUNU portal katmanında düzeltmek.

Ekran iki sütun:
  sol  → düzenleme: endpoint anahtarları + üst düzey alanlar. Her satırda FastAPI'deki değer, farklıysa
         "portalda düzenlendi" etiketi ve "FastAPI'ye dön". Kilitli olanlar (metot, path, alan adı/konumu) salt okunur.
  sağ  → canlı operatör önizlemesi: taslak katman uygulanmış form (çalışmaz, sadece görünüş).

Akış: düzenlemeler bir TASLAK portal katmanında birikir (core/overlay.py set_override sadece farkları tutar);
"Kaydet ve yayınla" taslağı kaydeder (catalog.save_overlay), menü ve form yeni haliyle yeniden çizilir.
Hangi anahtarın düzeltilebildiği core/overlay.py'de yazılıdır; bu ekran o listeyi gösterir, kural koymaz.
"""
import copy

from nicegui import ui

from core import catalog
from core.i18n import t
from core.models import Field, MenuItem, Overlay
from core.openapi import UNTAGGED
from core.overlay import (SIMPLE_KINDS, apply_overlay, build_view, coerce_default, parse_options, set_override)
from ui import theme
from ui.fields import FieldInput
from ui.state import AppContext

# Ekranda gösterilen sıra (core/overlay.py'deki düzeltilebilir anahtarlardan).
ENDPOINT_KEYS = ["summary", "summary_en", "description", "description_en", "group", "view", "confirm",
                 "list_path", "read_from", "row_action", "dry_run"]
FIELD_KEYS = ["label", "label_en", "help", "help_en", "placeholder", "default", "kind", "options",
              "minimum", "maximum", "pattern", "required", "hidden"]
BOOL_KEYS = {"confirm", "dry_run", "required", "hidden"}
LONG_TEXT_KEYS = {"description", "description_en", "help", "help_en"}
NUMBER_KEYS = {"minimum", "maximum"}
VIEWS = ["auto", "table", "json", "raw"]


def shown(value) -> str:
    """FastAPI'deki değerin kısa gösterimi."""
    if value in (None, "", []):
        return "—"
    if isinstance(value, bool):
        return "✓" if value else "✗"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, list):
        return ", ".join(map(str, value))
    text = str(value)
    return text if len(text) <= 60 else text[:57] + "…"


class DefinitionPanel:
    def __init__(self, ctx: AppContext, lang: str, item: MenuItem, on_saved):
        self.ctx, self.lang, self.item, self.on_saved = ctx, lang, item, on_saved
        entry = catalog.get_entry(ctx.db, item.endpoint_id)
        doc = catalog.load_doc(ctx.db, entry.resource_id)
        endpoint = next(e for e in doc.endpoints if e.key == entry.key)
        self.base = build_view(endpoint, doc.schemas)                     # FastAPI'nin dediği (katmansız)
        self.draft: Overlay = copy.deepcopy(catalog.get_overlay(ctx.db, item.endpoint_id))
        self.badges: dict[tuple, ui.element] = {}                         # (alan yolu, anahtar) → etiket satırı

        with ui.row().classes("w-full gap-6 items-start"):
            with ui.column().classes("gap-3 min-w-0").style("flex:1 1 520px"):
                ui.label(t("definition.hint", lang)).classes("gx-muted text-xs")
                with ui.row().classes("items-center gap-2"):
                    theme.pill(endpoint.method, theme.METHOD_COLORS.get(endpoint.method, theme.METHOD_COLORS["GET"]))
                    ui.label(endpoint.path).classes("gx-mono text-sm")
                    ui.icon("lock", size="16px").classes("gx-muted").tooltip(t("definition.locked", lang))
                ui.label(t("definition.locked", lang)).classes("gx-muted text-xs -mt-2")
                with ui.expansion(t("definition.endpoint_part", lang), value=False).classes("w-full gx-card"):
                    for key in ENDPOINT_KEYS:
                        self.editor_row(key, getattr(self.base.endpoint, key), None, self.base.endpoint)
                with ui.expansion(t("definition.fields_part", lang), value=False).classes("w-full gx-card"):
                    ui.label(t("definition.nested_note", lang)).classes("gx-muted text-xs")
                    for base_field in self.base.fields:
                        self.field_block(base_field)
                with ui.row().classes("items-center gap-3"):
                    ui.button(t("definition.save", lang), icon="publish", on_click=self.save).props("unelevated no-caps")
                    self.summary_label = ui.label("").classes("gx-muted text-sm")
            with ui.column().classes("gap-2").style("flex:0 1 380px;position:sticky;top:12px"):
                ui.label(t("definition.preview", lang)).classes("text-xs font-semibold gx-muted")
                with ui.column().classes("w-full gap-3 p-4").style(
                        "border:1px dashed var(--gx-border);border-radius:10px;background:var(--gx-input)"):
                    self.preview()
        self.update_summary()

    # --- düzenleme satırları ------------------------------------------------------------------

    def field_block(self, base_field: Field) -> None:
        path = base_field.name
        title = f"{base_field.name or t('common.body', self.lang)} · {base_field.location} · {t(f'kinds.{base_field.kind}', self.lang)}"
        with ui.expansion(title, icon="edit_note" if path in self.draft.fields else "tune") \
                .classes("w-full").props("dense header-class=gx-mono"):
            for key in FIELD_KEYS:
                if key == "kind" and base_field.kind not in SIMPLE_KINDS:
                    continue                            # bileşik tipin bileşeni değişmez (overlay.SIMPLE_KINDS)
                self.editor_row(key, getattr(base_field, key), path, base_field)

    def current(self, key: str, path: str | None):
        """Taslaktaki değer; taslakta yoksa FastAPI'deki."""
        changes = self.draft.endpoint if path is None else self.draft.fields.get(path, {})
        if key in changes:
            return changes[key]
        source = self.base.endpoint if path is None else next(f for f in self.base.fields if f.name == path)
        return getattr(source, key)

    def editor_row(self, key: str, base_value, path: str | None, base_obj) -> None:
        label = t(f"definition.key.{key}", self.lang)
        value = self.current(key, path)
        with ui.column().classes("w-full gap-0 py-1"):
            change = lambda e, k=key, p=path, b=base_obj: self.changed(k, p, b, e.value)
            if key in BOOL_KEYS:
                widget = ui.switch(label, value=bool(value), on_change=change)
            elif key == "view":
                widget = ui.select({v: t(f"definition.view.{v}", self.lang) for v in VIEWS}, label=label,
                                   value=value, on_change=change).props("outlined dense").classes("w-full")
            elif key == "kind":
                widget = ui.select({k: t(f"kinds.{k}", self.lang) for k in sorted(SIMPLE_KINDS)}, label=label,
                                   value=value, on_change=change).props("outlined dense").classes("w-full")
            elif key in NUMBER_KEYS:
                widget = ui.number(label, value=value, on_change=change).props("outlined dense clearable").classes("w-full")
            elif key in LONG_TEXT_KEYS:
                widget = ui.textarea(label, value=value or "", on_change=change).props("outlined dense autogrow") \
                    .classes("w-full")
            else:
                text = ", ".join(map(str, value)) if key == "options" else ("" if value is None else str(value))
                hint = {"options": "definition.options_hint", "default": "definition.default_hint"}.get(key)
                widget = ui.input(label, value=text, on_change=change).props(
                    "outlined dense" + (f' hint="{t(hint, self.lang)}"' if hint else "")).classes("w-full")
            with ui.row().classes("items-center gap-2 text-xs") as badge:
                ui.label(t("definition.from_fastapi", self.lang, value=shown(base_value))).classes("gx-muted")
                edited = ui.row().classes("items-center gap-2")
                with edited:
                    ui.label(t("definition.edited", self.lang)).classes("font-semibold px-2 rounded").style(
                        f"background:{theme.STATUS_COLORS['4xx'][0]};color:{theme.STATUS_COLORS['4xx'][1]}")
                    ui.button(t("definition.revert", self.lang), on_click=lambda w=widget, k=key, b=base_value:
                              self.revert(w, k, b)).props("flat dense no-caps size=sm")
            self.badges[(path, key)] = edited
            edited.set_visibility(self.is_overridden(key, path))

    def is_overridden(self, key: str, path: str | None) -> bool:
        changes = self.draft.endpoint if path is None else self.draft.fields.get(path, {})
        return key in changes

    # --- olaylar ---------------------------------------------------------------------------

    def changed(self, key: str, path: str | None, base_obj, raw) -> None:
        try:
            value = self.convert(key, raw, base_obj)
        except ValueError:
            ui.notify(t("definition.invalid", self.lang, key=key), type="warning")
            return
        set_override(self.draft, self.base, key, value, field_path=path)
        self.badges[(path, key)].set_visibility(self.is_overridden(key, path))
        self.update_summary()
        self.preview.refresh()

    def convert(self, key: str, raw, base_obj):
        """Bileşenden gelen değeri FastAPI'deki değerle karşılaştırılabilir tipe çevirir."""
        base_value = getattr(base_obj, key)
        if key == "options":
            return parse_options(raw)
        if key == "default":
            return coerce_default(base_obj, raw)
        if key in BOOL_KEYS:
            return bool(raw)
        if key in NUMBER_KEYS:
            return None if raw in (None, "") else float(raw)
        if isinstance(raw, str) and raw.strip() == "" and base_value in (None, ""):
            return base_value                            # boş bırakmak = FastAPI'deki boşluk
        return raw

    def revert(self, widget, key: str, base_value) -> None:
        """FastAPI'ye dön: bileşen FastAPI'deki değere döner; on_change farkı taslaktan siler."""
        if key == "options":
            widget.value = ", ".join(map(str, base_value or []))
        elif key in BOOL_KEYS:
            widget.value = bool(base_value)
        elif key in NUMBER_KEYS or key in ("view", "kind"):
            widget.value = base_value
        else:
            widget.value = "" if base_value is None else str(base_value)

    def update_summary(self) -> None:
        count = len(self.draft.endpoint) + sum(len(c) for c in self.draft.fields.values())
        self.summary_label.set_text(t("definition.changes", self.lang, n=count) if count
                                    else t("definition.no_changes", self.lang))

    @ui.refreshable_method
    def preview(self) -> None:
        view = apply_overlay(self.base, self.draft)
        endpoint = view.endpoint
        name = endpoint.summary_en if self.lang == "en" and endpoint.summary_en else endpoint.summary
        ui.label(name or endpoint.path).classes("font-bold")
        group = t("common.untagged_group", self.lang) if endpoint.group == UNTAGGED else endpoint.group
        ui.label(f"{group} · {endpoint.method} {endpoint.path}").classes("gx-muted text-xs gx-mono")
        for field in view.fields:
            if not field.hidden:
                FieldInput(field, self.lang, compact=True)
        ui.button(t("form.run", self.lang)).props("unelevated no-caps disable")

    def save(self) -> None:
        catalog.save_overlay(self.ctx.db, self.item.endpoint_id, self.draft)
        ui.notify(t("definition.saved", self.lang), type="positive")
        self.on_saved()
