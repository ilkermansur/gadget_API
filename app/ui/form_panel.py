"""Form | Docs alanı (tasarim-v1 §4): seçili endpoint'in formu ve dokümanı.

Akış (Çalıştır):
  1. alanlar okunur (FieldInput.read) — hatalı JSON vb. alanın altında gösterilir
  2. portal katmanında eklenmiş kısıtlar denetlenir (core/overlay.py portal_errors)
  3. catalog.run_endpoint: yetki → istek → gönderim → geçmişe yazma
  4. 422 dönerse hatalı alanlar formda işaretlenir; sonuç on_result ile Yanıt alanına verilir

Geliştirici/Admin profilinde "Alanlar | JSON gövde" geçişi: gövde metin olarak düzenlenip olduğu gibi gönderilir.
"""
import asyncio
import json
from dataclasses import replace

from nicegui import ui

from core.errors import describe
from core import catalog, editing
from core.i18n import t
from core.models import MenuItem, Result, User
from core.overlay import portal_errors
from ui.confirm_dialog import confirm_run, needs_confirmation
from ui.docs_panel import render_docs
from ui.edit_drawer import open_edit
from ui.fields import NOT_SENT, WIDE_KINDS, FieldInput
from ui.state import AppContext

GRID = "display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px;width:100%"


class FormPanel:
    def __init__(self, ctx: AppContext, user: User, lang: str, item: MenuItem, developer: bool, source: str,
                 on_result, on_running, initial_values: dict | None = None):
        self.ctx, self.user, self.lang, self.item = ctx, user, lang, item
        # Sayfa yenilenince son çalıştırmanın değerleri forma geri dolar (Sıfırla bunları da siler).
        self.initial_values = dict(initial_values or {})
        self.developer, self.source = developer, source
        self.on_result, self.on_running = on_result, on_running
        # PATCH/PUT formunda "Getir ve düzenle": kimliği formdan al, mevcut değerleri oku, düzenleme panelini aç.
        self.edit_link = editing.link_for_patch(ctx.db, user, item) \
            if item.view.endpoint.method in ("PATCH", "PUT") else None
        self.tab = "form"
        self.json_mode = False
        self.inputs: dict[str, FieldInput] = {}
        self.json_text = "{}"
        self.task: asyncio.Task | None = None
        with ui.column().classes("w-full gap-3"):
            self.tab_bar()
            # Form ve Docs ayrı kutularda; sekme değişince sadece görünürlük değişir, formda yazılanlar kalır.
            with ui.column().classes("w-full gap-3") as self.form_box:
                self.body()
            with ui.column().classes("w-full") as self.docs_box:
                render_docs(self.item.view, self.lang, self.source)
            self.docs_box.set_visibility(False)

    # --- sekmeler -------------------------------------------------------------------------

    @ui.refreshable_method
    def tab_bar(self) -> None:
        with ui.row().classes("w-full items-center gap-2"):
            for key in ("form", "docs"):
                ui.button(t(f"form.tab_{key}", self.lang), on_click=lambda k=key: self.set_tab(k)) \
                    .props("no-caps dense unelevated" + ("" if self.tab == key else " flat color=grey-8")).classes("px-3")
            if self.developer and self.tab == "form" and self.has_body():
                ui.space()
                for mode, key in ((False, "form.fields_tab"), (True, "form.json_body_tab")):
                    ui.button(t(key, self.lang), on_click=lambda m=mode: self.set_json_mode(m)) \
                        .props("no-caps dense outline" + ("" if self.json_mode == mode else " flat color=grey-7"))

    def set_tab(self, tab: str) -> None:
        self.tab = tab
        self.tab_bar.refresh()
        self.form_box.set_visibility(tab == "form")
        self.docs_box.set_visibility(tab == "docs")

    def has_body(self) -> bool:
        return self.item.view.endpoint.body is not None

    # --- form -------------------------------------------------------------------------------

    @ui.refreshable_method
    def body(self) -> None:
        self.inputs = {}
        visible = [f for f in self.item.view.fields if not f.hidden]
        shown = [f for f in visible if not (self.json_mode and f.location == "body")]
        if not visible:
            ui.label(t("form.no_params", self.lang)).classes("gx-muted")
        with ui.element("div").style(GRID):
            for field in shown:
                with ui.element("div").style("grid-column:1 / -1" if field.kind in WIDE_KINDS else ""):
                    if field.name in self.initial_values:
                        field = replace(field, default=self.initial_values[field.name])
                    self.inputs[field.name] = FieldInput(field, self.lang)
        if self.json_mode:
            self.json_box = ui.textarea(t("form.json_body_tab", self.lang), value=self.json_text) \
                .classes("w-full").props("input-class=gx-mono autogrow outlined")
            self.json_error = ui.label("").classes("text-xs").style("color:#B91C1C")
        # Eylem satırı: zorunlu alan notu solda; Sıfırla, Getir ve düzenle, Çalıştır sağa yaslı (Çalıştır en sağda).
        with ui.row().classes("w-full items-center gap-2"):
            if any(f.required for f in visible):
                ui.label(t("common.required_note", self.lang)).classes("gx-muted text-xs")
            ui.space()
            ui.button(t("form.reset", self.lang), on_click=self.reset).props("flat no-caps color=grey-8")
            if self.edit_link is not None:
                ui.button(t("form.fetch_and_edit", self.lang), icon="edit", on_click=self.fetch_and_edit) \
                    .props("outline no-caps")
            self.run_button = ui.button(t("form.run", self.lang), on_click=self.run).props("unelevated no-caps")

    def set_json_mode(self, on: bool) -> None:
        if on and not self.json_mode:
            body = self.collect(only_body=True, quiet=True)
            self.json_text = json.dumps(body, ensure_ascii=False, indent=2, default=str)
        self.json_mode = on
        self.tab_bar.refresh()
        self.body.refresh()

    def reset(self) -> None:
        self.json_text = "{}"
        self.initial_values = {}
        self.body.refresh()

    # --- değerleri toplamak -------------------------------------------------------------------

    def collect(self, only_body: bool = False, quiet: bool = False) -> dict | None:
        """Formdaki değerler. Okunamayan alan varsa altında gösterir ve None döner (quiet: göstermez, atlar)."""
        values, failed = {}, False
        for name, field_input in self.inputs.items():
            if only_body and field_input.field.location != "body":
                continue
            if not quiet:
                field_input.show_error("")
            try:
                value = field_input.read()
            except ValueError as exc:
                failed = True
                if not quiet:
                    field_input.show_error(describe(exc, self.lang))
                continue
            if value is not NOT_SENT:
                values[name] = value
        return None if failed and not quiet else values

    # --- çalıştırma -----------------------------------------------------------------------------

    async def run(self) -> None:
        values = self.collect()
        if values is None:
            return
        problems = portal_errors(self.item.view, values)
        for name, (key, params) in problems.items():
            if name in self.inputs:
                self.inputs[name].show_error(t(key, self.lang, **params))
        if problems:
            return
        json_body = None
        if self.json_mode:
            try:
                json_body = json.loads(self.json_box.value or "{}")
                self.json_error.set_text("")
            except json.JSONDecodeError as exc:
                self.json_error.set_text(t("form.invalid_json", self.lang, reason=exc.msg))
                return

        if needs_confirmation(self.item.view):
            try:
                preview = catalog.preview_request(self.ctx.db, self.ctx.box, self.item.endpoint_id, values, json_body)
            except ValueError as exc:
                ui.notify(describe(exc, self.lang), type="warning")
                return
            if not await confirm_run(self.lang, self.item.view, preview, values):
                return

        self.set_running(True)
        # Ayrı bir görev: Yanıt alanındaki "İptal" bu görevi durdurabilsin.
        self.task = asyncio.create_task(catalog.run_endpoint(
            self.ctx.db, self.ctx.box, self.ctx.tokens, self.user, self.item.endpoint_id, values, json_body))
        try:
            result = await self.task
        except asyncio.CancelledError:
            ui.notify(t("response.cancelled", self.lang))
            self.set_running(False)
            return
        except PermissionError:
            ui.notify(t("response.err_permission", self.lang), type="negative")
            self.set_running(False)
            return
        except ValueError as exc:                      # ör. path parametresi boş
            ui.notify(describe(exc, self.lang), type="warning")
            self.set_running(False)
            return
        self.set_running(False)
        self.mark_field_errors(result)
        self.on_result(result, values)

    async def fetch_and_edit(self) -> None:
        field_input = self.inputs.get(self.edit_link.param)
        record = field_input.read() if field_input else NOT_SENT
        if record in (NOT_SENT, None, ""):
            if field_input:
                field_input.show_error(t("form.required_missing", self.lang))
            return
        await open_edit(self.ctx, self.user, self.lang, self.edit_link, record, on_done=self.after_edit)

    async def after_edit(self, result: Result) -> None:
        self.on_result(result, {})

    def cancel(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()

    def show_form(self) -> None:
        self.set_tab("form")

    def set_running(self, running: bool) -> None:
        self.run_button.set_enabled(not running)
        self.run_button.set_text(t("form.running" if running else "form.run", self.lang))
        self.on_running(running)

    def mark_field_errors(self, result: Result) -> None:
        """422'deki alan hatalarını formdaki üst düzey alanların altına yazar ("items[1].name" → "items")."""
        if not result.error:
            return
        for error in result.error.fields:
            top = error.field.split(".")[0].split("[")[0]
            if top in self.inputs:
                text = error.message if top == error.field else f"{error.field}: {error.message}"
                self.inputs[top].show_error(text)
