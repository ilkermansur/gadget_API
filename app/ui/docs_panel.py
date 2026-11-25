"""Docs görünümü (tasarim-v1 §4.3): endpoint'in dokümanı, formla aynı kaynaktan (birleşmiş görünüm).

Başlık bandı (metot, path, kaynak) → "Ne yapar?" (açıklama, markdown) → parametre tablosu (tip, konum, zorunlu,
formdaki etiket) → onay ve varsayılan görünüm kutuları. Portalda düzeltilmiş değerler "portalda düzenlendi" ile işaretli.
"""
import json

from nicegui import ui

from core.i18n import t
from core.models import EndpointView
from ui import theme

EDITED_COLORS = ("#E0E7FF", "#3730A3")


def render_docs(view: EndpointView, lang: str, source: str) -> None:
    endpoint = view.endpoint
    with ui.column().classes("w-full gap-3"):
        with ui.row().classes("w-full items-center gap-2 p-3").style("background:var(--gx-side);border-radius:8px"):
            theme.pill(endpoint.method, theme.METHOD_COLORS.get(endpoint.method, ("#E1E6ED", "#374151")))
            ui.label(endpoint.path).classes("gx-mono").style("color:#E2E8F0")
            ui.space()
            ui.label(f"{t('docs.source', lang)}: {source}").classes("text-xs").style("color:var(--gx-side-muted)")
            if endpoint.overridden:
                theme.pill(t("docs.edited", lang), EDITED_COLORS)

        description = endpoint.description_en if lang == "en" and endpoint.description_en else endpoint.description
        with ui.column().classes("w-full gap-1 p-3").style("background:#EFF6FF;border-radius:8px;border:1px solid #DBEAFE"):
            ui.label(t("docs.what", lang)).classes("text-xs font-semibold").style("color:#1E40AF")
            ui.markdown(description or (endpoint.summary_en if lang == "en" and endpoint.summary_en else endpoint.summary))

        ui.label(t("docs.params", lang)).classes("font-semibold")
        visible = [f for f in view.fields if not f.hidden]
        if not visible:
            ui.label(t("docs.no_params", lang)).classes("gx-muted")
        else:
            columns = [{"name": key, "label": t(label, lang), "field": key, "align": "left"} for key, label in (
                ("name", "form.key"), ("type", "docs.type"), ("location", "docs.location"),
                ("required", "docs.required"), ("label", "docs.label"), ("help", "docs.what"))]
            rows = [{"name": f.name or "(body)", "type": t(f"kinds.{f.kind}", lang), "location": f.location,
                     "required": t("common.yes" if f.required else "common.no", lang),
                     "label": f.label_for(lang) + (f" ✎" if f.overridden else ""), "help": f.help_for(lang)} for f in visible]
            table = ui.table(rows=rows, columns=columns, row_key="name").props("flat dense").classes("w-full")
            colors = json.dumps(theme.LOCATION_COLORS).replace('"', "'")   # tek tırnak: HTML niteliğini bozmasın; renkler theme.py'de
            table.add_slot("body-cell-location", f"""
                <q-td :props="props"><span class="gx-pill"
                  :style="{{background: ({colors}[props.value] || ['#E1E6ED'])[0],
                            color: ({colors}[props.value] || [0, '#374151'])[1]}}">{{{{ props.value }}}}</span></q-td>""")

        with ui.row().classes("w-full gap-3"):
            info_box(t("docs.confirm", lang), t("docs.confirm_yes" if endpoint.confirm else "docs.confirm_no", lang))
            view_name = {"auto": "—", "table": t("response.view_table", lang), "json": t("response.view_json", lang),
                         "raw": t("response.view_raw", lang)}.get(endpoint.view, endpoint.view)
            info_box(t("docs.default_view", lang), view_name)


def info_box(title: str, text: str) -> None:
    with ui.column().classes("gap-0 p-3").style("flex:1 1 200px;border:1px solid var(--gx-border);border-radius:8px"):
        ui.label(title).classes("text-xs gx-muted")
        ui.label(text).classes("text-sm font-semibold")
