"""Yedek komutları (container içinde çalışır; README.md "Yedek").

    python backup.py create                 → /data/backups/portal-<zaman>.db  (uygulama çalışırken de olur)
    python backup.py check  <dosya>         → sayılar + kimlikler bu SECRET_KEY ile açılıyor mu
    python backup.py restore <dosya>        → yedeği geri koyar (uygulama DURMUŞKEN)

İşin kendisi core/backup.py'dedir; burası sadece komut satırı.
"""
import sys
from pathlib import Path

from config import load_settings
from core import backup
from core.db import connect
from core.resources import SecretBox


def main(args: list[str]) -> int:
    settings = load_settings()
    box = SecretBox(settings.secret_key)
    database = settings.data_dir / "portal.db"
    command, rest = (args[0], args[1:]) if args else ("", [])
    try:
        if command == "create":
            target = Path(rest[0]) if rest else settings.data_dir / "backups" / backup.backup_name()
            print(f"Yedek alındı: {backup.create_backup(connect(database), target)}")
            return show(backup.check_backup(target, box))
        elif command == "check" and rest:
            return show(backup.check_backup(Path(rest[0]), box))
        elif command == "restore" and rest:
            show(backup.restore_backup(Path(rest[0]), database, box))
            print(f"Geri yüklendi: {database} (eskisi yanında .before-restore-* adıyla duruyor)")
        else:
            print(__doc__)
            return 2
    except ValueError as exc:
        print(f"HATA: {exc}")
        return 1
    return 0


def show(report: backup.BackupReport) -> int:
    """Raporu yazar; kimlikler okunamıyorsa 1 döner (betikler hatayı yakalayabilsin)."""
    print(f"  kullanıcı: {report.users} · resource: {report.resources} · endpoint: {report.endpoints} · "
          f"geçmiş: {report.history}")
    print("  kimlikler bu SECRET_KEY ile okunuyor: " +
          ("evet" if report.ok else f"HAYIR → {', '.join(report.unreadable_resources)}"))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
