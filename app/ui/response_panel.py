"""Yanıt alanı (tasarim-v1 §5.1): bir çalıştırmanın sonucunu gösterir.

Görünümler: Özet (sadece hatada) | Tablo | JSON | Serbest (ham HTTP, düz metin).
Tablo iki biçimde kurulur: yanıtta liste varsa her kayıt bir satır; tek bir kayıt (nesne) döndüyse her alan bir
satır (Alan | Değer, iç içe değerler kısa JSON). Varsayılan: endpoint'in görünümü (x-gadget.view ya da portal
katmanı); "auto" ise Tablo; hata → Özet. Tabloda boşlukla ayrılan kelimelerin KESİŞİMİ aranır, eşleşen hücreler sarı; sayfa başına 5/10/25/50.

Önce saf kurallar (ekransız test edilir), sonra ResponsePanel (çizim).
"""
import csv
import io
import json
import re

from nicegui import ui

from core.caller import table_rows
from core.errors import render
from core.i18n import t
from core.models import Endpoint, EndpointView, MenuItem, Result
from ui import theme

HIGHLIGHT = "#FEF08A"          # aramayla eşleşen hücre


# =============================================================================
# Saf kurallar
# =============================================================================

def cell_text(value: object) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else str(value)


def as_row(row: object) -> dict:
    return row if isinstance(row, dict) else {"değer": row}


def columns_of(rows: list) -> list[str]:
    """Sütunlar: satırlardaki anahtarların birleşimi, ilk görüldükleri sırayla."""
    names = []
    for row in rows:
        for key in as_row(row):
            if key not in names:
                names.append(key)
    return names


def search_words(query: str) -> list[str]:
    return [w for w in query.casefold().split() if w]


def filter_rows(rows: list, query: str) -> list:
    """Bütün kelimeleri (herhangi bir hücrede) içeren satırlar. Boş arama = hepsi."""
    words = search_words(query)
    if not words:
        return rows
    def matches(row):
        text = " ".join(cell_text(v) for v in as_row(row).values()).casefold()
        return all(word in text for word in words)
    return [row for row in rows if matches(row)]


def search_hits(row: object, query: str) -> list[str]:
    """Bu satırda aranan kelimelerden birini içeren hücrelerin sütun adları (sarı vurgu için)."""
    words = search_words(query)
    return [key for key, value in as_row(row).items() if words and any(w in cell_text(value).casefold() for w in words)]


def rows_to_csv(rows: list, header: bool = True) -> str:
    """UTF-8 BOM + ";" ayırıcı (Excel Türkçe karakterleri doğru açar); iç içe değerler JSON metni.

    header=False: sütun adı satırı yazılmaz. Tek kayıtlık (Alan | Değer) tabloda alan adları zaten ilk sütunda
    olduğu için başlık satırı gereksizdir; dosya doğrudan "id;EP-1040" ile başlar.
    """
    columns = columns_of(rows)
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\n")
    if header:
        writer.writerow(columns)
    for row in rows:
        row = as_row(row)
        writer.writerow([cell_text(row.get(c)) for c in columns])
    return "﻿" + out.getvalue()


def record_rows(body: object, field_label: str, value_label: str) -> list[dict] | None:
    """Tek kayıtlık yanıt → iki sütunlu tablo satırları: her alan bir satır. Nesne değilse ya da boşsa None."""
    if not isinstance(body, dict) or not body:
        return None
    return [{field_label: key, value_label: value} for key, value in body.items()]


def default_view(endpoint: Endpoint, result: Result, rows: list | None) -> str:
    if result.outcome != "ok":
        return "summary"
    if endpoint.view == "raw" or isinstance(result.body, str):
        return "raw"
    if endpoint.view == "json":
        return "json"
    return "table" if rows is not None else "json"


def error_text_key(result: Result) -> str:
    """Hata özetinin başlık metni (locales anahtarı)."""
    if result.error and result.error.code == "token_error":
        return "response.err_token"
    if result.outcome == "timeout":
        return "response.err_timeout"
    if result.outcome == "connection_error":
        return "response.err_connection"
    return {422: "response.err_validation", 401: "response.err_401", 403: "response.err_403",
            404: "response.err_404", 500: "response.err_500"}.get(result.status, "response.err_other")


def raw_http(result: Result) -> str:
    headers = "\n".join(f"{k}: {v}" for k, v in result.headers.items())
    return f"HTTP {result.status}\n{headers}\n\n{result.text}"


def export_name(view: EndpointView, values: dict, max_length: int = 120) -> str:
    """İndirilen dosyanın adı, gönderilen sorgudan: path değişkenleri gerçek değerleriyle, sonra doldurulmuş
    sorgu parametreleri. "/devices/{device_id}" + {"device_id": "EP-1040"} → "devices_EP-1040";
    "/devices" + {"site": "Ankara DC", "limit": 3} → "devices_site-Ankara-DC_limit-3". Uzantı eklenmez.
    """
    path = view.endpoint.path
    for field in view.fields:
        if field.location == "path" and values.get(field.name) not in (None, ""):
            path = path.replace("{" + field.name + "}", str(values[field.name]))
    path = re.sub(r"\{(\w+)\}", r"\1", path)                   # değeri olmayan değişken: süslü parantezsiz adı
    parts = [part for part in path.strip("/").split("/") if part]
    parts += [f"{field.name}-{values[field.name]}" for field in view.fields
              if field.location == "query" and values.get(field.name) not in (None, "", [], {})]
    name = "_".join(parts) or "yanit"
    name = re.sub(r"[^\w.-]+", "-", name).strip("-_.")          # dosya adında sorun çıkaran karakterler → "-"
    return name[:max_length] or "yanit"


def format_size(size: int) -> str:
    return f"{size} B" if size < 1024 else f"{size / 1024:.1f} KB" if size < 1024 ** 2 else f"{size / 1024 ** 2:.1f} MB"


# =============================================================================
# Çizim
# =============================================================================

class ResponsePanel:
    def __init__(self, lang: str, item: MenuItem, result: Result, *, on_back_to_form, on_retry, file_name: str,
                 on_edit_row=None):
        self.lang, self.item, self.result = lang, item, result
        self.on_back_to_form, self.on_retry, self.file_name = on_back_to_form, on_retry, file_name
        self.rows = table_rows(result.body, item.view.endpoint.list_path) if result.outcome == "ok" else None
        # Liste yoksa ve tek bir kayıt döndüyse: Alan | Değer tablosu. Satırlar kayıt değil alan olduğu için
        # bu tabloda satır "Düzenle" sütunu olmaz.
        is_record = self.rows is None and result.outcome == "ok"
        self.is_record = False
        if is_record:
            self.rows = record_rows(result.body, t("response.col_field", lang), t("response.col_value", lang))
            self.is_record = self.rows is not None
        self.on_edit_row = None if is_record else on_edit_row
        if self.rows and any(not isinstance(row, dict) for row in self.rows):    # düz değer listesi: tek sütun
            self.rows = [row if isinstance(row, dict) else {t("response.col_value", lang): row} for row in self.rows]   # liste endpoint'inde satır "Düzenle"
        self.view = default_view(item.view.endpoint, result, self.rows)
        self.query = ""
        with ui.column().classes("w-full gap-3"):
            self.header()
            self.body()

    def available_views(self) -> list[str]:
        views = ["summary"] if self.result.outcome != "ok" else []
        if self.rows is not None:
            views.append("table")
        if self.result.body not in (None, "") and not isinstance(self.result.body, str):
            views.append("json")
        return views + ["raw"]

    @ui.refreshable_method
    def header(self) -> None:
        result = self.result
        with ui.row().classes("w-full items-center gap-3"):
            theme.pill(str(result.status or t(error_text_key(result), self.lang).split(":")[0]),
                       theme.STATUS_COLORS[theme.status_class(result.status, result.outcome)])
            ui.label(f"{result.elapsed_ms} ms · {format_size(result.size)}").classes("gx-muted text-sm")
            ui.space()
            for view in self.available_views():
                ui.button(t(f"response.view_{view}", self.lang), on_click=lambda v=view: self.set_view(v)) \
                    .props("no-caps dense unelevated" + ("" if view == self.view else " flat color=grey-8")).classes("px-3")
            ui.button(icon="content_copy", on_click=self.copy).props(f'flat dense aria-label="{t("common.copy", self.lang)}"')
            if self.rows:
                ui.button(t("response.download_csv", self.lang), icon="download", on_click=self.download_csv) \
                    .props("flat dense no-caps")

    def set_view(self, view: str) -> None:
        self.view = view
        self.header.refresh()
        self.body.refresh()

    @ui.refreshable_method
    def body(self) -> None:
        if self.view == "summary":
            self.summary()
        elif self.view == "table":
            ui.input(t("response.search_rows", self.lang), placeholder=t("response.search_placeholder", self.lang),
                     value=self.query, on_change=self.on_search).props("dense outlined clearable").classes("w-80")
            self.table()
        elif self.view == "json":
            ui.code(json.dumps(self.result.body, indent=2, ensure_ascii=False), language="json").classes("w-full")
        else:
            # Düz metin: "http" renklendirmesi tanıyamadığı satırları hata sayıp kırmızı çerçeveye alıyordu.
            ui.code(raw_http(self.result), language="text").classes("w-full")

    def on_search(self, event) -> None:
        self.query = event.value or ""
        self.table.refresh()

    @ui.refreshable_method
    def table(self) -> None:
        rows = filter_rows(self.rows, self.query)
        if not rows:
            ui.label(t("response.no_rows" if self.query else "response.empty", self.lang)).classes("gx-muted")
            return
        columns = [{"name": c, "label": c, "field": c, "align": "left", "sortable": True} for c in columns_of(self.rows)]
        if self.on_edit_row:
            columns.append({"name": "__edit", "label": "", "field": "__i"})
        display = [{**{k: cell_text(v) for k, v in as_row(row).items()}, "__i": i, "__hits": search_hits(row, self.query)}
                   for i, row in enumerate(rows)]
        table = ui.table(rows=display, columns=columns, row_key="__i", pagination={"rowsPerPage": 10}) \
            .props(f'flat dense :rows-per-page-options="[5, 10, 25, 50]" rows-per-page-label="{t("response.per_page", self.lang)}"') \
            .classes("w-full")
        table.add_slot("body-cell", f"""
            <q-td :props="props" :style="props.row.__hits.includes(props.col.name) ? 'background:{HIGHLIGHT}' : ''">
              {{{{ props.value }}}}
            </q-td>""")
        if self.on_edit_row:
            table.add_slot("body-cell-__edit", f"""
                <q-td :props="props" class="text-right">
                  <q-btn flat dense no-caps size="sm" icon="edit" label="{t('response.edit_row', self.lang)}"
                         @click="() => $parent.$emit('edit', props.row.__i)" />
                </q-td>""")
            table.on("edit", lambda e: self.on_edit_row(rows[e.args]))

    def summary(self) -> None:
        """Hata özeti: ne oldu, neden olabilir, ne yapılabilir."""
        result, error = self.result, self.result.error
        with ui.column().classes("w-full gap-2 p-3").style("border-radius:8px;background:#FEF2F2;border:1px solid #FECACA"):
            ui.label(t(error_text_key(result), self.lang, status=result.status)).classes("font-semibold").style("color:#991B1B")
            message = render(error.msg.key, error.msg.values, self.lang) if error and error.msg else (error.message if error else "")
            if message:
                ui.label(message).classes("text-sm")
            if error and error.fields:
                ui.label(t("response.fields_with_errors", self.lang)).classes("text-xs font-semibold mt-1")
                for field_error in error.fields:
                    ui.label(f"• {field_error.field or t('common.body', self.lang)}: {field_error.message}").classes("text-sm gx-mono")
            if error and error.request_id:
                with ui.row().classes("items-center gap-2"):
                    ui.label(f"{t('response.request_id', self.lang)}: {error.request_id}").classes("text-sm gx-mono")
                    ui.button(icon="content_copy", on_click=lambda: self.copy_text(error.request_id)).props("flat dense round")
            with ui.row().classes("gap-2"):
                if result.status == 422:
                    ui.button(t("response.back_to_form", self.lang), on_click=self.on_back_to_form).props("outline no-caps")
                if result.outcome in ("timeout", "connection_error") or (result.status or 0) >= 500:
                    ui.button(t("response.retry", self.lang), on_click=self.on_retry).props("outline no-caps")

    # --- kopyala / indir ---------------------------------------------------------------------

    def copy(self) -> None:
        text = raw_http(self.result) if self.view in ("raw", "summary") else (
            self.csv_text() if self.view == "table" else
            json.dumps(self.result.body, indent=2, ensure_ascii=False))
        self.copy_text(text)

    def copy_text(self, text: str) -> None:
        ui.clipboard.write(text)
        ui.notify(t("common.copied", self.lang))

    def csv_text(self) -> str:
        """Görünen (aramaya uyan) satırlar; tek kayıtta başlık satırı yok."""
        return rows_to_csv(filter_rows(self.rows, self.query), header=not self.is_record)

    def download_csv(self) -> None:
        ui.download.content(self.csv_text().encode(), f"{self.file_name}.csv", "text/csv")
