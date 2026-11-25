"""Geliştirici · Custom endpoint tanımı (tasarim-v1 §8): şemada olmayan bir isteği elle tanımlamak.

Pencere 6 bölüm + sağda canlı operatör önizlemesi:
  1 Genel (ad, grup, metot, path, adres, ödünç kimlik) · 2 Parametreler · 3 Yanıt · 4 Docs · 5 Erişim · 6 Düzenleme bağlantısı
Ekran sadece bir CustomSpec doldurur; doğrulama, OpenAPI'ye çevirme ve saklama core/custom.py'dedir.
Önizleme, kaydedilecek OpenAPI işleminin kendisinden kurulur: operatörün göreceği form tam olarak budur.
"""
from nicegui import ui

from core.errors import describe
from core import custom, resources
from core.i18n import t
from core.models import CustomParam, CustomSpec, Field, Resource
from core.openapi import parse_openapi
from core.overlay import build_view, coerce_default, parse_options
from ui import theme
from ui.definition import VIEWS
from ui.fields import FieldInput
from ui.state import AppContext


class CustomEditor:
    def __init__(self, ctx: AppContext, lang: str, custom_resource: Resource, on_saved,
                 endpoint_id: int | None = None):
        self.ctx, self.lang, self.custom_resource, self.on_saved = ctx, lang, custom_resource, on_saved
        self.endpoint_id = endpoint_id
        self.spec = custom.load_spec(ctx.db, endpoint_id) if endpoint_id else \
            CustomSpec(method="GET", path="", base_url="", summary="")
        lenders = {r.id: r.name for r in resources.list_resources(ctx.db, ctx.box) if r.kind != "custom"}

        with ui.dialog().props("maximized") as self.dialog, ui.card().classes("w-full h-full gap-3").style("background:var(--gx-bg)"):
            with ui.row().classes("w-full items-center"):
                ui.label(t("custom.title_edit" if endpoint_id else "custom.title_new", lang)).classes("text-lg font-bold")
                ui.space()
                ui.button(icon="close", on_click=self.dialog.close).props("flat round dense")
            with ui.row().classes("w-full gap-6 items-start no-wrap flex-1 min-h-0"):
                with ui.scroll_area().classes("h-full").style("flex:1 1 620px"):
                    with ui.column().classes("w-full gap-2 pr-3"):
                        self.general(lenders)
                        with self.part("custom.part_params"):
                            ui.label(t("custom.path_param_note", lang)).classes("gx-muted text-xs")
                            self.params_area()
                            ui.button(t("custom.add_param", lang), icon="add", on_click=self.add_param) \
                                .props("outline dense no-caps")
                        with self.part("custom.part_response"):
                            self.select("custom.view", {v: t(f"definition.view.{v}", lang) for v in VIEWS}, "view")
                            self.text("custom.list_path", "list_path")
                        with self.part("custom.part_docs"):
                            ui.textarea(t("custom.docs", lang), value=self.spec.description,
                                        on_change=lambda e: self.set("description", e.value)) \
                                .props("outlined autogrow").classes("w-full")
                        with self.part("custom.part_access"):
                            ui.switch(t("custom.confirm", lang), value=self.spec.confirm,
                                      on_change=lambda e: self.set("confirm", e.value))
                            ui.label(t("custom.access_note", lang)).classes("gx-muted text-xs")
                        with self.part("custom.part_edit"):
                            self.text("custom.read_from", "read_from")
                            self.text("custom.row_action", "row_action")
                with ui.column().classes("gap-2").style("flex:0 1 380px"):
                    ui.label(t("definition.preview", lang)).classes("text-xs font-semibold gx-muted")
                    with ui.column().classes("w-full gap-3 p-4").style(
                            "border:1px dashed var(--gx-border);border-radius:10px;background:var(--gx-input)"):
                        self.preview()
            self.error = ui.label("").classes("text-sm").style(f"color:{theme.STATUS_COLORS['5xx'][1]}")
            with ui.row().classes("w-full items-center gap-2"):
                if endpoint_id:
                    ui.button(t("custom.delete", lang), icon="delete", on_click=self.delete) \
                        .props("flat no-caps color=negative")
                ui.space()
                ui.button(t("common.cancel", lang), on_click=self.dialog.close).props("flat no-caps")
                ui.button(t("custom.save", lang), icon="publish", on_click=self.save).props("unelevated no-caps")
        self.dialog.open()

    # --- bölümler ---------------------------------------------------------------------------

    def part(self, key: str):
        return ui.expansion(t(key, self.lang), value=False).classes("w-full gx-card").props("header-class=font-semibold")

    def general(self, lenders: dict) -> None:
        with self.part("custom.part_general"):
            with ui.row().classes("w-full gap-2 no-wrap"):
                self.text("custom.summary", "summary", classes="flex-1")
                self.text("custom.group", "group", classes="w-48")
            with ui.row().classes("w-full gap-2 no-wrap"):
                self.select("custom.method", custom.METHODS, "method", classes="w-32")
                self.text("custom.path", "path", classes="flex-1", mono=True)
            self.text("custom.base_url", "base_url", mono=True)
            self.full_url = ui.label("").classes("gx-muted text-xs gx-mono")
            self.select("custom.auth_from", {None: t("custom.no_auth", self.lang), **lenders}, "auth_from")
            self.update_full_url()

    def text(self, key: str, attr: str, classes: str = "w-full", mono: bool = False) -> None:
        ui.input(t(key, self.lang), value=getattr(self.spec, attr) or "",
                 on_change=lambda e, a=attr: self.set(a, e.value or "")) \
            .props("outlined dense" + (" input-class=gx-mono" if mono else "")).classes(classes)

    def select(self, key: str, options, attr: str, classes: str = "w-full") -> None:
        ui.select(options, label=t(key, self.lang), value=getattr(self.spec, attr),
                  on_change=lambda e, a=attr: self.set(a, e.value)).props("outlined dense").classes(classes)

    @ui.refreshable_method
    def params_area(self) -> None:
        kinds = {k: t(f"custom.kind.{k}", self.lang) for k in custom.PARAM_KINDS}
        for index, param in enumerate(self.spec.params):
            with ui.column().classes("w-full gap-1 p-2").style(
                    "border:1px solid var(--gx-border);border-radius:8px;background:var(--gx-input)"):
                with ui.row().classes("w-full gap-2 no-wrap items-center"):
                    self.param_input("custom.param_name", param, "name", "w-40", mono=True)
                    self.param_input("custom.param_label", param, "label", "flex-1")
                    ui.select(kinds, label=t("custom.param_kind", self.lang), value=param.kind,
                              on_change=lambda e, p=param: self.set_param(p, "kind", e.value, redraw=True)) \
                        .props("outlined dense").classes("w-36")
                    ui.select(custom.LOCATIONS, label=t("custom.param_location", self.lang), value=param.location,
                              on_change=lambda e, p=param: self.set_param(p, "location", e.value)) \
                        .props("outlined dense").classes("w-28")
                    ui.button(icon="delete", on_click=lambda i=index: self.remove_param(i)) \
                        .props("flat round dense color=negative")
                with ui.row().classes("w-full gap-2 no-wrap items-center"):
                    ui.input(t("custom.param_default", self.lang),
                             value="" if param.default is None else str(param.default),
                             on_change=lambda e, p=param: self.set_default(p, e.value)) \
                        .props("outlined dense").classes("w-40")
                    if param.kind == "select":
                        ui.input(t("custom.param_options", self.lang), value=", ".join(map(str, param.options)),
                                 on_change=lambda e, p=param: self.set_param(p, "options", parse_options(e.value))) \
                            .props("outlined dense").classes("flex-1")
                    self.param_input("custom.param_help", param, "help", "flex-1")
                    ui.switch(t("custom.param_required", self.lang), value=param.required,
                              on_change=lambda e, p=param: self.set_param(p, "required", e.value))

    def param_input(self, key: str, param: CustomParam, attr: str, classes: str, mono: bool = False) -> None:
        ui.input(t(key, self.lang), value=getattr(param, attr) or "",
                 on_change=lambda e, p=param, a=attr: self.set_param(p, a, (e.value or "").strip())) \
            .props("outlined dense" + (" input-class=gx-mono" if mono else "")).classes(classes)

    @ui.refreshable_method
    def preview(self) -> None:
        try:
            custom.check_spec(self.spec)
            path = self.spec.path
            doc = parse_openapi({"openapi": "3.1.0", "info": {"title": "", "version": ""},
                                 "paths": {path: {self.spec.method.lower(): custom.to_operation(self.spec)}}})
            view = build_view(doc.endpoints[0], doc.schemas)
        except (ValueError, KeyError, IndexError) as exc:
            ui.label(t("custom.preview_error", self.lang, error=describe(exc, self.lang))).classes("gx-muted text-sm")
            return
        ui.label(view.endpoint.summary).classes("font-bold")
        ui.label(f"{view.endpoint.group} · {view.endpoint.method} {path}").classes("gx-muted text-xs gx-mono")
        for field in view.fields:
            FieldInput(field, self.lang, compact=True)
        ui.button(t("form.run", self.lang)).props("unelevated no-caps disable")

    # --- olaylar ---------------------------------------------------------------------------

    def set(self, attr: str, value) -> None:
        setattr(self.spec, attr, value)
        if attr in ("base_url", "path"):
            self.update_full_url()
        self.preview.refresh()

    def set_param(self, param: CustomParam, attr: str, value, redraw: bool = False) -> None:
        setattr(param, attr, value)
        if redraw:
            self.params_area.refresh()
        self.preview.refresh()

    def set_default(self, param: CustomParam, text: str) -> None:
        kind = "number" if param.kind in ("number", "integer") else param.kind
        try:
            # coerce_default sadece alanın tipine bakar: tip bilgisini taşıyan geçici bir Field yeter
            param.default = coerce_default(Field(name="", kind=kind, integer=param.kind == "integer"), text)
        except ValueError:
            return                                       # yarım yazılmış sayı: önizleme eski değerle kalır
        self.preview.refresh()

    def add_param(self) -> None:
        self.spec.params.append(CustomParam(name=f"param{len(self.spec.params) + 1}"))
        self.params_area.refresh()
        self.preview.refresh()

    def remove_param(self, index: int) -> None:
        self.spec.params.pop(index)
        self.params_area.refresh()
        self.preview.refresh()

    def update_full_url(self) -> None:
        self.full_url.set_text(t("custom.full_url", self.lang, url=self.spec.base_url.rstrip("/") + self.spec.path))

    async def save(self) -> None:
        try:
            endpoint_id = custom.save_custom(self.ctx.db, self.custom_resource.id, self.spec, self.endpoint_id)
        except ValueError as exc:
            self.error.set_text(describe(exc, self.lang))
            return
        ui.notify(t("custom.saved", self.lang), type="positive")
        self.dialog.close()
        await self.on_saved(endpoint_id)

    async def delete(self) -> None:
        custom.delete_custom(self.ctx.db, self.endpoint_id)
        ui.notify(t("custom.deleted", self.lang))
        self.dialog.close()
        await self.on_saved(None)

