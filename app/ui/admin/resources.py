"""Yönetim · Resource'lar (tasarim-v1 §9.1).

Beş parça:
  ResourcesAdmin    → tablo: ad, tür, adres, kimlik özeti, seçim özeti, son çekme, işlemler
  ResourceForm      → yeni / düzenle penceresi: bütün kimlik türleri, "Şemayı kontrol et" ile tür önerisi
  SelectionDialog   → endpoint seçimi: şemadaki endpoint'ler onay kutusuyla, gruplara göre
  SyncDialog        → senkron: önizleme + kararlar (yeni → Ekle/Ekleme, çakışma → hangisi); karar eksikse uygulanmaz
  CustomEndpointsDialog → Custom resource'undaki elle tanımlı endpoint'ler: yeni, düzenle, sil (admin)

Akış (yeni resource): Kaydet → şema çekilir ve saklanır → Endpoint seçimi açılır. Hiçbir endpoint
kendiliğinden menüye girmez. Şema alınamazsa resource yine kaydedilir; tablodan "Şemayı çek" ile tekrar denenir.
Şema bir kez saklandıktan sonra yenisi SADECE senkrondan geçer (sessizce üzerine yazılmaz).

Gizli değerler (şifre, token, anahtar) ekrana hiç gelmez: düzenlemede boş görünür, boş bırakılırsa eskisi korunur
(core/resources.py merge_auth). Kayıt, şifreleme ve kurallar core/resources.py ve core/catalog.py'dedir.
"""
from nicegui import ui

from core.errors import describe
from core import catalog, custom, resources, sync
from core.i18n import t, upper
from core.models import Resource
from core.openapi import UNTAGGED, parse_openapi
from ui import theme
from ui.custom_editor import CustomEditor
from ui.state import AppContext

TRIAL_ID = 0      # kaydedilmemiş resource ile şema denemesi: token önbelleğinde bu kimlikle durur, sonra atılır


# =============================================================================
# Tablo
# =============================================================================

class ResourcesAdmin:
    COLUMNS = ["col_name", "col_kind", "col_url", "col_auth", "col_selection", "col_fetched", "col_count", "col_actions"]
    WIDTHS = "minmax(100px,1fr) 70px minmax(140px,1.5fr) minmax(120px,1fr) minmax(130px,1fr) 140px 64px 168px"

    def __init__(self, ctx: AppContext, lang: str, on_change):
        self.ctx, self.lang, self.on_change = ctx, lang, on_change
        # Pencereler hep bu kutunun içinde açılır: tablo yenilense ya da form penceresi kapansa da kaybolmazlar.
        with ui.column().classes("w-full gap-3") as self.root:
            ui.button(t("admin.new_resource", lang), icon="add", on_click=lambda: self.open_form(None)) \
                .props("unelevated no-caps")
            self.listing()

    @ui.refreshable_method
    def listing(self) -> None:
        with ui.element("div").classes("w-full overflow-x-auto"):
            with ui.grid().classes("w-full items-center gap-x-3 gap-y-0 text-sm").style(
                    f"grid-template-columns:{self.WIDTHS};min-width:960px"):
                for column in self.COLUMNS:
                    ui.label(t(f"admin.{column}", self.lang)).classes("text-xs font-semibold gx-muted py-2")
                for resource in resources.list_resources(self.ctx.db, self.ctx.box):
                    self.row(resource)

    def row(self, resource: Resource) -> None:
        entries = catalog.list_endpoints(self.ctx.db, resource.id)
        selected, excluded, pending = catalog.selection_counts(entries)
        is_custom = resource.kind == "custom"
        cell = "py-2 border-t border-[color:var(--gx-border)] min-w-0 truncate"
        ui.label(theme.resource_label(resource, self.lang)).classes(cell + " font-semibold")
        ui.label(t(f"admin.kind_{resource.kind}", self.lang)).classes(cell)
        ui.label(resource.openapi_url or "—").classes(cell + " gx-mono text-xs").tooltip(resource.openapi_url or "")
        ui.label(t(f"admin.auth_type.{resource.auth_type}", self.lang) if resource.auth_type == "none" or is_custom
                 else resources.auth_summary(resource)).classes(cell)
        with ui.column().classes(cell + " gap-0"):
            ui.label(t("admin.selection_summary", self.lang, selected=selected, excluded=excluded))
            if pending:
                ui.label(t("admin.selection_pending", self.lang, n=pending)).classes("text-xs").style(
                    f"color:{theme.STATUS_COLORS['4xx'][1]}")
        fetched = (resource.last_fetched or "").replace("T", " ").rstrip("Z")
        ui.label(fetched or ("—" if is_custom else t("admin.never_fetched", self.lang))).classes(cell + " text-xs")
        ui.label(str(len([e for e in entries if not e.removed]))).classes(cell)
        with ui.row().classes("py-2 border-t border-[color:var(--gx-border)] gap-0 no-wrap"):
            if is_custom:
                # Custom resource'un kendisi sabit (silinmez), ama içindeki endpoint'ler buradan yönetilir.
                self.action("list", "admin.custom_endpoints", lambda r=resource: self.open_custom_list(r))
                ui.label(t("admin.fixed", self.lang)).classes("gx-muted text-xs ml-1")
                return
            self.action("checklist", "admin.select_endpoints", lambda r=resource: self.open_selection(r))
            if resource.last_fetched:
                self.action("sync", "admin.sync", lambda r=resource: self.open_sync(r))
            else:
                self.action("cloud_download", "admin.fetch_schema", lambda r=resource: self.first_fetch(r))
            self.action("edit", "admin.edit", lambda r=resource: self.open_form(r))
            self.action("delete", "admin.delete", lambda r=resource: self.delete(r), color="negative")

    def action(self, icon: str, key: str, on_click, color: str = "grey-8") -> None:
        ui.button(icon=icon, on_click=on_click).props(f'flat round dense color={color} aria-label="{t(key, self.lang)}"') \
            .tooltip(t(key, self.lang))

    # --- olaylar ---------------------------------------------------------------------------

    def open_form(self, resource: Resource | None) -> None:
        with self.root:
            ResourceForm(self.ctx, self.lang, resource, self.saved)

    async def saved(self, resource: Resource, is_new: bool) -> None:
        """Form kaydetti. Yeni resource'ta şema hemen çekilir ve seçim açılır."""
        self.listing.refresh()
        self.on_change()
        if is_new and await self.fetch(resource):
            self.open_selection(resource)

    async def first_fetch(self, resource: Resource) -> None:
        """Şeması hiç çekilmemiş resource: çek, sonra seçimi aç (sonraki çekmeler senkrondan geçer)."""
        if await self.fetch(resource):
            self.open_selection(resource)

    async def open_sync(self, resource: Resource) -> None:
        try:
            doc = await catalog.fetch_schema(resource, self.ctx.tokens)
        except ValueError as exc:
            ui.notify(describe(exc, self.lang), type="warning", multi_line=True)
            return
        with self.root:
            SyncDialog(self.ctx, self.lang, resource, doc, on_applied=self.selection_saved)

    async def fetch(self, resource: Resource) -> bool:
        try:
            doc = await catalog.fetch_schema(resource, self.ctx.tokens)
        except ValueError as exc:
            ui.notify(t("admin.saved_no_schema", self.lang, reason=describe(exc, self.lang)), type="warning", multi_line=True)
            return False
        catalog.store_schema(self.ctx.db, resource.id, doc)
        count = len(catalog.list_endpoints(self.ctx.db, resource.id))
        ui.notify(t("admin.schema_fetched", self.lang, n=count), type="positive")
        self.listing.refresh()
        return True

    def open_selection(self, resource: Resource) -> None:
        if catalog.load_doc(self.ctx.db, resource.id) is None:
            ui.notify(t("admin.no_schema", self.lang), type="warning")
            return
        with self.root:
            SelectionDialog(self.ctx, self.lang, resource, on_saved=self.selection_saved)

    def open_custom_list(self, resource: Resource) -> None:
        with self.root:
            CustomEndpointsDialog(self.ctx, self.lang, resource, on_change=self.selection_saved)

    def selection_saved(self) -> None:
        self.listing.refresh()
        self.on_change()

    async def delete(self, resource: Resource) -> None:
        with self.root, ui.dialog() as dialog, ui.card().classes("w-full max-w-lg gap-3"):
            ui.label(t("admin.delete_title", self.lang)).classes("text-lg font-bold").style(f"color:{theme.STATUS_COLORS['5xx'][1]}")
            ui.label(t("admin.delete_warning", self.lang, name=resource.name))
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button(t("common.cancel", self.lang), on_click=lambda: dialog.submit(False)).props("flat no-caps")
                ui.button(t("admin.delete", self.lang), on_click=lambda: dialog.submit(True)) \
                    .props("unelevated no-caps color=negative")
        if not await dialog:
            return
        resources.delete_resource(self.ctx.db, resource.id)
        self.ctx.tokens.drop(resource.id)
        ui.notify(t("admin.deleted", self.lang))
        self.listing.refresh()
        self.on_change()


# =============================================================================
# Yeni / düzenle formu
# =============================================================================

class ResourceForm:
    def __init__(self, ctx: AppContext, lang: str, current: Resource | None, on_saved):
        self.ctx, self.lang, self.current, self.on_saved = ctx, lang, current, on_saved
        self.auth_type = current.auth_type if current else "none"
        # Gizli olmayan değerler forma dolar; gizliler hiç gelmez (boş görünür).
        self.values = {k: v for k, v in (current.auth if current else {}).items() if k not in resources.SECRET_KEYS}
        self.values.setdefault("key_in", "header")
        self.values.setdefault("grant", "password")

        with ui.dialog() as self.dialog, ui.card().classes("w-full max-w-2xl gap-3"):
            ui.label(t("admin.edit_resource" if current else "admin.new_resource", lang)).classes("text-lg font-bold")
            self.name = ui.input(t("admin.name", lang) + " *", value=current.name if current else "") \
                .props("outlined dense").classes("w-full")
            with ui.row().classes("w-full items-start gap-2 no-wrap"):
                self.openapi_url = ui.input(t("admin.openapi_url", lang) + " *", value=current.openapi_url if current else "",
                                            placeholder="http://test-api:9000/openapi.json",
                                            on_change=self.update_base_hint) \
                    .props("outlined dense input-class=gx-mono").classes("flex-1")
                ui.button(t("admin.check_schema", lang), icon="travel_explore", on_click=self.check_schema) \
                    .props("outline no-caps").classes("mt-1")
            self.schema_note = ui.label("").classes("text-sm")
            with ui.row().classes("w-full gap-2 no-wrap"):
                self.base_url = ui.input(t("admin.base_url", lang), value=current.base_url if current else "") \
                    .props("outlined dense input-class=gx-mono").classes("flex-1")
                self.timeout = ui.number(t("admin.timeout", lang), value=current.timeout if current else 30, min=1) \
                    .props("outlined dense").classes("w-36")
            self.base_hint = ui.label("").classes("gx-muted text-xs -mt-2")
            self.update_base_hint()

            ui.separator()
            ui.label(t("admin.auth", lang)).classes("font-semibold")
            ui.label(t("admin.auth_optional", lang)).classes("gx-muted text-xs -mt-2")
            self.auth_select = ui.select({k: t(f"admin.auth_type.{k}", lang) for k in resources.AUTH_FIELDS},
                                         value=self.auth_type, on_change=self.set_auth_type) \
                .props("outlined dense").classes("w-full")
            with ui.column().classes("w-full gap-2"):
                self.auth_inputs()

            self.error = ui.label("").classes("text-sm").style(f"color:{theme.STATUS_COLORS['5xx'][1]}")
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button(t("common.cancel", lang), on_click=self.dialog.close).props("flat no-caps")
                ui.button(t("admin.save", lang), on_click=self.save).props("unelevated no-caps")
        self.dialog.open()

    @ui.refreshable_method
    def auth_inputs(self) -> None:
        keeps_secret = self.current is not None and self.current.auth_type == self.auth_type
        for key in resources.auth_keys(self.auth_type, self.values.get("grant", "password")):
            label = t(f"admin.auth_key.{key}", self.lang)
            if key in ("key_in", "grant"):
                options = {o: t(f"admin.{key}.{o}", self.lang) for o in
                           (["header", "query"] if key == "key_in" else list(resources.TOKEN_GRANT_FIELDS))}
                ui.select(options, label=label, value=self.values.get(key),
                          on_change=lambda e, k=key: self.set_value(k, e.value, redraw=k == "grant")) \
                    .props("outlined dense").classes("w-full")
            elif key in resources.SECRET_KEYS:
                hint = f' hint="{t("admin.secret_kept", self.lang)}"' if keeps_secret else ""
                ui.input(label, password=True, password_toggle_button=True, value=self.values.get(key, ""),
                         on_change=lambda e, k=key: self.set_value(k, e.value)) \
                    .props("outlined dense autocomplete=new-password" + hint).classes("w-full")
            else:
                ui.input(label, value=self.values.get(key, ""), on_change=lambda e, k=key: self.set_value(k, e.value)) \
                    .props("outlined dense").classes("w-full")

    def set_value(self, key: str, value, redraw: bool = False) -> None:
        self.values[key] = value
        if redraw:
            self.auth_inputs.refresh()

    def set_auth_type(self, event) -> None:
        self.auth_type = event.value
        self.auth_inputs.refresh()

    def update_base_hint(self) -> None:
        url = (self.openapi_url.value or "").strip()
        self.base_hint.set_text(t("admin.base_url_hint", self.lang, base=resources.base_from_openapi_url(url)) if url else "")

    def draft(self) -> Resource:
        """Formdaki hali, kaydetmeden: şema denemesi ve kayıt için."""
        keys = resources.auth_keys(self.auth_type, self.values.get("grant", "password"))
        auth = resources.merge_auth(self.current, self.auth_type, {k: self.values.get(k, "") for k in keys})
        url = (self.openapi_url.value or "").strip()
        return Resource(id=self.current.id if self.current else TRIAL_ID, name=(self.name.value or "").strip(),
                        kind="fastapi", openapi_url=url,
                        base_url=((self.base_url.value or "").strip() or resources.base_from_openapi_url(url)).rstrip("/"),
                        auth_type=self.auth_type, auth=auth, timeout=float(self.timeout.value or 30))

    async def check_schema(self) -> None:
        """Kaydetmeden şemayı dener: kaç endpoint var, şema hangi kimlik türünü öneriyor."""
        draft = self.draft()
        try:
            raw = await catalog.fetch_schema(draft, self.ctx.tokens)
        except ValueError as exc:
            self.schema_note.set_text(describe(exc, self.lang))
            self.schema_note.style(f"color:{theme.STATUS_COLORS['5xx'][1]}")
            return
        finally:
            if draft.id == TRIAL_ID:
                self.ctx.tokens.drop(TRIAL_ID)
        doc = parse_openapi(raw)
        suggested = resources.suggest_auth_types(doc)
        lines = [t("admin.schema_ok", self.lang, n=len(doc.endpoints), title=f"{doc.title} {doc.version}".strip())]
        lines.append(t("admin.schema_suggests", self.lang, types=", ".join(t(f"admin.auth_type.{s}", self.lang)
                                                                           for s in suggested))
                     if suggested else t("admin.schema_no_suggestion", self.lang))
        self.schema_note.set_text(" · ".join(lines))
        self.schema_note.style(f"color:{theme.STATUS_COLORS['2xx'][1]}")
        if suggested and self.auth_type == "none":                 # öneri sadece boşken uygulanır
            self.auth_select.value = suggested[0]

    async def save(self) -> None:
        draft = self.draft()
        try:
            if self.current:
                saved = resources.update_resource(
                    self.ctx.db, self.ctx.box, self.current.id, name=draft.name, openapi_url=draft.openapi_url,
                    base_url=draft.base_url, auth_type=draft.auth_type, auth=draft.auth, timeout=draft.timeout)
                self.ctx.tokens.drop(saved.id)          # kimlik değişmiş olabilir: eski token kullanılmasın
            else:
                saved = resources.create_resource(
                    self.ctx.db, self.ctx.box, draft.name, draft.openapi_url, base_url=draft.base_url,
                    auth_type=draft.auth_type, auth=draft.auth, timeout=draft.timeout)
        except ValueError as exc:
            self.error.set_text(describe(exc, self.lang))
            return
        self.dialog.close()
        ui.notify(t("admin.saved", self.lang), type="positive")
        await self.on_saved(saved, self.current is None)


# =============================================================================
# Endpoint seçimi
# =============================================================================

class SelectionDialog:
    def __init__(self, ctx: AppContext, lang: str, resource: Resource, on_saved):
        self.ctx, self.lang, self.resource, self.on_saved = ctx, lang, resource, on_saved
        doc = catalog.load_doc(ctx.db, resource.id)
        endpoints = {e.key: e for e in doc.endpoints}
        entries = [e for e in catalog.list_endpoints(ctx.db, resource.id) if not e.removed and e.key in endpoints]
        self.boxes: dict[int, ui.checkbox] = {}

        with ui.dialog() as self.dialog, ui.card().classes("w-full max-w-3xl gap-3").style("max-height:90vh"):
            ui.label(t("admin.selection_title", lang, name=resource.name)).classes("text-lg font-bold")
            ui.label(t("admin.selection_help", lang)).classes("gx-muted text-sm")
            with ui.row().classes("gap-2"):
                ui.button(t("admin.select_all", lang), on_click=lambda: self.check_all(True)).props("outline dense no-caps")
                ui.button(t("admin.select_none", lang), on_click=lambda: self.check_all(False)).props("outline dense no-caps")
            with ui.scroll_area().classes("w-full").style("height:min(60vh,600px)"):
                for group in doc.groups:
                    members = [e for e in entries if endpoints[e.key].group == group.name]
                    if not members:
                        continue
                    title = t("common.untagged_group", lang) if group.name == UNTAGGED else (group.description or group.name)
                    ui.label(upper(title, lang)).classes("text-xs font-semibold gx-muted mt-2")
                    for entry in members:
                        self.endpoint_row(entry, endpoints[entry.key])
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button(t("common.cancel", lang), on_click=self.dialog.close).props("flat no-caps")
                ui.button(t("admin.save_selection", lang), on_click=self.save).props("unelevated no-caps")
        self.dialog.open()

    def endpoint_row(self, entry, endpoint) -> None:
        background, foreground = theme.METHOD_COLORS.get(entry.method, ("#E1E6ED", "#374151"))
        with ui.row().classes("w-full items-center gap-2 no-wrap py-0.5"):
            self.boxes[entry.id] = ui.checkbox(value=entry.selected).props("dense")
            ui.label(entry.method).classes("gx-mono text-[11px] font-semibold text-center").style(
                f"min-width:56px;padding:1px 0;line-height:18px;border-radius:4px;background:{background};color:{foreground}")
            ui.label(entry.path).classes("gx-mono text-sm")
            ui.label(endpoint.summary).classes("gx-muted text-sm truncate flex-1")
            if not entry.decided:
                ui.badge(t("admin.new_badge", self.lang)).props("color=amber-8")

    def check_all(self, value: bool) -> None:
        for box in self.boxes.values():
            box.value = value

    def save(self) -> None:
        chosen = [endpoint_id for endpoint_id, box in self.boxes.items() if box.value]
        catalog.save_selection(self.ctx.db, self.resource.id, chosen)
        ui.notify(t("admin.selection_saved", self.lang, n=len(chosen)), type="positive")
        self.dialog.close()
        self.on_saved()


# =============================================================================
# Senkron
# =============================================================================

class SyncDialog:
    """Önizleme (core/sync.py plan_sync) ve kararlar. "Uygula" ancak her karar verilince açılır."""

    def __init__(self, ctx: AppContext, lang: str, resource: Resource, new_doc: dict, on_applied):
        self.ctx, self.lang, self.resource, self.new_doc, self.on_applied = ctx, lang, resource, new_doc, on_applied
        self.plan = sync.plan_sync(ctx.db, resource.id, new_doc)
        self.add: dict[str, bool] = {}
        self.choices: dict[str, str] = {}

        with ui.dialog() as self.dialog, ui.card().classes("w-full max-w-3xl gap-3").style("max-height:90vh"):
            ui.label(t("admin.sync_title", lang, name=resource.name)).classes("text-lg font-bold")
            ui.label(t("admin.sync_kept", lang, selected=self.plan.kept_selected, excluded=self.plan.kept_excluded)) \
                .classes("gx-muted text-sm")
            with ui.scroll_area().classes("w-full").style("height:min(60vh,560px)"):
                self.body()
            with ui.row().classes("w-full items-center justify-end gap-2"):
                self.pending = ui.label("").classes("text-sm").style(f"color:{theme.STATUS_COLORS['4xx'][1]}")
                ui.space()
                ui.button(t("common.cancel", lang), on_click=self.dialog.close).props("flat no-caps")
                self.apply_button = ui.button(t("admin.sync_apply", lang), on_click=self.apply).props("unelevated no-caps")
        self.update_pending()
        self.dialog.open()

    def body(self) -> None:
        plan, lang = self.plan, self.lang
        if plan.nothing_to_do:
            ui.label(t("admin.sync_nothing", lang))
        if plan.new:
            self.heading("admin.sync_new")
            for endpoint in plan.new:
                with self.line(endpoint.method, endpoint.path, endpoint.summary):
                    ui.toggle({True: t("admin.sync_add", lang), False: t("admin.sync_skip", lang)},
                              on_change=lambda e, k=endpoint.key: self.decide(self.add, k, e.value)) \
                        .props("dense no-caps unelevated toggle-color=primary")
        if plan.conflicts:
            self.heading("admin.sync_conflicts")
            for conflict in plan.conflicts:
                where = f"{conflict.field_path} · {conflict.key}" if conflict.field_path else conflict.key
                with ui.column().classes("w-full gap-1 p-2").style(
                        "border:1px solid var(--gx-border);border-radius:8px;background:var(--gx-input)"):
                    ui.label(t("admin.sync_conflict_where", lang, endpoint=conflict.endpoint_key, where=where)) \
                        .classes("gx-mono text-sm font-semibold")
                    ui.label(t("admin.sync_fastapi_now", lang, new=repr(conflict.fastapi_new), old=repr(conflict.fastapi_old))) \
                        .classes("text-sm")
                    ui.label(t("admin.sync_portal_has", lang, value=repr(conflict.portal_value))).classes("text-sm")
                    ui.toggle({"portal": t("admin.sync_keep_portal", lang), "fastapi": t("admin.sync_take_fastapi", lang)},
                              on_change=lambda e, k=conflict.id: self.decide(self.choices, k, e.value)) \
                        .props("dense no-caps unelevated toggle-color=primary")
        if plan.changed:
            self.heading("admin.sync_changed")
            for change in plan.changed:
                method, path = change.endpoint_key.split(" ", 1)
                with self.line(method, path, ""):
                    ui.label(", ".join(self.change_text(c) for c in change.changes)).classes("text-xs gx-muted")
        for key, entries in (("admin.sync_removed", plan.removed), ("admin.sync_returned", plan.returned)):
            if entries:
                self.heading(key)
                for entry in entries:
                    self.line(entry.method, entry.path, "")

    def change_text(self, change: str) -> str:
        """core/sync.py describe_changes çıktısı → ekran metni ("field_changed:limit" → "alan değişti: limit")."""
        if ":" in change:
            kind, name = change.split(":", 1)
            return t(f"admin.sync_{kind}", self.lang, name=name or t("common.body", self.lang))
        return t(f"definition.key.{change}", self.lang)

    def heading(self, key: str) -> None:
        ui.label(t(key, self.lang)).classes("text-xs font-semibold gx-muted mt-3")

    def line(self, method: str, path: str, summary: str):
        """Metot rozeti + path + özet; sağına eklenecek kontrol için satırı döner (with ile kullanılır)."""
        background, foreground = theme.METHOD_COLORS.get(method, ("#E1E6ED", "#374151"))
        row = ui.row().classes("w-full items-center gap-2 no-wrap py-0.5")
        with row:
            ui.label(method).classes("gx-mono text-[11px] font-semibold text-center").style(
                f"min-width:56px;padding:1px 0;line-height:18px;border-radius:4px;background:{background};color:{foreground}")
            ui.label(path).classes("gx-mono text-sm")
            ui.label(summary).classes("gx-muted text-sm truncate flex-1")
        return row

    def decide(self, decisions: dict, key: str, value) -> None:
        decisions[key] = value
        self.update_pending()

    def update_pending(self) -> None:
        missing = sync.undecided(self.plan, self.add, self.choices)
        self.pending.set_text(t("admin.sync_pending", self.lang, n=len(missing)) if missing else "")
        self.apply_button.set_enabled(not missing)

    def apply(self) -> None:
        sync.apply_sync(self.ctx.db, self.resource.id, self.new_doc, add=self.add, conflicts=self.choices)
        ui.notify(t("admin.sync_applied", self.lang), type="positive")
        self.dialog.close()
        self.on_applied()


# =============================================================================
# Custom endpoint'ler (admin)
# =============================================================================

class CustomEndpointsDialog:
    """Custom resource'undaki endpoint'lerin listesi. Düzenle = geliştiricinin kullandığı aynı 6 bölümlü pencere
    (ui/custom_editor.py). Sil = menüden kaldırır; kayıt ve geçmiş bağlantısı kalır (core/custom.py delete_custom)."""

    def __init__(self, ctx: AppContext, lang: str, custom_resource: Resource, on_change):
        self.ctx, self.lang, self.custom_resource, self.on_change = ctx, lang, custom_resource, on_change
        with ui.dialog() as self.dialog, ui.card().classes("w-full max-w-3xl gap-3").style("max-height:90vh"):
            with ui.row().classes("w-full items-center"):
                ui.label(t("admin.custom_endpoints", lang)).classes("text-lg font-bold")
                ui.space()
                ui.button(t("custom.add", lang), icon="add", on_click=lambda: self.edit(None)).props("unelevated no-caps")
            with ui.column().classes("w-full gap-0") as self.box:
                self.listing()
            with ui.row().classes("w-full justify-end"):
                ui.button(t("common.close", lang), on_click=self.dialog.close).props("flat no-caps")
        self.dialog.open()

    @ui.refreshable_method
    def listing(self) -> None:
        entries = [e for e in catalog.list_endpoints(self.ctx.db, self.custom_resource.id) if not e.removed]
        if not entries:
            ui.label(t("admin.no_custom_endpoints", self.lang)).classes("gx-muted text-sm py-2")
            return
        for entry in entries:
            spec = custom.load_spec(self.ctx.db, entry.id)
            background, foreground = theme.METHOD_COLORS.get(entry.method, theme.METHOD_COLORS["GET"])
            with ui.row().classes("w-full items-center gap-2 no-wrap py-1.5 border-t border-[color:var(--gx-border)]"):
                ui.label(entry.method).classes("gx-mono text-[11px] font-semibold text-center").style(
                    f"min-width:56px;padding:1px 0;line-height:18px;border-radius:4px;background:{background};color:{foreground}")
                with ui.column().classes("gap-0 flex-1 min-w-0"):
                    ui.label(spec.summary).classes("font-semibold text-sm truncate")
                    ui.label(f"{spec.base_url}{entry.path} · {spec.group}").classes("gx-mono text-xs gx-muted truncate")
                ui.button(icon="edit", on_click=lambda i=entry.id: self.edit(i)) \
                    .props(f'flat round dense color=grey-8 aria-label="{t("admin.edit", self.lang)}"') \
                    .tooltip(t("admin.edit", self.lang))
                ui.button(icon="delete", on_click=lambda e=entry, sp=spec: self.delete(e.id, sp.summary)) \
                    .props(f'flat round dense color=negative aria-label="{t("admin.delete", self.lang)}"') \
                    .tooltip(t("admin.delete", self.lang))

    def edit(self, endpoint_id: int | None) -> None:
        with self.box:
            CustomEditor(self.ctx, self.lang, self.custom_resource, on_saved=self.saved, endpoint_id=endpoint_id)

    async def saved(self, endpoint_id: int | None) -> None:
        self.listing.refresh()
        self.on_change()

    async def delete(self, endpoint_id: int, name: str) -> None:
        with self.box, ui.dialog() as dialog, ui.card().classes("w-full max-w-lg gap-3"):
            ui.label(t("admin.custom_delete_title", self.lang)).classes("text-lg font-bold") \
                .style(f"color:{theme.STATUS_COLORS['5xx'][1]}")
            ui.label(t("admin.custom_delete_warning", self.lang, name=name))
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button(t("common.cancel", self.lang), on_click=lambda: dialog.submit(False)).props("flat no-caps")
                ui.button(t("admin.delete", self.lang), on_click=lambda: dialog.submit(True)) \
                    .props("unelevated no-caps color=negative")
        if not await dialog:
            return
        custom.delete_custom(self.ctx.db, endpoint_id)
        ui.notify(t("custom.deleted", self.lang))
        self.listing.refresh()
        self.on_change()

