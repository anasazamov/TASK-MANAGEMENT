# Davomat (yo'qlama) — Hikvision domofon integratsiyasi

Tizim karta/PIN bilan eshik ochilishini xodim davomatiga aylantiradi, kun birinchi
kirishida yuzni tekshiradi va Telegram bot orqali ogohlantiradi.

## Arxitektura

Domofon internetga chiqarilmaydi, ilova esa bulutdagi serverda. Ikkalasini LAN'dagi
kichik **ko'prik agent** bog'laydi:

```
[Domofon DS-KV6124-WBE1]  ──(lokal: ISAPI AcsEvent + kamera snapshot)──►  [Agent]
[Agent (LAN, Windows Server 2012)]  ──(chiquvchi HTTPS)──►  [/attendance/hook/<secret>/  (bulut)]
```

- **Agent** (`scripts/attendance_agent.py`) — mustaqil, faqat Python standart
  kutubxonasi (3.6+), Django kerak emas. Domofon jurnalini (AcsEvent) o'qiydi, yangi
  karta hodisalarini + yuz suratini serverga uzatadi.
- **Server** hodisani `AccessEvent` + kunlik `Attendance` ga yozadi (Toshkent 07:00
  dan boshlab), kun birinchi kirishida yuzni tekshiradi, ogohlantirishlarni yuboradi.

## 1. Agentni Windows Server 2012'da ishga tushirish

1. `scripts/attendance_agent.py` va `scripts/attendance_agent.config.example.json` ni
   serverga nusxalang (masalan `C:\attendance\`).
2. Configni tayyorlang:
   ```
   copy attendance_agent.config.example.json attendance_agent.config.json
   ```
   `attendance_agent.config.json` da to'ldiring:
   - `device_host`, `device_user`, `device_password` — domofon (LAN).
   - `server_url` — bulutdagi to'liq manzil + maxfiy yo'l:
     `https://DOMEN/attendance/hook/<ATTENDANCE_WEBHOOK_SECRET>/`
   - `lookback_minutes` — `5` (production).
3. Sinab ko'ring (bir marta):
   ```
   python attendance_agent.py --once
   ```
   Loglarda `Forwarded ... -> 200` ko'rinsa, ishladi.
4. Doimiy xizmat (NSSM bilan — tavsiya):
   ```
   nssm install AttendanceAgent "C:\Python3x\python.exe" "C:\attendance\attendance_agent.py --config C:\attendance\attendance_agent.config.json"
   nssm start AttendanceAgent
   ```
   Yoki Task Scheduler'da «At startup» + «Restart on failure».

> Server 2012 eski bo'lsa Python 3.8 o'rnating (yangi versiyalar qo'llab-quvvatlamaydi).
> Agentga qo'shimcha kutubxona kerak emas.

## 1a. Kartalarni (va PIN'ni) xodimlarga bog'lash

**Birinchi marta, birdan:** `python manage.py seed_access_cards` (avval `--dry-run`). Ro'yxat
21.10.2026 da domofondan olingan va tekshirilgan 27 xodim; `seed_staff` kabi qayta ishga
tushirsa bo'ladi. Keyingi o'zgarishlar uchun quyidagi `sync_access_cards`.

Domofondagi har bir shaxsning `employeeNo` va karta raqami tizimdagi foydalanuvchiga
bog'lanadi. PIN bilan ochilganda hodisa `employeeNo` bilan keladi, shuning uchun u ham
shu bog'lash orqali taniladi (PIN kodning o'zi o'qilmaydi).

1. Agent turgan kompyuterda ro'yxatni eksport qiling:
   ```
   python attendance_agent.py --config attendance_agent.config.json --export-users people.json
   ```
2. `people.json` ni serverga ko'chiring va avval **ko'rib chiqing** (hech narsa yozilmaydi):
   ```
   python manage.py sync_access_cards --file people.json
   ```
   Ismlar domofonda boshqacha yozilgan bo'ladi (`ILYOS AXMEDOV` ↔ `Ahmedov Ilyos …`), shuning
   uchun taqribiy solishtiriladi: `aniq` — ishonchli, `taxminiy` — tekshiring, `topilmadi` —
   qo'lda bog'lang.
3. Yozing:
   ```
   python manage.py sync_access_cards --file people.json --map 17=narziyev --skip <sinov ID> --apply
   ```
   `--map EMPLOYEE_NO=USER` qo'lda bog'laydi (USER — tizimdagi login yoki ID, masalan
   `--map 17=narziyev` yoki `--map 17=28`), `--skip` keraksiz yozuvni o'tkazib yuboradi,
   `--likely` taxminiylarni ham yozadi. Qayta ishga tushirish xavfsiz: o'zgarmaganlar tegilmaydi,
   boshqa xodimga bog'langan karta `ZIDDIYAT` deb ko'rsatiladi va yozilmaydi.

Domofon ko'rinadigan joyda (`HIKVISION_*` sozlangan) `--file` siz to'g'ridan-to'g'ri o'qiydi.
Yangi xodimga karta berilganda 1–3-qadamlarni takrorlang.

## 2. Yuz taqqoslash (OpenCV — Windows va Linux'da bir xil)

Faqat `opencv-python-headless` kerak (u `requirements.txt` da, tayyor wheel bilan
keladi — Python 3.15'da ham; insightface, onnxruntime, C++ build tools kerak emas).
Model fayllarini bir marta yuklang:

```
pip install -r requirements.txt
python scripts/setup_face_models.py     # YuNet (aniqlash) + SFace (taqqoslash) -> face_models/
```

Model fayllari yo'q bo'lsa tizim **karta-only** rejimiga tushadi (yuz tekshirilmaydi,
davomat baribir yoziladi).

**Yuz surati qayerdan olinadi.** Agar hodisada domofonning o'z surati (`pictureURL`)
bo'lsa, agent o'shani ishlatadi. DS-KV6124-WBE1 karta hodisalariga surat biriktirmaydi
(`saveVerificationPic` yoqilganda ham), shuning uchun agent hodisani ko'rishi bilan
kameradan jonli kadr oladi (`fallback_snapshot: true`). So'rov oralig'i **1 soniya**
bo'lgani uchun kadr karta bosilgandan ~1 soniya keyin, odam hali eshik oldida turganda
olinadi. Real test (2026-10-21): orqadan yoritilgan holatda ham moslik **0.63**
(chegara 0.363) — davomat avtomatik tasdiqlandi. `interval_seconds` ni oshirsangiz,
kadr kech qolib bo'sh eshikni olishi mumkin.

- Xodim yuzini biriktirish: **Xodimlar → Davomat → Yuzlar** sahifasida har xodimga aniq, yorug'
  surat yuklang. Server yuzni tahlil qiladi va embedding + surat saqlaydi.
- Kun birinchi kirishida tushirilgan surat biriktirilgan yuz bilan solishtiriladi:
  - moslik `FACE_MATCH_THRESHOLD` (0.363) dan yuqori → davomat tasdiqlanadi, surat
    saqlanmaydi;
  - past → surat saqlanadi, davomat «Tekshiruvda» bo'ladi, **Xodimlar → Davomat → Tekshiruvlar**
    sahifasida kotib referent tasdiqlaydi yoki rad etadi (botga ham xabar boradi).

## 3. Ogohlantirishlar (Telegram bot)

`manage.py attendance_alerts` har soat ishga tushadi (Docker `reminders` konteyneri
uni `send_reminders` bilan birga bajaradi; Dockersiz — Task Scheduler/cron har soat).

| Kim | Qachon | Nima |
| --- | --- | --- |
| Kotib referent | kun birinchi kirishi | «X ishga keldi — soat HH:MM» |
| Kotib referent | past moslik | «Yuz tekshiruvi kerak» + havola |
| Kotib referent | 11:00 (Toshkent) | bugun kelmaganlar ro'yxati |
| Xodim | 09:00 (Toshkent) | «ishga kechikyapsiz» (hali kelmagan bo'lsa) |
| Xodim | yo'qlamasi tekshiruvga tushsa | «… kirishingiz tekshiruvga yuborildi; keyingi kirishda yuzingizni ko'rsating» |
| Xodim | review rad etilsa (kun butunlay hisobga olinmasa) | «yo'qlamangiz tasdiqlanmadi» + sabab |

Uzrli sababi kiritilgan xodimga 09:00 dagi xabar yuborilmaydi; 11:00 dagi ro'yxatda u
«Uzrli» bo'limida sababi bilan chiqadi.

Har ogohlantirish kuniga bir marta (`AttendanceAlert` takrorlanishni to'xtatadi).

## 4. Sozlamalar (.env)

```
ATTENDANCE_WEBHOOK_SECRET=uzun-tasodifiy-satr
ATTENDANCE_ALLOWED_IPS=<agent IP>        # ixtiyoriy
ATTENDANCE_DAY_START_HOUR=7
ATTENDANCE_LATE_HOUR=9
ATTENDANCE_ABSENT_HOUR=11
FACE_MODEL=buffalo_l
FACE_MATCH_THRESHOLD=0.35
```

## Sahifalar

- **Xodimlar → Davomat** — kunlik jadval (kun chiplari, sahifa qayta yuklanmaydi, bugungi
  ro'yxat o'zi yangilanadi), Excel eksport. `ATTENDANCE_LATE_HOUR` (09:00) dan keyin
  kelganlar «Kechikdi» va kechikish muddati bilan; har qatorda **uzrli sabab** kiritish
  (kasal, kechroq keladi + vaqt, xizmat safari, ta'til, boshqa; bir necha kunga birdan),
  kim kiritgani saqlanadi.
- **Xodimlar → Eshik ochilishlari** — barcha kirishlar, sana-vaqt oralig'i filtri.
- **Xodimlar → Davomat → Yuzlar** — yuz biriktirish (kotib/rais).
- **Xodimlar → Davomat → Tekshiruvlar** — past moslikdagi yoki yuzsiz kirishlarni tasdiqlash/rad etish (sabab majburiy), ko‘rib chiqilganlar tarixi.
- **Django admin (/admin/) → Davomat bo'limlari** — superuser uchun to'liq nazorat:
  - *Domofon kartalari* — qo'shish/tahrirlash/o'chirish, faollashtirish; xodim sahifasida ham kartalar ro'yxati.
  - *Eshik hodisalari* — xom log (faqat o'qish, audit), «Noma'lum karta» filtri, saqlangan surat.
  - *Kunlik davomat* — faqat o'qish; «Kunni kirishlardan qayta hisoblash» amali.
  - *Yuz tekshiruvlari* — surat bilan; «Tasdiqlash» va «Rad etish» (sabab so'raladi) amallari
    sayt bilan bir xil xizmat orqali ishlaydi: kun qayta hisoblanadi, xodimga xabar boradi.
  - *Uzrli sabablar* — to'liq tahrir, kim kiritgani saqlanadi.
  - *Xodimlar yuzi* — surat yuklab biriktirish (yuz topilmasa saqlanmaydi), o'chirish.
  - *Davomat ogohlantirishlari* — kuniga bir martalik belgilar; o'chirilsa, keyingi ishga
    tushishda o'sha ogohlantirish qayta yuboriladi.
