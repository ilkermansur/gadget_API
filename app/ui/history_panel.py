"""Geçmiş sekmesi (tasarim-v1 §5.2): giriş yapan kişinin işlemleri; admin'de "Tüm kullanıcılar (denetim)".

Filtreler: sadece bu endpoint, dönüş kodu (Tümü / 2xx / 4xx / 5xx / Zaman aşımı / Bağlantı yok).
Satır eylemleri: Yanıtı aç (istek + yanıt penceresi), Tekrar çalıştır. CSV: görünen liste.
Kayıtlar core/history.py'den okunur; burada sadece gösterilir. Zamanlar UTC'dir.
"""
import json

from nicegui import ui

from core import history
from core.i18n import t
from core.models import HistoryItem, User
from ui import theme
from ui.state import AppContext

STATUS_FILTERS = ["all", "2xx", "4xx", "5xx", "timeout", "connection_error"]


def can_rerun(values: object) -> bool:
    """Maskeli (gizli) değer içeren kayıt olduğu gibi tekrar gönderilemez: maskenin kendisi giderdi."""
    if isinstance(values, dict):
        return all(can_rerun(v) for v in values.values())
    if isinstance(values, list):
        return all(can_rerun(v) for v in values)
    return values != history.MASK


def show_time(iso: str) -> str:
    return iso.replace("T", " ").rstrip("Z")


class HistoryPanel:
    def __init__(self, ctx: AppContext, user: User, lang: str, endpoint_id: int, on_rerun, file_name: str):
        self.ctx, self.user, self.lang, self.endpoint_id = ctx, user, lang, endpoint_id
        self.on_rerun, self.file_name = on_rerun, file_name
        self.everyone, self.only_this, self.status = False, False, "all"
        with ui.column().classes("w-full gap-3"):
            self.filters()
            self.listing()

    def items(self) -> list[HistoryItem]:
        return history.list_history(
            self.ctx.db,
            user_id=None if self.everyone else self.user.id,
            endpoint_id=self.endpoint_id if self.only_this else None,
            status_class=None if self.status == "all" else self.status,
        )

    def filters(self) -> None:
        with ui.row().classes("w-full items-center gap-3"):
            if self.user.role == "admin":
                ui.switch(t("history.everyone", self.lang), value=self.everyone,
                          on_change=lambda e: self.set("everyone", e.value))
            ui.checkbox(t("history.only_this", self.lang), value=self.only_this,
                        on_change=lambda e: self.set("only_this", e.value))
            labels = {"all": "history.all", "timeout": "history.timeout", "connection_error": "history.connection"}
            ui.select({s: t(labels[s], self.lang) if s in labels else s for s in STATUS_FILTERS}, value=self.status,
                      label=t("history.status_filter", self.lang), on_change=lambda e: self.set("status", e.value)) \
                .props("dense").classes("w-44")
            ui.space()
            ui.button(t("response.download_csv", self.lang), icon="download", on_click=self.download_csv) \
                .props("flat dense no-caps")

    def set(self, name: str, value) -> None:
        setattr(self, name, value)
        self.listing.refresh()

    @ui.refreshable_method
    def listing(self) -> None:
        items = self.items()
        if not items:
            ui.label(t("history.empty", self.lang)).classes("gx-muted")
            return
        self.by_id = {item.id: item for item in items}
        columns = [{"name": key, "label": t(f"history.col_{key}", self.lang), "field": key, "align": "left"}
                   for key in ("time", "user", "endpoint", "params", "result", "elapsed")]
        columns.append({"name": "actions", "label": "", "field": "id"})
        rows = [{
            "id": item.id,
            "time": show_time(item.at),
            "user": item.user_name + (f" {t('common.deleted_user', self.lang)}" if item.user_deleted else ""),
            "endpoint": item.endpoint_key,
            "params": json.dumps(item.values, ensure_ascii=False, default=str)[:120],
            "result": str(item.status) if item.status is not None else t(
                "history.timeout" if item.outcome == "timeout" else "history.connection", self.lang),
            "elapsed": f"{item.elapsed_ms} ms",
            "class": theme.status_class(item.status, item.outcome),
        } for item in items]
        table = ui.table(rows=rows, columns=columns, row_key="id", pagination={"rowsPerPage": 10}) \
            .props(':rows-per-page-options="[10, 25, 50]" flat dense').classes("w-full")
        colors = json.dumps({k: v for k, v in theme.STATUS_COLORS.items()}).replace('"', "'")
        table.add_slot("body-cell-result", f"""
            <q-td :props="props"><span class="gx-pill"
              :style="{{background: {colors}[props.row.class][0], color: {colors}[props.row.class][1]}}">{{{{ props.value }}}}</span></q-td>""")
        table.add_slot("body-cell-actions", f"""
            <q-td :props="props" class="text-right">
              <q-btn flat dense no-caps size="sm" label="{t('history.open_response', self.lang)}"
                     @click="() => $parent.$emit('open', props.row.id)" />
              <q-btn flat dense no-caps size="sm" color="primary" label="{t('history.rerun', self.lang)}"
                     @click="() => $parent.$emit('rerun', props.row.id)" />
            </q-td>""")
        table.on("open", lambda e: self.open_record(self.by_id[e.args]))
        table.on("rerun", lambda e: self.rerun(self.by_id[e.args]))

    def open_record(self, item: HistoryItem) -> None:
        with ui.dialog() as dialog, ui.card().classes("w-full max-w-3xl"):
            with ui.row().classes("w-full items-center"):
                ui.label(f"{item.endpoint_key} · {show_time(item.at)} {t('history.utc', self.lang)}").classes("font-semibold")
                ui.space()
                ui.button(icon="close", on_click=dialog.close).props("flat round dense")
            ui.label(t("history.request", self.lang)).classes("text-xs gx-muted")
            ui.code(json.dumps(item.request, indent=2, ensure_ascii=False), language="json").classes("w-full")
            ui.label(t("history.response", self.lang)).classes("text-xs gx-muted")
            ui.code(item.response_text or "—", language="json").classes("w-full")
            if item.truncated:
                ui.label(t("response.truncated", self.lang)).classes("text-xs").style("color:#92400E")
        dialog.open()

    async def rerun(self, item: HistoryItem) -> None:
        if not can_rerun(item.values):
            ui.notify(t("history.rerun_secret", self.lang), type="warning")
            return
        await self.on_rerun(item)

    def download_csv(self) -> None:
        ui.download.content(history.to_csv(self.items()).encode(), f"{self.file_name}-history.csv", "text/csv")
