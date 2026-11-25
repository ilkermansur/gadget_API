"""Giriş sayfası (tasarim-v1 §2, mockup "GİRİŞ SAYFASI").

Sol panel: ürün adı + 16:9 logo alanı (Branding). Sağ: TR | EN, kullanıcı adı, şifre, Giriş yap.
Hatalar ayrı mesajla: kullanıcı yok / şifre hatalı / hesap pasif. Oturum zaman aşımıyla düştüyse bir kez söylenir.
"""
import base64

from nicegui import ui

from core import branding, users
from core.i18n import LANGS, t
from ui import theme
from ui.state import AppContext, Session

LOGIN_ERRORS = {"unknown_user": "login.unknown_user", "wrong_password": "login.wrong_password",
                "inactive": "login.inactive"}


def mount(ctx: AppContext) -> None:
    @ui.page("/login")
    def login_page() -> None:
        if Session.current_user(ctx.db) is not None:
            ui.navigate.to("/")
            return
        brand = branding.get_branding(ctx.db)
        theme.apply_theme(brand.palette)
        LoginView(ctx, brand, Session.pop_logout_reason())


class LoginView:
    def __init__(self, ctx: AppContext, brand, logout_reason: str):
        self.ctx, self.brand = ctx, brand
        self.error = t(f"login.{logout_reason}", Session.lang()) if logout_reason else ""
        self.typed_username = ""        # hata sonrası yeniden çizimde kaybolmasın
        self.content()

    @ui.refreshable_method
    def content(self) -> None:
        lang = Session.lang()
        with ui.row().classes("w-full min-h-screen items-center justify-center p-4"):
            with ui.row().classes("w-full max-w-4xl gap-0 overflow-hidden items-stretch").style(
                    "border-radius:14px;border:1px solid var(--gx-border);background:var(--gx-card)"):
                self.brand_panel(lang)
                self.form_panel(lang)

    def brand_panel(self, lang: str) -> None:
        with ui.column().classes("gx-side p-9 gap-5").style("flex:1 1 340px"):
            ui.label(self.brand.name).classes("text-2xl font-bold").style("color:#F8FAFC")
            with ui.element("div").classes("w-full flex items-center justify-center overflow-hidden").style(
                    "aspect-ratio:16/9;border-radius:10px;border:1px dashed var(--gx-side3);background:var(--gx-side2)"):
                if self.brand.logo_png:
                    source = "data:image/png;base64," + base64.b64encode(self.brand.logo_png).decode()
                    ui.image(source).props(f'fit=contain alt="{self.brand.name} logo"').classes("w-full h-full")
                else:
                    ui.label(t("login.logo_empty", lang)).classes("text-xs").style("color:var(--gx-side-muted)")

    def form_panel(self, lang: str) -> None:
        with ui.column().classes("p-9 gap-4").style("flex:1 1 400px"):
            with ui.row().classes("self-end gap-1 p-1").style(
                    "border-radius:8px;background:#E1E6ED;border:1px solid var(--gx-border)") \
                    .props(f'role=group aria-label="{t("common.lang_label", lang)}"'):
                for code in LANGS:
                    ui.button(code.upper(), on_click=lambda c=code: self.switch_lang(c)) \
                        .props("unelevated dense" + ("" if code == lang else " flat")).classes("min-w-[40px]")
            ui.label(t("login.title", lang)).classes("text-xl font-bold")
            ui.label(t("login.subtitle", lang)).classes("gx-muted text-sm -mt-3")
            self.username = ui.input(t("login.username", lang), placeholder=t("login.username_placeholder", lang),
                                     value=self.typed_username) \
                .props('autocomplete=username outlined input-class=gx-mono').classes("w-full")
            self.password = ui.input(t("login.password", lang), password=True) \
                .props('autocomplete=current-password outlined').classes("w-full")
            self.password.on("keydown.enter", self.submit)
            if self.error:
                ui.label(self.error).classes("w-full p-3 text-sm").props("role=alert").style(
                    "border-radius:8px;background:#F8D9D9;border:1px solid #F0B4B4;color:#991B1B")
            ui.button(t("login.submit", lang), on_click=self.submit).props("unelevated").classes("w-full h-11")

    def switch_lang(self, lang: str) -> None:
        Session.set_lang(lang)
        self.typed_username = self.username.value or ""
        self.error = ""
        self.content.refresh()

    def submit(self) -> None:
        self.typed_username = self.username.value or ""
        result = users.login(self.ctx.db, self.typed_username, self.password.value or "")
        if not result.ok:
            self.error = t(LOGIN_ERRORS[result.reason], Session.lang())
            self.content.refresh()
            return
        Session.login(result.user)
        ui.navigate.to("/")
