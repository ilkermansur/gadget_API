"""Kayıt düzenleme paneli (tasarim-v1 §6): sağdan açılır.

  1. mevcut değerler okuma GET'iyle alınır (kullanıcının yetkisiyle, geçmişe de yazılır)
  2. form: PATCH gövdesinin alanları, mevcut değerlerle dolu
  3. "Değişiklikleri gözden geçir": eski → yeni listesi ve PATCH gövdesi (SADECE değişenler); hiç değişiklik
     yoksa ilerlenemez
  4. "Onayla ve gönder": PATCH gider; sonuç on_done'a verilir
Akışın mantığı (hangi PATCH, hangi GET, ne değişti) core/editing.py'dedir; burada sadece ekran var.
"""
import json
from dataclasses import replace

from nicegui import ui

from core.errors import describe
from core import catalog
from core.editing import EditLink, changed_values
from core.i18n import t
from core.models import User
from ui.fields import NOT_SENT, FieldInput
from ui.state import AppContext


async def open_edit(ctx: AppContext, user: User, lang: str, link: EditLink, record_id: object, on_done) -> None:
    read = await catalog.run_endpoint(ctx.db, ctx.box, ctx.tokens, user, link.read_id, {link.param: record_id})
    if read.outcome != "ok" or not isinstance(read.body, dict):
        ui.notify(t("edit.load_failed", lang) + (f" ({read.status})" if read.status else ""), type="negative")
        return
    patch_view = catalog.endpoint_view(ctx.db, catalog.get_entry(ctx.db, link.patch_id))
    EditDrawer(ctx, user, lang, link, record_id, read.body, patch_view, on_done)


class EditDrawer:
    def __init__(self, ctx, user, lang, link: EditLink, record_id, current: dict, patch_view, on_done):
        self.ctx, self.user, self.lang, self.link = ctx, user, lang, link
        self.record_id, self.current, self.on_done = record_id, current, on_done
        self.fields = [f for f in patch_view.fields if f.location == "body" and not f.hidden]
        self.changes: dict = {}

        with ui.dialog().props("position=right") as self.dialog, \
                ui.card().classes("gap-3").style("width:min(520px,100vw);height:100vh;max-height:100vh;overflow:auto"):
            with ui.row().classes("w-full items-center"):
                ui.label(t("edit.title", lang)).classes("text-lg font-bold")
                ui.space()
                ui.button(icon="close", on_click=self.dialog.close).props("flat round dense")
            ui.label(f"{t('edit.loaded', lang)} {record_id}").classes("gx-muted text-sm gx-mono")
            with ui.column().classes("w-full gap-3") as self.form_step:
                self.inputs = {f.name: FieldInput(replace(f, default=current.get(f.name, f.default)), lang)
                               for f in self.fields}
                self.form_error = ui.label("").classes("text-sm").style("color:#B91C1C")
                ui.button(t("edit.review", lang), on_click=self.review).props("unelevated no-caps")
            with ui.column().classes("w-full gap-3") as self.review_step:
                self.review_area()
            self.review_step.set_visibility(False)
        self.dialog.open()

    def edited_values(self) -> dict | None:
        values = {}
        for name, field_input in self.inputs.items():
            field_input.show_error("")
            try:
                value = field_input.read()
            except ValueError as exc:
                field_input.show_error(describe(exc, self.lang))
                return None
            values[name] = None if value is NOT_SENT else value
        return values

    def review(self) -> None:
        values = self.edited_values()
        if values is None:
            return
        self.changes = changed_values({k: self.current.get(k) for k in self.inputs}, values)
        if not self.changes:
            self.form_error.set_text(t("edit.no_changes", self.lang))
            return
        self.form_error.set_text("")
        self.review_area.refresh()
        self.form_step.set_visibility(False)
        self.review_step.set_visibility(True)

    @ui.refreshable_method
    def review_area(self) -> None:
        ui.label(t("edit.changes_title", self.lang)).classes("text-xs font-semibold gx-muted")
        labels = {f.name: f.label for f in self.fields}
        for name, new in self.changes.items():
            with ui.row().classes("w-full items-center gap-2 p-2").style("background:#FEF9C3;border-radius:6px"):
                ui.label(labels.get(name, name)).classes("font-semibold text-sm")
                ui.label(f"{self.current.get(name)!r} → {new!r}").classes("gx-mono text-sm")
                ui.space()
                ui.button(t("edit.undo", self.lang), on_click=lambda n=name: self.undo(n)).props("flat dense no-caps")
        ui.label(t("edit.patch_body", self.lang)).classes("text-xs font-semibold gx-muted")
        ui.code(json.dumps(self.changes, indent=2, ensure_ascii=False, default=str), language="json").classes("w-full")
        with ui.row().classes("w-full gap-2"):
            ui.button(t("edit.back", self.lang), on_click=self.back).props("flat no-caps")
            ui.button(t("edit.confirm_send", self.lang), on_click=self.send).props("unelevated no-caps") \
                .set_enabled(bool(self.changes))

    def undo(self, name: str) -> None:
        """Bir değişikliği geri al: gönderilecekler listesinden çıkar ve formdaki değeri eski haline getir."""
        self.changes.pop(name, None)
        element = getattr(self.inputs[name], "element", None)
        if element is not None:
            element.value = self.current.get(name)
        self.review_area.refresh()

    def back(self) -> None:
        self.review_step.set_visibility(False)
        self.form_step.set_visibility(True)

    async def send(self) -> None:
        result = await catalog.run_endpoint(self.ctx.db, self.ctx.box, self.ctx.tokens, self.user, self.link.patch_id,
                                            {self.link.param: self.record_id, **self.changes})
        if result.outcome == "ok":
            ui.notify(t("edit.saved", self.lang), type="positive")
            self.dialog.close()
        await self.on_done(result)
