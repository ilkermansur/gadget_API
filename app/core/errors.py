"""Kullanıcıya gösterilecek hatalar: metin anahtarı + değerler, dil ekranda seçilir.

Çekirdek dil bilmez (hangi dilde gösterileceği ekranın işi). Bu yüzden hata, metnin kendisiyle değil,
app/locales/*.toml'daki anahtarıyla fırlatılır:

    raise UserError("errors.name_empty")
    raise UserError("errors.logo_ratio", width=800, height=600)

Ekran describe(exc, lang) ile kullanıcının dilinde metne çevirir. str(exc) Türkçe metindir: loglar, testler
ve UserError olmayan ValueError'lar için her şey eskisi gibi çalışır (UserError bir ValueError'dır).

Değerlerin içinde başka bir mesaj ya da çevrilecek bir ad olabilir:
  Msg("errors.not_openapi")           → o da aynı dilde çevrilir
  Term("admin.auth_key.", ["username"]) → her eleman "admin.auth_key.username" metniyle, virgülle birleştirilir
"""
from dataclasses import dataclass, field

from core.i18n import DEFAULT_LANG, t


@dataclass
class Msg:
    """Çevrilecek bir mesaj (hata içinde hata gibi)."""
    key: str
    values: dict = field(default_factory=dict)


@dataclass
class Term:
    """Çevrilecek ad(lar): prefix + her eleman bir metin anahtarı ("admin.auth_type." + "basic")."""
    prefix: str
    names: list


class UserError(ValueError):
    def __init__(self, key: str, /, **values):      # "/": değerlerden biri de "key" adını taşıyabilir
        self.key, self.values = key, values
        super().__init__(render(key, values, DEFAULT_LANG))


def render(key: str, values: dict, lang: str) -> str:
    return t(key, lang, **{name: render_value(value, lang) for name, value in values.items()})


def render_value(value: object, lang: str) -> object:
    if isinstance(value, Msg):
        return render(value.key, value.values, lang)
    if isinstance(value, Term):
        return ", ".join(t(value.prefix + str(name), lang) for name in value.names)
    return value


def describe(exc: Exception, lang: str) -> str:
    """Hatanın kullanıcının dilindeki metni. UserError değilse (beklenmeyen hata) olduğu gibi."""
    if isinstance(exc, UserError):
        return render(exc.key, exc.values, lang)
    return str(exc)
