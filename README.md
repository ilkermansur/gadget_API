# Gadget API

Tek sayfalık bir **NiceGUI** uygulaması: son kullanıcıya hitap eden, evcilleştirilmiş bir API istemcisi
(Postman/Hoppscotch'un operatör hali). Bir FastAPI'nin `/openapi.json` şemasını okur; menü, formlar, Docs ve
TR/EN metinler oradan kendiliğinden oluşur. Hedef API çağrıları **sunucu tarafında** yapılır, kimlik bilgileri
tarayıcıya hiç gitmez.

- **Operatör:** kendisine açılan endpoint'leri formla çalıştırır, yanıtı tablo/JSON/ham olarak görür, CSV indirir,
  kayıtları düzenler (sadece değişen alanlar PATCH ile gider), geçmişine bakar.
- **Geliştirici:** FastAPI endpoint'inin sunumunu portal katmanında düzeltir, elle **Custom** endpoint tanımlar.
- **Admin:** resource (FastAPI) ekler, endpoint seçer, senkronlar; kullanıcı, rol, grup ve branding yönetir.

Sürüm: **v1.0**

## Gereksinimler

- Docker ve Docker Compose (v2, `docker compose` komutu)
- Portalın ulaşabileceği bir FastAPI (deneme için repodaki `test-api` yeter)
- Yerel geliştirme için ayrıca Python 3.12

## Kurulum

Üç adım: ayar dosyasını oluştur, gizli anahtarı yaz, admin şifresini yaz.

**1. Ayar dosyası.** Örnekten kopyala. Bundan sonra yalnızca `.env` düzenlenir; `.env.example` repoda kalan boş
şablondur, ona değer yazılmaz (uygulama onu okumaz).

```bash
cp .env.example .env
```

**2. Gizli anahtar (`SECRET_KEY`).** Bu komut rastgele bir anahtar üretip `.env`'deki boş `SECRET_KEY=` satırına
kendisi yazar. Satır zaten doluysa dokunmaz.

```bash
python3 -c "import secrets, pathlib; p = pathlib.Path('.env'); t = p.read_text(); p.write_text(t.replace('SECRET_KEY=\n', 'SECRET_KEY=' + secrets.token_urlsafe(32) + '\n', 1)); print('SECRET_KEY yazıldı' if 'SECRET_KEY=\n' in t else 'SECRET_KEY zaten dolu, dokunulmadı')"
```

> **`SECRET_KEY` kaybolursa** resource'ların kimlik bilgileri (şifre, token, anahtar) bir daha çözülemez.
> Veritabanı yedeğiyle birlikte ama **ayrı ve güvenli bir yerde** sakla. `.env` asla commit edilmez.

**3. Admin şifresi.** `.env`'i bir düzenleyiciyle açıp `ADMIN_PASSWORD=` satırına bir şifre yaz
(`ADMIN_USER` varsayılan olarak `admin`). Bu ikisi sadece ilk açılışta, veritabanı boşken ilk admin hesabını oluşturur.

**Kontrol.** İki satırın da dolu olduğunu değerlerini göstermeden kontrol et. İkisi de `dolu` demeli:

```bash
grep -E "^(SECRET_KEY|ADMIN_PASSWORD)=" .env | sed -E 's/=.+$/= dolu/; s/=$/= BOŞ/'
```

Biri boşsa uygulama başlamaz:

- `SECRET_KEY` boşsa `docker compose` "SECRET_KEY .env icinde bos" diyerek hiç başlatmaz.
- İlk açılışta admin bilgileri boşsa uygulama logda nedenini yazıp durur.

## Çalıştırma

Üretim (sadece portal, http://localhost:8080):

```bash
docker compose up -d --build
```

Test ortamı (portal + örnek FastAPI `test-api`; portal ona `http://test-api:9000` adresiyle ulaşır):

```bash
docker compose --profile test up -d --build
```

**Çalışıyor mu?** `app` satırında `Up` görmelisin. `Restarting` görüyorsan uygulama açılırken hata veriyordur.

```bash
docker compose ps
```

Nedeni logun sonundadır:

```bash
docker compose logs --tail 20 app
```

Ayarı düzelttikten sonra aynı `up` komutunu tekrar çalıştır.

## İlk 10 dakika

Test ortamını açtıktan sonra:

1. http://localhost:8080 → `.env`'deki `ADMIN_USER` / `ADMIN_PASSWORD` ile gir.
2. Üst bardan profili **Admin** yap → Yönetim · **Resource'lar** → **Yeni resource**.
3. Ad: `Test API`, OpenAPI adresi: `http://test-api:9000/openapi.json` → **Şemayı kontrol et** → **Kaydet**.
4. Açılan **Endpoint seçimi** penceresinde menüye girecekleri işaretle → **Seçimi kaydet**.
   Hiçbir endpoint kendiliğinden menüye girmez.
5. Yönetim · **Kullanıcılar** → **Yeni kullanıcı**: rol **Operatör**, gruplardan örneğin `Cihazlar` → Kaydet.
6. Profili **Operatör** yap → soldaki menüden **Cihazları listele** → **Çalıştır**: yanıt tabloda gelir.
   PATCH endpoint'i de seçiliyse satırdaki **Düzenle** ile kaydı değiştirip yalnızca değişen alanları gönderebilirsin.
7. Çıkış yap, yeni kullanıcıyla gir: sadece verdiğin grubu görür.

## Takılacak FastAPI için kısa kurallar

Portal yalnızca `/openapi.json`'ı okur. Bu kurallara uyan bir FastAPI'de menü, formlar ve Docs kendiliğinden kurulur;
uymayan API'ler de eklenebilir, eksikler portalda (Geliştirici · Tanım) düzeltilir. Çalışan örnek: `test-api/service.py`.

- **Menü grubu = `tags`.** Grup aynı zamanda erişim birimidir: kullanıcılara grup verilir.
- **Ad ve Docs:** `summary` (menüdeki ad), `description` (Docs, markdown). İngilizce için
  `openapi_extra={"x-gadget": {"summary_en": "...", "description_en": "..."}}`.
- **Form alanları Pydantic modelinden:** her alanın tipi olmalı; `Field(title=..., description=...)` etiket ve yardım olur.
  Doğrulamayı FastAPI yapar, 422 hataları alanların altında gösterilir.
- **Düzenleme:** `GET /x/{id}` + `PATCH /x/{id}`; PATCH yalnızca gelen alanları güncellemeli (`model_dump(exclude_unset=True)`).
  Bağlantı yol düzeninden kendiliğinden kurulur ya da `x-gadget.row_action` / `read_from` ile yazılır.
- **Listeler:** `[...]` ya da `{"count": n, "items": [...]}`; başka bir kabukta `x-gadget.list_path` (ör. `data.list`).
- **Diğer `x-gadget` ipuçları:** `view` (`table` / `json` / `raw`), `confirm` (onay iste; DELETE her zaman onaylı).
- **Kimlik:** `components.securitySchemes` ile bildirilirse resource eklenirken tür önerilir.

Şema değişikliği portala admin **Senkronize et** deyince girer; eşleştirme metot + path ile yapılır.

Senkron denemesi için test-api değişmiş şemayla açılabilir (bir endpoint eklenir, biri kalkar, açıklamalar değişir):

```bash
TEST_API_SCHEMA=v2 docker compose --profile test up -d test-api
```

## Veri ve kalıcılık

Bütün durum `gadget-data` volume'unda (`/data`): SQLite veritabanı (`portal.db`), oturum deposu, yedekler.
Container yeniden başlatılsa, yeniden derlense ya da `docker compose down` yapılsa da veri kalır.
**`docker compose down -v` volume'u siler**: bütün veri gider.

## Yedek

Yedek, uygulama çalışırken alınabilir (SQLite'ın kendi yedek işleviyle tutarlı kopya):

```bash
docker compose exec app python backup.py create
```

Yedek `/data/backups/portal-<zaman>.db` olarak yazılır. Makineye kopyalamak için:

```bash
docker compose cp app:/data/backups/portal-20261125-093000.db ./
```

Bir yedeğin bu `SECRET_KEY` ile açılabildiğini kontrol etmek için (okunamıyorsa çıkış kodu 1):

```bash
docker compose exec app python backup.py check /data/backups/portal-20261125-093000.db
```

Geri yükleme **uygulama durmuşken** yapılır. Mevcut veritabanı silinmez, yanına `portal.db.before-restore-<zaman>`
adıyla kenara alınır. Yedek bu `SECRET_KEY` ile okunamıyorsa geri yüklenmez.

```bash
docker compose stop app
```

```bash
docker compose run --rm app python backup.py restore /data/backups/portal-20261125-093000.db
```

```bash
docker compose start app
```

Makinedeki bir yedeği geri yüklemek için önce volume'a kopyala (uygulama çalışırken):

```bash
docker compose cp ./portal-20261125-093000.db app:/data/backups/
```

## Container'sız çalıştırma (geliştirme)

Yerel ortam (bir kez; portal ve test-api aynı venv'de):

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
```

Portal:

```bash
cd app && DATA_DIR=../.data SECRET_KEY=dev ADMIN_USER=admin ADMIN_PASSWORD=dev ../.venv/bin/python main.py
```

test-api:

```bash
cd test-api && ../.venv/bin/uvicorn service:app --port 9000
```

## Güvenlik notları

- Hedef API'ye giden istekler sunucudan gider; resource kimlikleri veritabanında `SECRET_KEY` ile şifreli durur,
  ekranda ve geçmişte görünmez (geçmişte gizli alanlar maskeli, kimlik başlıkları hiç yazılmaz).
- Kullanıcı şifreleri scrypt ile hash'lenir. Oturum 15 dakika işlem yapılmazsa kapanır.
- Geçmiş ve denetim kayıtları silinmez; kullanıcı pasif yapılsa ya da silinse de kalır.
- Portal kendi başına HTTPS sunmaz; dışarı açılacaksa önüne TLS sonlandıran bir ters vekil (reverse proxy) koy.
