"""Onay penceresi (tasarim-v1 §7): çalıştırmadan önce "ne gidecek" gösterilir ve onay istenir.

Ne zaman: endpoint onay istiyorsa (x-gadget.confirm ya da portal katmanı) ve DELETE'de HER ZAMAN.
Ne gösterir: metot + tam adres + parametreler/gövde (gizli alanlar maskeli).
DELETE: kırmızı, "geri alınamaz"; kaydın kimliği yazılmadan "Evet, sil" açılmaz.
Geçmişten "Tekrar çalıştır" da aynı pencereden geçer.
"""
import json

from nicegui import ui

from core import history
from core.i18n import t
from core.models import EndpointView, Request


def needs_confirmation(view: EndpointView) -> bool:
    return view.endpoint.confirm or view.endpoint.method == "DELETE"


def delete_key(view: EndpointView, values: dict) -> str:
    """DELETE'de yazılması istenen kimlik: path parametrelerinin değeri (yoksa metot+path)."""
    parts = [str(values[f.name]) for f in view.fields if f.location == "path" and f.name in values]
    return " ".join(parts) or view.endpoint.key


async def confirm_run(lang: str, view: EndpointView, request: Request, values: dict) -> bool:
    """Pencereyi açar, cevabı bekler: True = çalıştır."""
    is_delete = view.endpoint.method == "DELETE"
    secrets = history.secret_field_names(view.fields)
    shown = {"params": history.mask(request.params, secrets), "body": history.mask(request.json, secrets)
             if request.json is not None else history.mask(request.data, secrets)}
    key = delete_key(view, values)

    with ui.dialog() as dialog, ui.card().classes("w-full max-w-xl gap-3"):
        title = t("confirm.delete_title" if is_delete else "confirm.title", lang)
        ui.label(title).classes("text-lg font-bold").style("color:#991B1B" if is_delete else "")
        ui.label(t("confirm.will_run", lang)).classes("gx-muted text-sm")
        ui.label(f"{request.method} {request.url}").classes("gx-mono text-sm p-2 w-full").style(
            "background:var(--gx-card);border-radius:6px;border:1px solid var(--gx-border)")
        if shown["params"] or shown["body"]:
            ui.label(t("confirm.params", lang)).classes("text-xs gx-muted")
            ui.code(json.dumps({k: v for k, v in shown.items() if v}, indent=2, ensure_ascii=False, default=str),
                    language="json").classes("w-full")
        if is_delete:
            ui.label(t("confirm.delete_warning", lang)).classes("font-semibold").style("color:#991B1B")
            typed = ui.input(t("confirm.delete_type_id", lang, id=key)).classes("w-full").props("outlined input-class=gx-mono")
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button(t("common.cancel", lang), on_click=lambda: dialog.submit(False)).props("flat no-caps")
            run = ui.button(t("confirm.delete_run" if is_delete else "confirm.run", lang),
                            on_click=lambda: dialog.submit(True)).props("unelevated no-caps" + (" color=negative" if is_delete else ""))
        if is_delete:
            run.disable()
            typed.on_value_change(lambda e: run.set_enabled((e.value or "").strip() == key))
    return bool(await dialog)
