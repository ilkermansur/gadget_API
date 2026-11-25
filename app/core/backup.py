"""Yedek ve geri yükleme (SPEC §8 madde 13).

Bütün portal verisi tek SQLite dosyasındadır (DATA_DIR/portal.db). Yedek, uygulama çalışırken de tutarlı
alınır: SQLite'ın kendi yedek işlevi (Connection.backup) yazma sürerken bile sağlam bir kopya çıkarır.

Resource kimlikleri SECRET_KEY ile şifrelidir ve anahtar veritabanında DEĞİLDİR. Bu yüzden:
  - yedek, SECRET_KEY ile birlikte saklanmalı (ayrı ve güvenli bir yerde),
  - geri yüklemeden önce check_backup ile yedeğin bu anahtarla açılabildiği kontrol edilir.

Komut satırı: app/backup.py (create / check / restore). Ayrıntılı kullanım: README.md "Yedek".
"""
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.errors import UserError
from core.resources import SecretBox


@dataclass
class BackupReport:
    users: int
    resources: int
    endpoints: int
    history: int
    unreadable_resources: list[str]       # SECRET_KEY ile çözülemeyen resource adları

    @property
    def ok(self) -> bool:
        return not self.unreadable_resources


def backup_name(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"portal-{now.strftime('%Y%m%d-%H%M%S')}.db"


def create_backup(conn: sqlite3.Connection, target: Path) -> Path:
    """Açık veritabanının tutarlı kopyasını `target`'a yazar. Var olan dosyanın üzerine yazmaz."""
    if target.exists():
        raise UserError("errors.backup_exists", target=target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(target) as copy:
        conn.backup(copy)
    copy.close()
    return target


def check_backup(path: Path, box: SecretBox) -> BackupReport:
    """Yedeği açar, sayıları verir ve her resource kimliğinin bu SECRET_KEY ile çözülebildiğini dener."""
    if not path.exists():
        raise UserError("errors.backup_missing", path=path)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)       # salt okunur: yedeğe dokunulmaz
    conn.row_factory = sqlite3.Row
    try:
        count = lambda table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        unreadable = []
        for row in conn.execute("SELECT name, auth_sealed FROM resources"):
            try:
                box.open(row["auth_sealed"])
            except ValueError:
                unreadable.append(row["name"])
        return BackupReport(count("users"), count("resources"), count("endpoints"), count("history"), unreadable)
    except sqlite3.DatabaseError as exc:
        raise UserError("errors.backup_not_portal", reason=exc) from None
    finally:
        conn.close()


def restore_backup(source: Path, database: Path, box: SecretBox) -> BackupReport:
    """Yedeği veritabanının yerine koyar. Uygulama DURMUŞKEN çalıştırılmalı.

    Önce yedek kontrol edilir (bu SECRET_KEY ile okunamıyorsa geri yüklenmez); mevcut veritabanı silinmez,
    yanına .before-restore-<zaman> adıyla kenara alınır.
    """
    report = check_backup(source, box)
    if not report.ok:
        raise UserError("errors.backup_wrong_key", names=", ".join(report.unreadable_resources))
    if database.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        shutil.move(database, database.with_name(f"{database.name}.before-restore-{stamp}"))
        for suffix in ("-wal", "-shm"):                              # eski dosyanın günlük artıkları
            leftover = database.with_name(database.name + suffix)
            if leftover.exists():
                leftover.unlink()
    shutil.copy2(source, database)
    return report
