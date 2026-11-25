"""Yönetim ekranları (tasarim-v1 §9): sadece admin profilinde görünür.

area.py       → Yönetim alanının iskeleti: açılır/kapanır gruplar
resources.py  → Resource tablosu, yeni/düzenle formu, endpoint seçimi, senkron
users.py      → Kullanıcı tablosu, yeni/düzenle (rol + gruplar), şifre sıfırlama, pasif/sil
branding.py   → Ürün adı, logo, palet ve önizleme
roles.py      → Roller ve yetkiler tablosu (salt okunur)
"""
