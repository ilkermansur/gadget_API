"""Uygulamanın ayarlarını ortam değişkenlerinden okur.

Bütün ayarlar burada tek bir `Settings` nesnesinde toplanır; başka hiçbir dosya `os.environ` okumaz.
SECRET_KEY zorunludur: gizli değerleri şifreler ve oturumu imzalar. Yoksa uygulama açılmaz.
"""
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path          # SQLite dosyası ve yüklenen dosyalar burada durur
    secret_key: str         # gizli değer şifreleme + oturum imzası
    host: str               # container içinde 0.0.0.0, yerelde 127.0.0.1
    port: int
    admin_user: str         # sadece ilk açılışta, veritabanı boşken kullanılır
    admin_password: str


def load_settings(env: dict[str, str] | None = None) -> Settings:
    # env parametresi testler içindir; normalde gerçek ortam değişkenleri okunur.
    env = os.environ if env is None else env

    secret_key = env.get("SECRET_KEY", "")
    if not secret_key:
        raise RuntimeError("SECRET_KEY tanımlı değil. .env dosyasına ekle (bkz. .env.example).")

    return Settings(
        data_dir=Path(env.get("DATA_DIR", "/data")),
        secret_key=secret_key,
        host=env.get("HOST", "127.0.0.1"),
        port=int(env.get("PORT", "8080")),
        admin_user=env.get("ADMIN_USER", ""),
        admin_password=env.get("ADMIN_PASSWORD", ""),
    )
