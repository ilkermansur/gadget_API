"""Ana ekranın yerleşimi (tasarim-v1 §3): sol menü + üst bar + işlev bandı + içerik alanları.

    ┌──────────┬──────────────────────────────────────────────────────────┐
    │ sidebar  │ topbar   : Resource / Grup / Endpoint · profil · TR|EN · kişi │
    │ (menü    │ band     : func1 = resource seçici + API durumu · func2… devre dışı │
    │  ya da   │ content  : bilgi şeridi + Form · Yanıt · Yönetim (aç/kapa)     │
    │  ikon    │                                                                │
    │  rayı)   │                                                                │
    └──────────┴──────────────────────────────────────────────────────────┘

Dört bölge ayrı ayrı yeniden çizilir (@ui.refreshable_method): menüde arama yapınca formda yazılanlar kaybolmaz.
Sayfanın durumu MainPage nesnesindedir (her tarayıcı sekmesine bir tane); kalıcı olması istenen birkaç seçim
(menü açık mı, son resource, son endpoint) app.storage.user'a yazılır, sayfa yenilense de kalır.
Form, Yanıt ve Yönetim alanlarının içi kendi dosyalarındadır (form_panel, response_panel, admin/).
"""
import time

from nicegui import app, ui

from core import branding, catalog, editing, history, resources
from core.caller import send_with_auth
from core.i18n import LANGS, t, upper
from core.models import Group, MenuItem, Request, Resource, User
from core.openapi import UNTAGGED
from core.users import visible_profiles
from ui import theme
from ui.admin.area import admin_area
from ui.confirm_dialog import confirm_run, needs_confirmation
from ui.custom_editor import CustomEditor
from ui.definition import DefinitionPanel
from ui.edit_drawer import open_edit
from ui.form_panel import FormPanel
from ui.history_panel import HistoryPanel
from ui.response_panel import ResponsePanel, export_name
from ui.state import AppContext, Session, install_activity_watch, require_user

FUNC_SLOTS = ["func1", "func2", "func3", "func4"]
# Görevi olan yuvalar ve ekrandaki adları. Görevsiz yuvalar (func2–4) kodda durur ama çizilmez;
# yeni bir görev (ör. ön ayarlar) eklenince buraya "func2": "<görev>" yazılır ve band() onu da çizer.
SLOT_TASKS = {"func1": "source"}                      # func1 = kaynak (resource) seçici
API_CHECK_EVERY = 5 * 60     # sn: API durumu kendiliğinden bu sıklıkla ölçülür (kimse istek atmasa da)
API_CHECK_TICK = 30          # sn: "vakit geldi mi?" bu sıklıkla sorulur


def api_check_due(last_checked: float | None, now: float, every: float = API_CHECK_EVERY) -> bool:
    """API durumunu ölçme vakti geldi mi? Hiç ölçülmediyse evet. Yenile düğmesi last_checked'i şimdiye çektiği
    için sonraki kendiliğinden ölçüm 5 dakika sonraya kayar."""
    return last_checked is None or now - last_checked >= every


def mount(ctx: AppContext) -> None:
    @ui.page("/")
    def main_page() -> None:
        user = require_user(ctx)
        if user is None:
            return
        theme.apply_theme(branding.get_branding(ctx.db).palette)
        install_activity_watch()
        MainPage(ctx, user)


class MainPage:
    def __init__(self, ctx: AppContext, user: User):
        self.ctx, self.user, self.lang = ctx, user, Session.lang()
        self.brand = branding.get_branding(ctx.db)
        store = app.storage.user
        self.side_open: bool = store.get("ui.side_open", True)
        self.profile = "operator"                       # görünen profil sekmesi
        self.sections = {"form": True, "response": True, "definition": False, "admin": False}
        self.search = ""
        self.closed_groups: set[str] = set()
        self.api_status: tuple[str, int] | None = None  # ("online", ms) | ("offline", 0) | None (bilinmiyor)
        self.api_checked_at: float | None = None        # son ölçümün zamanı (time.monotonic)
        self.api_checking = False
        self.section_parts: dict = {}                   # alan anahtarı → (içerik kutusu, aç/kapa düğmesi)

        self.resources = self.available_resources()
        ids = [r.id for r in self.resources]
        self.resource_id = store.get("ui.resource_id") if store.get("ui.resource_id") in ids else (ids[0] if ids else None)
        self.load_menu()
        last = store.get("ui.endpoint_id")
        self.endpoint_id = last if any(m.endpoint_id == last for m in self.menu) else None
        self.closed_groups = self.default_closed_groups()

        with ui.row().classes("w-full min-h-screen no-wrap items-stretch gap-0"):
            self.sidebar()
            with ui.column().classes("flex-1 min-w-0 gap-0"):
                self.topbar()
                self.band()
                self.content()
        ui.timer(0.1, self.check_api, once=True)
        ui.timer(API_CHECK_TICK, self.check_api_if_due)   # sunucu tarafında; kullanıcı hareketi sayılmaz

    # =========================================================================
    # Veri: resource'lar, menü, gruplar
    # =========================================================================

    def available_resources(self) -> list[Resource]:
        """func1'de görünenler: admin hepsini; diğerleri menüsünde en az bir endpoint olanları görür."""
        all_resources = resources.list_resources(self.ctx.db, self.ctx.box)
        if self.user.role == "admin":
            return all_resources
        return [r for r in all_resources if catalog.menu_for(self.ctx.db, self.user, r.id)]

    def load_menu(self) -> None:
        self.menu: list[MenuItem] = catalog.menu_for(self.ctx.db, self.user, self.resource_id) if self.resource_id else []
        doc = catalog.load_doc(self.ctx.db, self.resource_id) if self.resource_id else None
        known = {g.name: g for g in doc.groups} if doc else {}
        names = []
        for name in [g.name for g in doc.groups] if doc else []:          # şemadaki sıra
            if any(m.view.endpoint.group == name for m in self.menu):
                names.append(name)
        for item in self.menu:                                              # portalda yeni gruba taşınanlar
            if item.view.endpoint.group not in names:
                names.append(item.view.endpoint.group)
        self.groups: list[Group] = [known.get(name, Group(name=name)) for name in names]

    def default_closed_groups(self) -> set[str]:
        """Menü grupları kapalı başlar; sadece seçili uç noktanın grubu açık (seçili olan görünsün)."""
        item = self.current_item()
        return {g.name for g in self.groups} - ({item.view.endpoint.group} if item else set())

    def current_item(self) -> MenuItem | None:
        return next((m for m in self.menu if m.endpoint_id == self.endpoint_id), None)

    def current_resource(self) -> Resource | None:
        return next((r for r in self.resources if r.id == self.resource_id), None)

    def group_label(self, group: Group) -> str:
        if group.name == UNTAGGED:
            return t("common.untagged_group", self.lang)
        if self.lang == "en" and group.name_en:
            return group.name_en
        return group.description or group.name

    def endpoint_name(self, item: MenuItem) -> str:
        endpoint = item.view.endpoint
        if self.lang == "en" and endpoint.summary_en:
            return endpoint.summary_en
        return endpoint.summary or endpoint.path

    def visible_items(self, group: Group) -> list[MenuItem]:
        needle = self.search.casefold().strip()
        return [m for m in self.menu if m.view.endpoint.group == group.name and
                (not needle or needle in self.endpoint_name(m).casefold() or needle in m.view.endpoint.path.casefold())]

    # =========================================================================
    # Sol menü
    # =========================================================================

    @ui.refreshable_method
    def sidebar(self) -> None:
        if self.side_open:
            self.sidebar_open()
        else:
            self.sidebar_rail()

    def sidebar_open(self) -> None:
        with ui.column().classes("gx-side gap-3 px-3 py-4").style("flex:0 0 300px") \
                .props(f'role=navigation aria-label="{t("layout.menu", self.lang)}"'):
            with ui.row().classes("w-full items-center no-wrap gap-2 px-1"):
                # Beyaz fötr şapka. sanitize=False: içerik kodun kendi sabiti (dışarıdan gelmiyor); temizleyici SVG
                # maskesini siliyordu.
                ui.html(theme.BRAND_ICON_SVG, sanitize=False).style("width:34px;height:34px;display:flex")
                ui.label(self.brand.name).classes("flex-1 font-bold text-base").style("color:#F8FAFC")
                ui.button(icon="keyboard_double_arrow_left", on_click=lambda: self.set_side(False)) \
                    .props(f'flat dense color=grey-4 aria-label="{t("layout.collapse_menu", self.lang)}"')
            ui.input(placeholder=t("menu.search_placeholder", self.lang), value=self.search,
                     on_change=self.on_search).props(f'dense dark borderless clearable aria-label="{t("menu.search", self.lang)}"') \
                .classes("w-full px-2").style("border-radius:8px;background:var(--gx-side2);border:1px solid var(--gx-side3)")
            self.menu_list()

    @ui.refreshable_method
    def menu_list(self) -> None:
        if not self.resources:
            ui.label(t("menu.no_resources", self.lang)).classes("text-xs px-2").style("color:var(--gx-side-muted)")
            return
        if not self.menu:
            ui.label(t("menu.empty", self.lang)).classes("text-xs px-2").style("color:var(--gx-side-muted)")
            return
        shown = 0
        for index, group in enumerate(self.groups):
            items = self.visible_items(group)
            if not items:
                continue
            shown += len(items)
            is_open = group.name not in self.closed_groups or bool(self.search)
            background, foreground = theme.group_tint(index)
            with ui.column().classes("w-full gap-0.5"):
                with ui.button(on_click=lambda g=group.name: self.toggle_group(g)).props("flat no-caps align=left color=blue-grey-3") \
                        .classes("w-full px-1.5").style("font-size:11px;letter-spacing:.08em"):
                    with ui.row().classes("w-full items-center no-wrap gap-2"):
                        with ui.element("div").classes("flex items-center justify-center").style(
                                f"width:24px;height:24px;border-radius:6px;background:{background}"):
                            ui.icon(theme.group_icon(group.icon), size="15px").style(f"color:{foreground}")
                        ui.label(upper(self.group_label(group), self.lang)).classes("flex-1 text-left font-semibold")
                        ui.icon("expand_more" if is_open else "chevron_right", size="16px")
                        ui.label(str(len(items)))
                if is_open:
                    for item in items:
                        self.menu_row(item)
        if shown == 0:
            ui.label(t("menu.no_match", self.lang)).classes("text-xs px-2").style("color:var(--gx-side-muted)")

    def menu_row(self, item: MenuItem) -> None:
        selected = item.endpoint_id == self.endpoint_id
        method = item.view.endpoint.method
        background, foreground = theme.METHOD_COLORS.get(method, ("#E1E6ED", "#374151"))
        with ui.button(on_click=lambda i=item.endpoint_id: self.select_endpoint(i)).props("flat no-caps align=left color=" + ("white" if selected else "blue-grey-1")) \
                .classes("w-full px-2").style(
                    "min-height:38px;border-radius:6px;" +
                    ("background:var(--gx-acc2)" if selected else "")):
            with ui.row().classes("w-full items-center no-wrap gap-2.5"):
                ui.label(method).classes("gx-mono text-[11px] font-semibold text-center").style(
                    f"min-width:56px;padding:1px 0;line-height:18px;border-radius:4px;background:{background};color:{foreground}")
                ui.label(self.endpoint_name(item)).classes("flex-1 text-left text-[13.5px]")

    def sidebar_rail(self) -> None:
        with ui.column().classes("gx-side items-center gap-2 py-4").style("flex:0 0 68px") \
                .props(f'role=navigation aria-label="{t("layout.menu", self.lang)}"'):
            ui.button(icon="keyboard_double_arrow_right", on_click=lambda: self.set_side(True)) \
                .props(f'flat color=grey-4 aria-label="{t("layout.expand_menu", self.lang)}"')
            resource = self.current_resource()
            if resource:
                ui.label(upper(theme.resource_label(resource, self.lang)[:3], self.lang)).classes("gx-mono text-[10px] font-bold") \
                    .style("color:var(--gx-side-muted)").tooltip(theme.resource_label(resource, self.lang))
            for index, group in enumerate(self.groups):
                background, foreground = theme.group_tint(index)
                count = len([m for m in self.menu if m.view.endpoint.group == group.name])
                with ui.button(on_click=lambda g=group.name: self.open_group_from_rail(g)).props(
                        f'flat aria-label="{self.group_label(group)}"').style(
                        f"width:48px;height:48px;border-radius:10px;background:{background}"):
                    ui.icon(theme.group_icon(group.icon), size="20px").style(f"color:{foreground}")
                    ui.badge(str(count)).props("floating").style("background:var(--gx-side3)")
                    ui.tooltip(self.group_label(group))
            ui.space()
            ui.button(icon="logout", on_click=self.sign_out).props(
                f'flat color=grey-4 aria-label="{t("layout.sign_out", self.lang)}"').tooltip(t("layout.sign_out", self.lang))

    # =========================================================================
    # Üst bar
    # =========================================================================

    @ui.refreshable_method
    def topbar(self) -> None:
        item = self.current_item()
        with ui.row().classes("w-full items-center gap-3 px-5 py-3").style(
                "background:var(--gx-card);border-bottom:1px solid var(--gx-border)"):
            with ui.row().classes("items-baseline gap-2 min-w-0").style("flex:1 1 260px"):
                resource = self.current_resource()
                crumbs = [theme.resource_label(resource, self.lang)] if resource else []
                if item:
                    group = next((g for g in self.groups if g.name == item.view.endpoint.group), Group(name=""))
                    crumbs.append(self.group_label(group))
                ui.label(" / ".join(crumbs) + (" /" if crumbs and item else "")).classes("gx-muted text-[15px]")
                if item:
                    ui.label(self.endpoint_name(item)).classes("font-bold text-lg")
            profiles = visible_profiles(self.user.role)
            if len(profiles) > 1:
                self.toggle_group_buttons(profiles, self.profile, lambda p: t(f"roles.{p}", self.lang),
                                          self.set_profile, t("layout.profile", self.lang))
            self.toggle_group_buttons(LANGS, self.lang, str.upper, self.set_lang, t("common.lang_label", self.lang))
            with ui.row().classes("items-center gap-2 pl-3").style("border-left:1px solid var(--gx-border)"):
                initials = "".join(part[0] for part in self.user.name.split()[:2]).upper()
                ui.label(initials).classes("flex items-center justify-center text-xs font-semibold").style(
                    "width:32px;height:32px;border-radius:50%;background:#334155;color:#F1F5F9")
                with ui.column().classes("gap-0 leading-tight"):
                    ui.label(self.user.name).classes("text-[13px] font-semibold")
                    ui.label(t(f"roles.{self.user.role}", self.lang)).classes("gx-muted text-[11.5px]")
                # Çerçevesiz; fareyle üstüne gelince Quasar'ın hover zemini belirginleştirir.
                ui.button(t("layout.sign_out", self.lang), icon="logout", on_click=self.sign_out) \
                    .props("flat dense no-caps color=grey-8").classes("px-2")

    @staticmethod
    def toggle_group_buttons(options, current, label, on_pick, aria_label: str) -> None:
        with ui.row().classes("gap-0.5 p-0.5").style(
                "border-radius:7px;background:#E1E6ED;border:1px solid var(--gx-border)") \
                .props(f'role=group aria-label="{aria_label}"'):
            for option in options:
                ui.button(label(option), on_click=lambda o=option: on_pick(o)) \
                    .props("unelevated no-caps dense" + ("" if option == current else " flat color=grey-8")) \
                    .classes("gx-seg")

    # =========================================================================
    # İşlev bandı
    # =========================================================================

    @ui.refreshable_method
    def band(self) -> None:
        with ui.row().classes("w-full items-center gap-2.5 px-5 py-2").style(
                "min-height:56px;background:#E3E8EE;border-bottom:1px solid var(--gx-border)") \
                .props(f'role=toolbar aria-label="{t("layout.function_band", self.lang)}"'):
            for slot in FUNC_SLOTS:
                task = SLOT_TASKS.get(slot)
                if task is None:
                    continue                                    # görevsiz yuva: gizli
                with ui.row().classes("items-center gap-2 px-2.5 no-wrap").style(
                        "height:40px;border-radius:8px;border:1px solid #BFC8D4;background:var(--gx-card)") \
                        .props(f'data-slot="{slot}"'):
                    ui.label(t(f"layout.slot_{task}", self.lang)).classes("text-[11px] font-semibold").style("color:#64748B")
                    if task == "source":
                        self.resource_selector()

    def resource_selector(self) -> None:
        if not self.resources:
            ui.label(t("menu.no_resources", self.lang)).classes("gx-muted text-[12.5px]")
            return
        # Etiket yok: yuva zaten "func1" yazıyor, kayan etiket kutuyu yuvadan taşırıyordu. Anlamı aria-label'da.
        resource_label = t("layout.resource", self.lang)
        ui.select({r.id: theme.resource_label(r, self.lang) for r in self.resources}, value=self.resource_id,
                  on_change=lambda e: self.set_resource(e.value)) \
            .props(f'dense options-dense borderless hide-bottom-space aria-label="{resource_label}"') \
            .classes("gx-band-select min-w-[160px]").tooltip(resource_label)
        if self.api_status is not None:
            state, ms = self.api_status
            online = state == "online"
            background, foreground = theme.STATUS_COLORS["2xx" if online else "5xx"]
            text = t("layout.api_online", self.lang, ms=ms) if online else t("layout.api_offline", self.lang)
            with ui.row().classes("items-center gap-1.5 px-2 py-0.5 text-xs font-semibold no-wrap").style(
                    f"border-radius:999px;background:{background};color:{foreground}").props("role=status"):
                ui.element("span").style(f"width:8px;height:8px;border-radius:50%;background:{foreground}")
                ui.label(text)
            ui.tooltip(t("layout.api_check_note", self.lang))
        resource = self.current_resource()
        if resource is not None and resource.kind != "custom":
            label = t("layout.api_check_now", self.lang)
            ui.button(icon="sync", on_click=self.check_api) \
                .props(f'flat round dense size=sm color=grey-8 aria-label="{label}"' + (" loading" if self.api_checking else "")) \
                .tooltip(label)

    async def check_api_if_due(self) -> None:
        if api_check_due(self.api_checked_at, time.monotonic()):
            await self.check_api()

    async def check_api(self) -> None:
        """Seçili resource'un ulaşılabilir olup olmadığı: şema adresine kendi kimliğiyle kısa bir istek.

        Sayfa açılınca, resource değişince, yenile düğmesiyle ve 5 dakikada bir (check_api_if_due) çalışır.
        """
        if self.api_checking:
            return
        resource = self.current_resource()
        if resource is None or resource.kind == "custom":
            self.api_status, self.api_checked_at = None, time.monotonic()
            self.band.refresh()
            return
        self.api_checking = True
        self.band.refresh()
        try:
            result = await send_with_auth(Request("GET", resource.openapi_url), resource, self.ctx.tokens)
            self.api_status = ("online", result.elapsed_ms) if result.outcome == "ok" else ("offline", 0)
        finally:
            self.api_checking, self.api_checked_at = False, time.monotonic()
        self.band.refresh()

    # =========================================================================
    # İçerik: bilgi şeridi + açılır/kapanır alanlar
    # =========================================================================

    @ui.refreshable_method
    def content(self) -> None:
        item = self.current_item()
        self.result, self.result_values, self.running = None, {}, False
        if item:
            self.restore_last_run(item)
            self.info_strip(item)
        with ui.column().classes("w-full gap-4 p-5"):
            if self.profile in ("developer", "admin"):
                ui.button(t("custom.add", self.lang), icon="add", on_click=lambda: self.open_custom_editor(None)) \
                    .props("outline no-caps dense").classes("self-end")
            if self.profile != "admin" and item is None:
                ui.label(t("form.no_endpoint", self.lang)).classes("gx-muted")
            if item:
                self.section("form", t("form.section", self.lang), lambda: self.form_body(item))
                self.section("response", t("response.section", self.lang), lambda: self.response_section(item))
                if self.profile in ("developer", "admin"):
                    self.section("definition", t("definition.section", self.lang), lambda: self.definition_body(item))
            if self.profile == "admin":
                self.section("admin", t("layout.admin_section", self.lang),
                             lambda: admin_area(self.ctx, self.user, self.lang, on_resources_changed=self.reload_resources))

    def definition_body(self, item: MenuItem) -> None:
        """FastAPI'den gelen: sunum düzeltme (portal katmanı). Custom: tanımın kendisi düzenlenir."""
        if catalog.get_entry(self.ctx.db, item.endpoint_id).custom:
            ui.button(t("custom.edit", self.lang), icon="edit",
                      on_click=lambda: self.open_custom_editor(item.endpoint_id)).props("unelevated no-caps")
        else:
            DefinitionPanel(self.ctx, self.lang, item, on_saved=self.after_definition_saved)

    def open_custom_editor(self, endpoint_id: int | None) -> None:
        custom_resource = next(r for r in resources.list_resources(self.ctx.db, self.ctx.box) if r.kind == "custom")
        CustomEditor(self.ctx, self.lang, custom_resource, on_saved=self.after_custom_saved, endpoint_id=endpoint_id)

    async def after_custom_saved(self, endpoint_id: int | None) -> None:
        """Custom kaydedildi: func1 Custom resource'una geçer ve endpoint seçilir; silindiyse seçim kalkar."""
        self.reload_resources()
        custom_resource = next((r for r in self.resources if r.kind == "custom"), None)
        if custom_resource is not None:
            await self.set_resource(custom_resource.id)
        if endpoint_id is not None and any(m.endpoint_id == endpoint_id for m in self.menu):
            self.select_endpoint(endpoint_id)

    def form_body(self, item: MenuItem) -> None:
        resource = self.current_resource()
        source = (theme.resource_label(resource, self.lang) if resource.kind == "custom" else f"FastAPI · {resource.name}") \
            if resource else ""
        self.form_panel = FormPanel(self.ctx, self.user, self.lang, item, developer=self.profile in ("developer", "admin"),
                                    source=source, on_result=self.show_result, on_running=self.show_running,
                                    initial_values=history.restorable_values(self.result_values))

    def response_section(self, item: MenuItem) -> None:
        """Yanıt | Geçmiş sekmeleri; ikisi de çizili durur, sekme sadece görünürlüğü değiştirir."""
        file_name = export_name(item.view, {})          # geçmiş CSV'si: endpoint'in path'inden ("devices-history.csv")
        with ui.row().classes("gap-2"):
            self.response_tabs = {key: ui.button(t(f"response.tab_{key}", self.lang),
                                                 on_click=lambda k=key: self.set_response_tab(k))
                                  .props("no-caps dense unelevated").classes("px-3")
                                  for key in ("response", "history")}
        with ui.column().classes("w-full") as self.response_box:
            self.response_area()
        with ui.column().classes("w-full") as self.history_box:
            self.history_panel = HistoryPanel(self.ctx, self.user, self.lang, item.endpoint_id,
                                              on_rerun=self.rerun, file_name=file_name)
        self.set_response_tab("response")

    def set_response_tab(self, tab: str) -> None:
        self.response_box.set_visibility(tab == "response")
        self.history_box.set_visibility(tab == "history")
        for key, button in self.response_tabs.items():
            button.props(remove="flat color=grey-8") if key == tab else button.props("flat color=grey-8")
        if tab == "history":
            self.history_panel.listing.refresh()

    async def rerun(self, record) -> None:
        """Geçmişten tekrar çalıştır: o endpoint seçilir, kayıttaki değerlerle çalışır, sonuç Yanıt'ta görünür."""
        if not catalog.can_run(self.ctx.db, self.user, record.endpoint_id):
            ui.notify(t("history.rerun_unavailable", self.lang), type="warning")
            return
        if record.endpoint_id != self.endpoint_id:
            self.select_endpoint(record.endpoint_id)
        view = self.current_item().view
        if needs_confirmation(view):
            preview = catalog.preview_request(self.ctx.db, self.ctx.box, record.endpoint_id, record.values)
            if not await confirm_run(self.lang, view, preview, record.values):
                return
        self.show_running(True)
        result = await catalog.run_endpoint(self.ctx.db, self.ctx.box, self.ctx.tokens, self.user,
                                            record.endpoint_id, record.values)
        self.show_running(False)
        self.show_result(result, record.values)
        ui.notify(t("history.rerun_done", self.lang))

    def show_running(self, running: bool) -> None:
        self.running = running
        self.response_area.refresh()

    def restore_last_run(self, item: MenuItem) -> None:
        """Sayfa yenilendiyse: bu endpoint'in son çalıştırması (geçmişten) yanıt alanına ve forma geri gelir.

        Tarayıcıda sadece kaydın numarası tutulur (app.storage.user); kayıt kişinin kendisine ait değilse ya da
        başka bir endpoint'e aitse yok sayılır.
        """
        record_id = app.storage.user.get("ui.last_runs", {}).get(str(item.endpoint_id))
        record = history.get_own_item(self.ctx.db, record_id, self.user.id) if record_id else None
        if record is not None and record.endpoint_id == item.endpoint_id:
            self.result, self.result_values = history.to_result(record), record.values

    def remember_run(self, result) -> None:
        if result.history_id is not None and self.endpoint_id is not None:
            last_runs = dict(app.storage.user.get("ui.last_runs", {}))
            last_runs[str(self.endpoint_id)] = result.history_id
            app.storage.user["ui.last_runs"] = last_runs

    def show_result(self, result, values: dict) -> None:
        self.remember_run(result)
        self.result, self.result_values = result, values
        self.sections["response"] = True
        self.set_section_visible("response")
        self.set_response_tab("response")
        self.response_area.refresh()

    @ui.refreshable_method
    def response_area(self) -> None:
        item = self.current_item()
        if self.running:
            with ui.column().classes("w-full gap-2"):
                ui.linear_progress(show_value=False).props("indeterminate")
                with ui.row().classes("w-full items-center gap-2"):
                    ui.label(t("response.sending", self.lang)).classes("gx-muted")
                    ui.label(f"{item.view.endpoint.method} {item.view.endpoint.path}").classes("gx-mono text-sm")
                    ui.space()
                    ui.button(t("response.cancel", self.lang), on_click=self.form_panel.cancel).props("outline no-caps")
            return
        if self.result is None:
            ui.label(t("response.empty", self.lang)).classes("gx-muted")
            return
        link = editing.link_for_list(self.ctx.db, self.user, item) if item.view.endpoint.method == "GET" else None
        ResponsePanel(self.lang, item, self.result, on_back_to_form=self.back_to_form, on_retry=self.form_panel.run,
                      file_name=export_name(item.view, self.result_values),   # gönderilen sorgudan
                      on_edit_row=(lambda row: self.edit_row(link, row)) if link else None)

    async def edit_row(self, link, row: dict) -> None:
        record = editing.record_id(row, link.param)
        if record is None:
            ui.notify(t("edit.not_editable", self.lang), type="warning")
            return
        await open_edit(self.ctx, self.user, self.lang, link, record, on_done=self.after_row_edit)

    async def after_row_edit(self, result) -> None:
        """Düzenleme başarılıysa liste yeniden çalıştırılır (tablo güncel kalsın); değilse hata gösterilir."""
        if result.outcome == "ok":
            await self.form_panel.run()
        else:
            self.show_result(result, {})

    def back_to_form(self) -> None:
        self.sections["form"] = True
        self.set_section_visible("form")
        self.form_panel.show_form()

    def info_strip(self, item: MenuItem) -> None:
        endpoint = item.view.endpoint
        entry = catalog.get_entry(self.ctx.db, item.endpoint_id)
        base = catalog.target_resource(self.ctx.db, self.ctx.box, entry).base_url
        background, foreground = theme.METHOD_COLORS.get(endpoint.method, ("#E1E6ED", "#374151"))
        with ui.row().classes("w-full items-center gap-2.5 px-5 py-2.5").style(
                "background:#EFF6FF;border-bottom:1px solid #DBEAFE"):
            ui.label(endpoint.method).classes("gx-mono text-[11px] font-semibold text-center").style(
                f"min-width:56px;padding:1px 0;line-height:18px;border-radius:4px;background:{background};color:{foreground}")
            ui.label(base + endpoint.path).classes("gx-mono text-[13px]").style("color:#1E3A8A")
            description = endpoint.description_en if self.lang == "en" and endpoint.description_en else endpoint.description
            ui.label(description.split("\n")[0]).classes("text-[13px]").style("flex:1 1 240px;color:#374151")

    def section(self, key: str, title: str, body) -> None:
        """Açılır/kapanır alan: başlık solda, aç/kapa oku her zaman en sağda (tasarim-v1 §3.4).

        Kapatmak içeriği silmez, sadece gizler: formda yazılanlar kaybolmaz.
        """
        with ui.column().classes("gx-card w-full gap-0"):
            with ui.row().classes("w-full items-center px-4 py-2 no-wrap"):
                ui.label(title).classes("font-semibold flex-1")
                button = ui.button(on_click=lambda: self.toggle_section(key)).props("flat round dense")
            with ui.column().classes("w-full px-4 pb-4") as box:
                body()
        self.section_parts[key] = (box, button)
        self.set_section_visible(key)

    def set_section_visible(self, key: str) -> None:
        if key not in self.section_parts:
            return
        box, button = self.section_parts[key]
        is_open = self.sections[key]
        box.set_visibility(is_open)
        button.props(f'icon={"expand_less" if is_open else "expand_more"} '
                     f'aria-label="{t("layout.close_section" if is_open else "layout.open_section", self.lang)}"')

    # =========================================================================
    # Olaylar
    # =========================================================================

    def set_side(self, is_open: bool) -> None:
        self.side_open = app.storage.user["ui.side_open"] = is_open
        self.sidebar.refresh()

    def on_search(self, event) -> None:
        self.search = event.value or ""
        self.menu_list.refresh()

    def toggle_group(self, name: str) -> None:
        self.closed_groups ^= {name}
        self.menu_list.refresh()

    def open_group_from_rail(self, name: str) -> None:
        self.closed_groups = {g.name for g in self.groups} - {name}
        self.set_side(True)

    def select_endpoint(self, endpoint_id: int) -> None:
        self.endpoint_id = app.storage.user["ui.endpoint_id"] = endpoint_id
        self.menu_list.refresh()
        self.topbar.refresh()
        self.content.refresh()

    async def set_resource(self, resource_id: int) -> None:
        self.resource_id = app.storage.user["ui.resource_id"] = resource_id
        self.endpoint_id = None
        self.search, self.api_status = "", None
        self.load_menu()
        self.closed_groups = self.default_closed_groups()
        self.sidebar.refresh()
        self.topbar.refresh()
        self.content.refresh()
        await self.check_api()

    def reload_resources(self) -> None:
        """Yönetimde resource ya da seçim değişti: func1 listesi ve menü yenilenir; Yönetim alanına dokunulmaz."""
        self.resources = self.available_resources()
        ids = [r.id for r in self.resources]
        if self.resource_id not in ids:
            self.resource_id, self.endpoint_id = (ids[0] if ids else None), None
        self.load_menu()
        if not any(m.endpoint_id == self.endpoint_id for m in self.menu):
            self.endpoint_id = None
        self.sidebar.refresh()
        self.topbar.refresh()
        self.band.refresh()

    def after_definition_saved(self) -> None:
        """Tanım kaydedildi: menü (ad, grup) ve form yeni haliyle çizilir; Tanım alanı açık kalır."""
        self.load_menu()
        self.select_endpoint(self.endpoint_id)

    def set_profile(self, profile: str) -> None:
        self.profile = profile
        # Admin profiline geçince Form ve Yanıt kapanır, Yönetim açılır (tasarim-v1 §3.4).
        is_admin = profile == "admin"
        self.sections = {"form": not is_admin, "response": not is_admin, "definition": profile == "developer",
                         "admin": is_admin}
        self.topbar.refresh()
        self.content.refresh()

    def set_lang(self, lang: str) -> None:
        Session.set_lang(lang)
        ui.navigate.reload()

    def toggle_section(self, key: str) -> None:
        self.sections[key] = not self.sections[key]
        self.set_section_visible(key)

    def sign_out(self) -> None:
        Session.logout()
        ui.navigate.to("/login")
