"""Yönetim · Branding (tasarim-v1 §9.2): ürün adı, logo ve 9 hazır paletten biri.

Önizleme, giriş ekranının sol panelini taklit eder: seçilen paletin koyu tonu + ad + logo. Ad ve palet
"Kaydet ve uygula" ile kaydedilir ve sayfa yenilenir (palet sayfa açılırken uygulanır, bkz. theme.apply_theme).
Logo seçilince hemen kontrol edilip kaydedilir; kurala uymayan dosya nedeniyle reddedilir, eskisi kalır.
Kurallar ve saklama core/branding.py'de, renkler ui/theme.py'de.
"""
import base64

from nicegui import events, ui

from core.errors import describe
from core import branding
from core.i18n import t
from ui import theme
from ui.state import AppContext


class BrandingAdmin:
    def __init__(self, ctx: AppContext, lang: str):
        self.ctx, self.lang = ctx, lang
        current = branding.get_branding(ctx.db)
        self.name, self.palette, self.logo = current.name, current.palette, current.logo_png

        with ui.row().classes("w-full gap-6 items-start"):
            with ui.column().classes("gap-3").style("flex:1 1 380px"):
                ui.input(t("admin.brand_name", lang), value=self.name, on_change=self.set_name) \
                    .props(f'outlined dense hint="{t("admin.brand_name_help", lang)}"').classes("w-full")
                ui.label(t("admin.logo", lang)).classes("font-semibold mt-2")
                ui.label(t("admin.logo_rules", lang)).classes("gx-muted text-xs")
                with ui.row().classes("items-center gap-2"):
                    ui.upload(on_upload=self.on_upload, auto_upload=True, max_files=1, label=t("admin.logo_pick", lang)) \
                        .props('accept=".png,image/png" flat bordered').classes("w-64").style("max-height:110px")
                    ui.button(t("admin.logo_remove", lang), icon="delete", on_click=self.remove_logo) \
                        .props("flat no-caps color=negative")
                ui.label(t("admin.palette", lang)).classes("font-semibold mt-2")
                ui.label(t("admin.palette_help", lang)).classes("gx-muted text-xs")
                self.palette_grid()
                ui.button(t("admin.brand_save", lang), icon="check", on_click=self.save).props("unelevated no-caps")
            with ui.column().classes("gap-2").style("flex:0 1 360px"):
                ui.label(t("admin.preview", lang)).classes("text-xs font-semibold gx-muted")
                self.preview()

    @ui.refreshable_method
    def palette_grid(self) -> None:
        with ui.grid(columns=3).classes("gap-2"):
            for palette_id, (name_tr, name_en, acc, _, side, side2, _) in theme.PALETTES.items():
                selected = palette_id == self.palette
                with ui.button(on_click=lambda p=palette_id: self.set_palette(p)) \
                        .props("flat no-caps align=left color=grey-9").classes("px-2 py-1").style(
                        "border-radius:8px;border:2px solid " + ("var(--gx-acc)" if selected else "var(--gx-border)")):
                    with ui.row().classes("items-center gap-2 no-wrap"):
                        for color in (side, side2, acc):
                            ui.element("span").style(f"width:14px;height:22px;border-radius:3px;background:{color}")
                        ui.label(name_en if self.lang == "en" else name_tr).classes("text-sm")

    @ui.refreshable_method
    def preview(self) -> None:
        _, _, acc, _, side, side2, _ = theme.PALETTES[self.palette]
        with ui.column().classes("w-full gap-3 p-5").style(f"background:{side};border-radius:12px"):
            ui.label(self.name or "—").classes("text-lg font-bold").style(f"color:{theme.BASE['side-text']}")
            with ui.element("div").classes("w-full flex items-center justify-center").style(
                    f"aspect-ratio:16/9;border-radius:8px;background:{side2}"):
                if self.logo:
                    ui.image("data:image/png;base64," + base64.b64encode(self.logo).decode()) \
                        .props(f'fit=contain alt="{self.name} logo"').classes("w-full h-full")
                else:
                    ui.label(t("admin.logo_none", self.lang)).classes("text-xs").style(
                        f"color:{theme.BASE['side-muted']}")
            ui.element("div").classes("w-full").style(f"height:32px;border-radius:6px;background:{acc}")   # vurgu rengi

    # --- olaylar ---------------------------------------------------------------------------

    def set_name(self, event) -> None:
        self.name = event.value or ""
        self.preview.refresh()

    def set_palette(self, palette_id: str) -> None:
        self.palette = palette_id
        self.palette_grid.refresh()
        self.preview.refresh()

    async def on_upload(self, event: events.UploadEventArguments) -> None:
        data = await event.file.read()
        try:
            branding.set_logo(self.ctx.db, data)
        except ValueError as exc:
            ui.notify(describe(exc, self.lang), type="negative", multi_line=True)
        else:
            self.logo = data
            ui.notify(t("admin.logo_saved", self.lang), type="positive")
            self.preview.refresh()
        event.sender.reset()                       # aynı dosya tekrar seçilebilsin

    def remove_logo(self) -> None:
        branding.remove_logo(self.ctx.db)
        self.logo = None
        ui.notify(t("admin.logo_removed", self.lang))
        self.preview.refresh()

    def save(self) -> None:
        try:
            branding.set_name(self.ctx.db, self.name)
            branding.set_palette(self.ctx.db, self.palette)
        except ValueError as exc:
            ui.notify(describe(exc, self.lang), type="negative")
            return
        ui.notify(t("admin.brand_saved", self.lang), type="positive")
        ui.timer(1.0, ui.navigate.reload, once=True)
