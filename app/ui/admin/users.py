"""Yönetim · Kullanıcılar (tasarim-v1 §9.3).

İki parça:
  UsersAdmin  → tablo: ad, kullanıcı adı, rol (satırdan değişir), profiller, gruplar, durum, son giriş, işlemler
  UserForm    → yeni / düzenle penceresi: ad, kullanıcı adı (düzenlemede kilitli), e-posta, rol ve
                erişebileceği endpoint grupları (resource başına); düzenlemede "Şifreyi sıfırla"

Rol = ne yapabilir, grup = nerede yapabilir. Admin bütün grupları görür, ona grup atanmaz.
Sil: Vazgeç / Pasif yap / Kalıcı olarak sil. Geçmiş kayıtları iki durumda da kalır. Kendi hesabını silemez,
pasif yapamaz, kendi admin rolünü düşüremez (kurallar core/users.py'de; burada hata mesajı gösterilir).
"""
from nicegui import ui

from core.errors import describe
from core import catalog, resources, users
from core.i18n import t
from core.models import Group, User
from core.openapi import UNTAGGED
from ui import theme
from ui.state import AppContext


def group_label(group: Group, lang: str) -> str:
    if group.name == UNTAGGED:
        return t("common.untagged_group", lang)
    if lang == "en" and group.name_en:
        return group.name_en
    return group.description or group.name


class UsersAdmin:
    COLUMNS = ["col_user", "col_username", "col_role", "col_profiles", "col_groups", "col_status", "col_last_login",
               "col_actions"]
    WIDTHS = "minmax(120px,1.2fr) minmax(100px,1fr) 150px minmax(130px,1fr) 90px 80px 140px 90px"

    def __init__(self, ctx: AppContext, me: User, lang: str):
        self.ctx, self.me, self.lang = ctx, me, lang
        with ui.column().classes("w-full gap-3") as self.root:
            ui.button(t("admin.new_user", lang), icon="person_add", on_click=lambda: self.open_form(None)) \
                .props("unelevated no-caps")
            self.listing()

    @ui.refreshable_method
    def listing(self) -> None:
        with ui.element("div").classes("w-full overflow-x-auto"):
            with ui.grid().classes("w-full items-center gap-x-3 gap-y-0 text-sm").style(
                    f"grid-template-columns:{self.WIDTHS};min-width:960px"):
                for column in self.COLUMNS:
                    ui.label(t(f"admin.{column}", self.lang)).classes("text-xs font-semibold gx-muted py-2")
                for user in users.list_users(self.ctx.db):
                    self.row(user)

    def row(self, user: User) -> None:
        cell = "py-1.5 border-t border-[color:var(--gx-border)] min-w-0 truncate"
        is_me = user.id == self.me.id
        ui.label(user.name + (f" ({t('admin.you', self.lang)})" if is_me else "")).classes(cell + " font-semibold")
        ui.label(user.username).classes(cell + " gx-mono text-xs")
        with ui.element("div").classes(cell):
            role = ui.select({r: t(f"roles.{r}", self.lang) for r in users.ROLES}, value=user.role,
                             on_change=lambda e, u=user: self.change_role(u, e.value)).props("dense options-dense borderless")
            role.set_enabled(not is_me)                    # kendi rolünü buradan değiştiremez
        ui.label(", ".join(t(f"roles.{p}", self.lang) for p in users.visible_profiles(user.role))).classes(cell + " text-xs")
        ui.label(t("admin.all_groups", self.lang) if user.role == "admin"
                 else t("admin.groups_count", self.lang, n=len(user.groups))).classes(cell + " text-xs")
        background, foreground = theme.STATUS_COLORS["2xx" if user.active else "5xx"]
        with ui.element("div").classes(cell):
            ui.label(t("admin.active" if user.active else "admin.inactive", self.lang)) \
                .classes("text-xs font-semibold px-2 py-0.5 inline-block") \
                .style(f"border-radius:999px;background:{background};color:{foreground}")
        ui.label((user.last_login or "").replace("T", " ").rstrip("Z") or t("admin.never", self.lang)) \
            .classes(cell + " text-xs")
        with ui.row().classes("py-1.5 border-t border-[color:var(--gx-border)] gap-0 no-wrap"):
            self.action("edit", "admin.edit", lambda u=user: self.open_form(u))
            if user.active:
                self.action("delete", "admin.delete", lambda u=user: self.delete(u), color="negative", enabled=not is_me)
            else:
                self.action("person", "admin.make_active", lambda u=user: self.reactivate(u))

    def action(self, icon: str, key: str, on_click, color: str = "grey-8", enabled: bool = True) -> None:
        button = ui.button(icon=icon, on_click=on_click).props(
            f'flat round dense color={color} aria-label="{t(key, self.lang)}"').tooltip(t(key, self.lang))
        button.set_enabled(enabled)

    # --- olaylar ---------------------------------------------------------------------------

    def open_form(self, user: User | None) -> None:
        with self.root:
            UserForm(self.ctx, self.me, self.lang, user, on_saved=self.listing.refresh)

    def change_role(self, user: User, role: str) -> None:
        try:
            users.update_user(self.ctx.db, user.id, name=user.name, email=user.email, role=role,
                              acting_user_id=self.me.id)
        except ValueError as exc:
            ui.notify(describe(exc, self.lang), type="negative")
        else:
            ui.notify(t("admin.user_saved", self.lang), type="positive")
        self.listing.refresh()

    def reactivate(self, user: User) -> None:
        users.set_active(self.ctx.db, user.id, True, self.me.id)
        ui.notify(t("admin.user_activated", self.lang))
        self.listing.refresh()

    async def delete(self, user: User) -> None:
        with self.root, ui.dialog() as dialog, ui.card().classes("w-full max-w-lg gap-3"):
            ui.label(t("admin.delete_user_title", self.lang)).classes("text-lg font-bold") \
                .style(f"color:{theme.STATUS_COLORS['5xx'][1]}")
            ui.label(t("admin.delete_user_text", self.lang, name=user.name))
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button(t("common.cancel", self.lang), on_click=lambda: dialog.submit(None)).props("flat no-caps")
                ui.button(t("admin.make_inactive", self.lang), on_click=lambda: dialog.submit("inactive")) \
                    .props("outline no-caps")
                ui.button(t("admin.delete_forever", self.lang), on_click=lambda: dialog.submit("delete")) \
                    .props("unelevated no-caps color=negative")
        choice = await dialog
        try:
            if choice == "inactive":
                users.set_active(self.ctx.db, user.id, False, self.me.id)
                ui.notify(t("admin.user_deactivated", self.lang))
            elif choice == "delete":
                users.delete_user(self.ctx.db, user.id, self.me.id)
                ui.notify(t("admin.user_deleted", self.lang))
        except ValueError as exc:
            ui.notify(describe(exc, self.lang), type="negative")
        self.listing.refresh()


class UserForm:
    def __init__(self, ctx: AppContext, me: User, lang: str, current: User | None, on_saved):
        self.ctx, self.me, self.lang, self.current, self.on_saved = ctx, me, lang, current, on_saved
        self.role = current.role if current else "operator"
        self.chosen: set[tuple[int, str]] = set(current.groups) if current else set()

        with ui.dialog() as self.dialog, ui.card().classes("w-full max-w-2xl gap-3").style("max-height:90vh"):
            ui.label(t("admin.edit_user" if current else "admin.new_user", lang)).classes("text-lg font-bold")
            with ui.row().classes("w-full gap-2 no-wrap"):
                self.name = ui.input(t("admin.user_name", lang) + " *", value=current.name if current else "") \
                    .props("outlined dense").classes("flex-1")
                self.username = ui.input(t("admin.username", lang) + " *", value=current.username if current else "") \
                    .props("outlined dense input-class=gx-mono").classes("flex-1")
                self.username.set_enabled(current is None)          # giriş kimliği değişmez
            with ui.row().classes("w-full gap-2 no-wrap"):
                self.email = ui.input(t("admin.email", lang), value=current.email if current else "") \
                    .props("outlined dense").classes("flex-1")
                role = ui.select({r: t(f"roles.{r}", lang) for r in users.ROLES}, value=self.role,
                                 label=t("admin.role", lang), on_change=self.set_role).props("outlined dense").classes("flex-1")
                role.set_enabled(current is None or current.id != me.id)
            if current is None:
                self.password = ui.input(t("admin.password", lang) + " *", password=True, password_toggle_button=True) \
                    .props("outlined dense autocomplete=new-password").classes("w-full")
            ui.separator()
            ui.label(t("admin.groups_title", lang)).classes("font-semibold")
            with ui.scroll_area().classes("w-full").style("height:min(36vh,320px)"):
                self.group_picker()
            self.error = ui.label("").classes("text-sm").style(f"color:{theme.STATUS_COLORS['5xx'][1]}")
            with ui.row().classes("w-full items-center gap-2"):
                if current is not None:
                    ui.button(t("admin.reset_password", lang), icon="key", on_click=self.reset_password) \
                        .props("flat no-caps")
                ui.space()
                ui.button(t("common.cancel", lang), on_click=self.dialog.close).props("flat no-caps")
                ui.button(t("admin.save", lang), on_click=self.save).props("unelevated no-caps")
        self.dialog.open()

    @ui.refreshable_method
    def group_picker(self) -> None:
        if self.role == "admin":
            ui.label(t("admin.groups_admin_note", self.lang)).classes("gx-muted text-sm")
            return
        for resource in resources.list_resources(self.ctx.db, self.ctx.box):
            ui.label(theme.resource_label(resource, self.lang)).classes("text-xs font-semibold gx-muted mt-2")
            groups = catalog.assignable_groups(self.ctx.db, resource.id)
            if not groups:
                ui.label(t("admin.groups_none", self.lang)).classes("gx-muted text-xs")
            with ui.row().classes("gap-x-4 gap-y-0"):
                for group in groups:
                    key = (resource.id, group.name)
                    ui.checkbox(group_label(group, self.lang), value=key in self.chosen,
                                on_change=lambda e, k=key: self.toggle_group(k, e.value)).props("dense")

    def set_role(self, event) -> None:
        self.role = event.value
        self.group_picker.refresh()

    def toggle_group(self, key: tuple[int, str], checked: bool) -> None:
        if checked:
            self.chosen.add(key)
        else:
            self.chosen.discard(key)

    def save(self) -> None:
        name, email = (self.name.value or "").strip(), (self.email.value or "").strip()
        try:
            if not name:
                raise ValueError(t("admin.user_name", self.lang) + " *")
            if self.current:
                user = users.update_user(self.ctx.db, self.current.id, name=name, email=email, role=self.role,
                                         acting_user_id=self.me.id)
            else:
                user = users.create_user(self.ctx.db, self.username.value or "", name, self.password.value or "",
                                         self.role, email=email)
        except ValueError as exc:
            self.error.set_text(describe(exc, self.lang))
            return
        # Admin'in grupları saklanmaz (hepsini görür); rolü sonra düşürülürse sıfırdan atanır.
        users.set_groups(self.ctx.db, user.id, [] if self.role == "admin" else sorted(self.chosen))
        ui.notify(t("admin.user_saved", self.lang), type="positive")
        self.dialog.close()
        self.on_saved()

    async def reset_password(self) -> None:
        with ui.dialog() as dialog, ui.card().classes("w-full max-w-sm gap-3"):
            ui.label(t("admin.reset_password", self.lang)).classes("font-bold")
            new = ui.input(t("admin.new_password", self.lang), password=True, password_toggle_button=True) \
                .props("outlined dense autocomplete=new-password").classes("w-full")
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button(t("common.cancel", self.lang), on_click=lambda: dialog.submit(None)).props("flat no-caps")
                ui.button(t("admin.save", self.lang), on_click=lambda: dialog.submit(new.value)).props("unelevated no-caps")
        password = await dialog
        if not password:
            return
        users.set_password(self.ctx.db, self.current.id, password)
        ui.notify(t("admin.password_reset_done", self.lang), type="positive")
