# Topshiriq nazorati

Samarkand Invest Company topshiriqlarini boshqarish uchun Django monolit. Claude Design’dagi **Topshiriqlar — sodda** varianti asosida. UI o‘zbek tilida, muddatlar Asia/Tashkent vaqtida.

## Arxitektura

- Bitta Django ilova, serverda render qilinadigan HTML, mahalliy CSS va kichik JavaScript.
- Django ORM, migratsiyalar, session authentication va CSRF himoyasi.
- SQLite bilan lokal ishga tushadi; PostgreSQL konfiguratsiyasi mavjud.
- UI va backend bir serverda. Node.js, React, alohida API server, Redis yoki Celery kerak emas.
- Django 5.2 LTS: [rasmiy reliz ma’lumotlari](https://docs.djangoproject.com/en/5.2/releases/5.2/).

## Lokal ishga tushirish (PowerShell)

Python 3.11–3.14. Ushbu loyiha Python 3.14 va Django 5.2.17 bilan tekshirilgan.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py seed_demo
.\.venv\Scripts\python.exe manage.py collectstatic --noinput
.\.venv\Scripts\python.exe serve.py --host 127.0.0.1 --port 8000
```

Brauzer: http://127.0.0.1:8000

**Lokal tarmoqdan (boshqa kompyuterlardan) ovoz bilan ishlash.** Brauzer mikrofonni faqat HTTPS yoki `localhost`da beradi, shuning uchun `http://192.168...` orqali mikrofon ishlamaydi. HTTPS bilan ishga tushiring:

```powershell
.\.venv\Scripts\python.exe serve.py --host 0.0.0.0 --https
```

Server `certs/` papkasida shu kompyuter IP manzillari uchun o‘z-o‘zidan imzolangan sertifikat yaratadi va 443-portda ishlaydi (`https://192.168.x.x`). 80-portga kelgan `http://` so‘rovlar avtomatik `https://` ga yo‘naltiriladi. `--https` bilan `--port 80` bermang. Birinchi kirishda brauzer «Maxfiy emas» ogohlantirishini ko‘rsatadi: «Qo‘shimcha» → «Davom etish» bosiladi, shundan keyin mikrofon ishlaydi. Qo‘shimcha IP/domen: `--san nom`; o‘z sertifikatingiz: `--ssl-certfile`/`--ssl-keyfile`.

`serve.py` bitta Uvicorn/ASGI jarayonida Django sahifalari, HTTP API va jonli WebSocket ovozini beradi. Oddiy `manage.py runserver` yoki Waitress WebSocket rejimini bermaydi.

`seed_demo` bo‘sh development bazaga 25 xodim, 14 bo‘linma (rahbariyat bilan), 21 topshiriq, tarix va xabarnomalar qo‘shadi. Sana va vaqtlar ishga tushirilgan vaqtga nisbatan hisoblanadi. Uchta test login — `rais`, `boshliq`, `xodim`; tasodifiy parol terminalda ko‘rsatiladi. Boshqa namuna xodimlarining paroli mavjud emas; rais struktura orqali ularni belgilashi mumkin. Qayta ishga tushirish mavjud yozuvlar yoki parollarni o‘zgartirmaydi. Namuna F.I.Sh.larining ayrimlari dizayndagi initsiallar asosida tuzilgan, tashkilotning tasdiqlangan xodimlar reyestri emas.

Admin kerak bo‘lsa: `python manage.py createsuperuser`. Oddiy rais hisobi Django admin huquqiga ega emas. Production uchun namuna bazani ishlatmang: yangi baza va haqiqiy foydalanuvchilar yarating. Rais rolini superuser `/admin/` orqali belgilaydi.

## Ishlaydigan funksiyalar

- Login/logout, parolni o‘zgartirish, xodim parolini tiklash va bloklash.
- Rais: tashkilot bo‘yicha ko‘rish, topshiriq berish, qarorlar, bo‘linma/xodim boshqaruvi.
- Bo‘lim boshlig‘i: o‘z bo‘linmasidagi topshiriqlar, o‘z xodimlariga topshiriq berish, o‘ziga berilgan vazifani taqsimlash.
- Devonxona mudiri: kelgan xat bo‘yicha **faqat bo‘lim boshliqlariga** topshiriq beradi va o‘zi bergan topshiriqlar ijrosini kuzatadi. Boshqa bo‘limlarning ichki topshiriqlarini ko‘rmaydi.
- Kotiba: butun tashkilot bo‘yicha topshiriqlar va «Xodimlar» statistikasini **ko‘radi**, lekin topshiriq bermaydi va qaror qabul qilmaydi. Nazoratchi qilib belgilansa, izoh yozadi va hisobot so‘raydi.
- Xodim: faqat o‘z topshiriqlari, izoh, ijro, haftalik hisobot, muddat uzaytirish so‘rovi.
- Xat rekvizitlari: topshiriqqa xat raqami, sanasi va kimdan kelgani yoziladi; ular topshiriq sahifasida ko‘rinadi.
- Fayl biriktirish: topshiriqqa 25 MB gacha hujjat (PDF, Word, Excel, rasm, arxiv) biriktiriladi, bittasiga 20 tagacha. Fayllar bazada emas, S3/MinIO’da saqlanadi (`S3_ENDPOINT_URL` sozlansa); havolalar imzolangan va 15 daqiqa amal qiladi. Sozlanmagan bo‘lsa, fayllar serverdagi `media/` papkasida turadi. Faylni topshiriq ishtirokchilari biriktiradi, o‘chirishni esa yuklagan shaxs yoki rahbar bajaradi.
- Nazoratchi: rahbar topshiriqqa nazoratchi belgilaydi. U topshiriqni ko‘radi, izoh yozadi va haftalik hisobot so‘raydi, lekin ijroni qabul qilmaydi, muddat o‘zgartirmaydi va ijro topshirmaydi. Bo‘lim boshlig‘i o‘z bo‘limi xodimini, kotibani yoki devonxonani; rais va devonxona esa istalgan xodimni nazoratchi qila oladi.
- Faol, kechikkan, muddati yaqin, tasdiq kutilayotgan, muddatsiz va qabul qilingan vazifalar filtrlari; matn va xodim bo‘yicha qidirish.
- Qo‘shimcha ijrochilar: topshiriqni bergan rahbar (yoki rais) mavjud topshiriqqa xodim qo‘shib, unga aniq ijro qismini biriktiradi. Qo‘shimcha ijrochi topshiriqni ko‘radi, izoh va hisobot yozadi hamda **faqat o‘z qismini** topshiradi; rahbar shu qismni qabul qiladi yoki sabab bilan qaytaradi. Barcha qismlar qabul qilinmaguncha asosiy ijrochi topshiriq ijrosini topshira olmaydi. Xodim faqat yangi topshiriq berish doirasidan tanlanadi; asosiy ijrochi, topshiriq bergan shaxs va rais qo‘shilmaydi.
- Ijro topshirish → rahbarning qabul qilishi yoki majburiy sabab bilan qaytarishi.
- Muddat so‘rovi → tasdiqlash/rad etish, quyi va yuqori muddatlar mosligini tekshirish.
- Muddatsiz vazifalar 14 kundan keyin diqqat talab qiladigan vazifalarda ham ko‘rinadi.
- Xabarnomalar, xronologik tarix va ko‘p darajali delegatsiya zanjiri.
- «Xodimlar» sahifasida statistika jadvali: topshiriqlar soni, bajarilmoqda (jarayonda yoki tasdiq kutilmoqda), bajarilmagan (muddati o‘tgan) va tanishilmagan (ijrochi hali ochmagan) topshiriqlar. Hafta tanlansa, shu haftada berilgan topshiriqlar hisoblanadi; jadval Excel (`.xlsx`) sifatida yuklab olinadi. Rahbar faqat o‘ziga ko‘rinadigan topshiriqlar statistikasini ko‘radi.
- Responsive sahifalar, klaviatura bilan ishlaydigan tafsilotlar paneli va tungi rejim.

Huquqlar faqat menyu bilan cheklanmaydi: barcha POST amallar serverda tekshiriladi. Quyi vazifalari yopilmagan asosiy vazifa ijroga topshirilmaydi. Xodim o‘z ijrosini tasdiqlay olmaydi. Quyi topshiriq muddati barcha yuqori vazifalar muddatidan kech bo‘lishi mumkin emas. Faol topshiriqlari bo‘lgan xodim boshqa bo‘linmaga ko‘chirilmaydi. Tarix UI va Django admin orqali tahrirlanmaydi; baza administratori darajasida kriptografik o‘zgarmaslik da’vo qilinmaydi.

## Ovozli yordamchi: Muxlisa AI + OpenAI

**STT va TTS — Muxlisa AI; LLM — OpenAI.** Global agent barcha autentifikatsiyalangan sahifalarda ishlaydi. Oqim: brauzerda yozish → Django → Muxlisa transkripsiyasi → OpenAI Responses function calling → rolga mos Django funksiyalari → sahifa ochish yoki amal loyihasini tasdiqlash → Muxlisa ovozli javobi. Barchasi shu Django monolit ichida.

Agent topshiriqlarni qidiradi, sanaydi, tafsilot/tarix, xabarnoma va xodimlarni o‘qiydi; filtrlangan ro‘yxat yoki kerakli sahifani ochadi. Topshiriq yaratish/taqsimlash, izoh, ijro topshirish/qabul qilish/qaytarish, muddatni belgilash/uzaytirish/tasdiqlash/rad etish va hisobot amallari mavjud. Rais uchun xodim yaratish/tahrirlash/bloklash/faollashtirish, bo‘linma yaratish/nomini o‘zgartirish ham mavjud. Topshiriq mazmuni va ijrochisini boshqaruvchi tahrirlaydi. Xabarnomalarni o‘qilgan deb belgilash mumkin. Barcha o‘zgarishlar avval ko‘rib chiqish uchun tayyorlanadi, keyin tasdiqlanadi. Parollar faqat agent ochadigan xavfsiz formada kiritiladi; yangi hisob parol belgilanguncha kira olmaydi. Ma’lumot o‘chirish amali mavjud emas.

Misollar: «Kechikkan topshiriqlarni ko‘rsat», «T-104 ni och», «Xalimovga loyiha hisobotini ertaga 18:00 gacha tayyorlashni topshir», «Shu topshiriq bo‘yicha hisobot so‘ra».

Sahifa ochish darhol bajariladi; yozuvchi amallar ism, vazifa, matn va aniq muddat bilan oldindan ko‘rsatiladi. «Tasdiqlayman» yoki tasdiqlash tugmasi 10 daqiqalik loyihani bajaradi. O‘zgargan topshiriq qayta tekshiriladi; takroriy tasdiq bir amalni takrorlamaydi. Modelda tasdiqlash yoki ixtiyoriy URL/SQL bajarish funksiyasi yo‘q.

Suhbat serverda shu foydalanuvchiga bog‘lanadi; modelga oxirgi 10 almashinuv yuboriladi. Sahifa almashganda davom etadi. Sahifani ko‘rish uchun panel ixchamlashadi; tarix tugmasi uni kengaytiradi. Formada saqlanmagan matn bo‘lsa, avtomatik o‘tish o‘rniga sahifa havolasi ko‘rsatiladi. Rol yoki ko‘rish huquqi o‘zgarsa, oldingi suhbat konteksti tozalanadi. «Yangi suhbat» suhbat yozuvlari va loyihalarini tozalaydi, yaratilgan topshiriqlar qoladi.

OpenAI kalitisiz faqat kodda ko‘rsatilgan aniq sahifa ochish iboralari ishlaydi; UI bu cheklovni ko‘rsatadi. Erkin muloqot, qidirish/tahlil va vazifa amallari OpenAI kalitini talab qiladi.

`.env.example` asosidagi `.env` faylida quyidagilarni kiriting:

```dotenv
MUXLISA_API_KEY=
OPENAI_API_KEY=
OPENAI_TASK_MODEL=gpt-5.6-sol
OPENAI_TASK_REASONING=low
# Muxlisa ovozi: 0 — ayol, 1 — erkak.
MUXLISA_SPEAKER=0
```

LLM uchun GPT-5.6 Sol `low` reasoning bilan ishlaydi. Suhbat agenti va topshiriq loyihasi bir xil model sozlamasidan foydalanadi. Holat bo‘yicha so‘rovlar ro‘yxatni ochadi; «Men muddatsiz topshiriqlar dedim» kabi tuzatishlar joriy xodim filtrini saqlaydi. API javoblari `store=False`; ko‘p qadamli tool chaqiruvlarida reasoning javobi shu so‘rov ichida saqlanadi.

Kalitlarni kiritgandan so‘ng `serve.py` jarayonini qayta ishga tushiring. Kalitlar Git’dan chiqarilgan `.env`da qoladi; HTML/JavaScript/API javoblarida qaytarilmaydi. Muxlisa kaliti `x-api-key` sarlavhasida yuboriladi va hisobda STT hamda TTS uchun mablag‘ bo‘lishi kerak: STT soniya, TTS belgilar bo‘yicha hisoblanadi. OpenAI kalitida tanlangan modeldan foydalanish huquqi va API balansi bo‘lishi kerak. Ovoz provayderining kaliti OpenAI kaliti o‘rnida ishlamaydi va OpenAI serveriga yuborilmaydi.

**Ovozli kirish:** Login qilgan foydalanuvchi ovoz namunasi yozmasdan STT va jonli suhbatdan foydalanadi. Ovoz egasini tekshirish va uning sozlash sahifasi olib tashlangan. Avvalgi shifrlangan profillar hozir ishlatilmaydi.

**Jonli suhbat:** Agent panelidagi «Jonli suhbat»ni bir marta bosing. Mikrofon ochiq qoladi; 1,8 soniyalik sukut gapingiz tugaganini bildiradi. Keyingi gap uchun tugmani yana bosish kerak emas. Gap tugagach agent javobi to‘xtaydi va buyruq tahlilga yuboriladi. Agent sahifani ochganda sahifa mazmuni almashadi, mikrofon va suhbat davom etadi. Panelni yopish, «Suhbatni tugatish», boshqa tabga o‘tish yoki sahifani to‘liq qayta yuklash mikrofonni o‘chiradi.

- AudioWorklet ovozni 16 kHz mono PCM16 oqimiga aylantiradi. 640 ms oldingi audio gap boshini saqlaydi; har gap 28 soniyagacha. Silero v5 nutq ehtimoli va moslashuvchi ovoz balandligi chegarasi birga tekshiriladi. Oddiy nutq kamida 96 ms, agent gapirayotgandagi aralashish 256 ms davom etishi kerak. Sukut/nutq chegarasi 1,8 soniya; qisqa o‘ylash pauzasi buyruqni bo‘lib yubormaydi. Past ovoz faqat balandligi kamligi sababli fon shovqini sifatida o‘rganilmaydi.
- Erkin suhbat saqlanadi, murojaat so‘zi talab qilinmaydi. Echo cancellation, noise suppression, auto gain, 100–7000 Hz filtr va mahalliy neural VAD ishlaydi. Ovoz egasi tekshirilmaydi; yaqin atrofdagi suhbat ham nutq sifatida qabul qilinishi mumkin.
- Neural model alohida Web Worker ichida ishlaydi. ONNX Runtime 1.22.0 va Silero v5 fayllari shu Django static katalogidan yuklanadi; uchinchi tomon CDN so‘rovi va audio yuborilishi yo‘q. Model hamda WASM birinchi yuklanishda taxminan 14 MB. Versiyalar, SHA-256 va litsenziyalar `static/vendor/voice-filter-v1/`da; qayta olish: `python scripts/vendor_voice_filter.py`. Model ishlamasa, himoya sezdirmasdan o‘chirib qo‘yilmaydi — xabar chiqadi va mikrofon yopiladi.
- Brauzer o‘z Django serveriga WebSocket orqali ulanadi. Bir martalik 60 soniyalik ticket foydalanuvchi sessiyasi va Origin bilan bog‘langan. Audio Django’da to‘planadi va Muxlisa’ga server tomonidan yuboriladi; Muxlisa kaliti brauzerga chiqmaydi.
- Audio avval Django xotirasida yig‘iladi va mahalliy ovoz tekshiruvidan o‘tadi. Faqat tasdiqlangan audio Muxlisa’ga yuboriladi. Transkript gap tugagach qaytadi; oraliq so‘zlar ko‘rsatilmaydi, chunki provayderda oqimli STT yo‘q. OpenAI funksiyalarni bajarib javob tayyorlaydi. TTS 24 kHz PCM bo‘laklari kelishi bilan o‘qiladi. Ovoz tekshiruvi, STT, LLM va tarmoq tezligi javob kechikishiga ta’sir qiladi.
- Muxlisa’da oqimli (realtime) STT yo‘q: har bir gap tugagach, server o‘sha PCM’ni WAV qilib sinxron STT’ga bir marta yuboradi. Provayderda idempotentlik kaliti yo‘q, shuning uchun avtomatik qayta urinish yo‘q: xato bo‘lsa, qayta urinishni foydalanuvchi bosadi va bu alohida hisoblanadi. Kredit, ruxsat va limit xabarlari alohida ko‘rsatiladi. Uchta ketma-ket xatodan keyin mikrofon o‘chiriladi.
- Yangi gap boshlanganida eski ovoz oqimi bekor qilinadi. Bajarilishi boshlangan server amali natijasi saqlanadi; eski javob yangi gapdan keyin o‘zicha sahifa ochmaydi. Yozuvchi amallar avvalgidek alohida tasdiq talab qiladi.

- Mikrofon faqat foydalanuvchi bosganda yoqiladi. Oddiy «Gapirish» yozuvi uchun `MediaRecorder`, jonli rejim uchun AudioWorklet ishlatiladi. Web Speech API’ga bog‘liqlik yo‘q. Mikrofon uchun localhost yoki HTTPS kerak.
- Yozuv 29 soniyada avtomatik to‘xtaydi (Muxlisa sinxron STT limiti 60 soniya). 5 MB gacha WebM, WAV, MP3, M4A/AAC, OGG, FLAC faylini ham tanlash mumkin.
- Yozuv `202` bilan navbatga olinsa, Django foydalanuvchiga bog‘langan imzoli havola orqali natijani tekshiradi. UI 3 daqiqagacha kutadi; «Qayta urinish» mavjud job holatini tekshiradi. Panelni yopish mikrofon va ovozli javobni to‘xtatadi, provayder qabul qilgan job’ni bekor qilmaydi.
- STT va TTS qayta urinishlarida bir xil idempotency kaliti ishlatiladi. OpenAI SDK avtomatik qayta urinishlari o‘chirilgan. Suhbat agenti faqat provayderning 5xx xatosida javob yaratishni bir marta takrorlaydi; bajarilgan amallar qayta bajarilmaydi. Butun model sikli 85 soniya bilan chegaralangan. Autentifikatsiya, limit va noaniq tarmoq xatolari avtomatik takrorlanmaydi.
- Noaniq ijrochi/muddat aniqlashtiriladi. Sheva/sleng/ruscha aralash gapni rasmiy o‘zbekcha vazifaga aylantirish so‘raladi, lekin aniqlik kafolati berilmaydi. Transkripsiya matnini qo‘lda tahrirlash mumkin.
- Xodim ism, familiya yoki to‘liq nomi bilan topiladi: apostroflar, kirill yozuvi va o‘zbekcha kelishik qo‘shimchalari hisobga olinadi. Masalan, `Azamovni`, `A’zamov`, `Azizning`. Bir xil ismli xodimlar yoki yaqin yozilishlar bo‘lsa, agent aniqlashtiradi. Qidiruv faqat rol bo‘yicha ko‘rinadigan faol xodimlarda ishlaydi. STT butun ismni yo‘qotsa, u taxmin bilan tiklanmaydi.
- Faktli javoblarni `core/agent_replies.py` serverda tuzadi. OpenAI `tool_choice=required` bilan qidiruv vositasi, shu so‘rovda olingan `source_id` va javob yo‘nalishini tanlaydi; erkin model matni ekranga ham, TTS’ga ham uzatilmaydi. Model vosita o‘rniga matn qaytarsa, tekshirilgan natija yoki aniqlashtirish savoli beriladi. Nomzodlar ro‘yxati, ijrochi, muddat, holat va sonlar bazadagi natijadan olinadi. Bu modelning niyatni har doim to‘g‘ri tushunishini kafolatlamaydi; noaniq gap uchun aniqlashtirish kerak.
- Eski tekshirilmagan agent javoblari tarixda belgilab ko‘rsatiladi, lekin model kontekstiga dalil sifatida qayta berilmaydi va eski javob ovozi qayta taklif qilinmaydi. Har bir yangi javob manbasi faqat joriy so‘rovga tegishli. Noaniq yangi gap eski sahifa ochish buyrug‘ini qayta ishga tushirmaydi; tasdiqlangan nomzod tanlovi esa avvalgi ochish so‘rovini davom ettirishi mumkin. Xodim bo‘yicha ochilgan sahifa filtri agent kontekstida server tomonidan tekshiriladi.
- Agent so‘rovga qarab sahifa yaratadi: «kechikishlar bo‘yicha sahifa tayyorla» desangiz, avval ma’lumot vositasini, keyin sahifa ko‘rinishini (sarlavha, matn, `<style>`) yozadi va `/pages/` bo‘limida saqlaydi. Sahifa faqat yaratgan foydalanuvchiga ko‘rinadi, har ochilganda ma’lumot o‘sha foydalanuvchi huquqi doirasida qayta hisoblanadi. Sonlar modeldan emas: ko‘rinishda faqat `{{table}}`, `{{chart}}`, `{{total}}`, `{{title}}`, `{{generated_at}}` o‘rin egalari to‘ldiriladi va kamida bittasi bo‘lishi shart. Saqlashdan oldin HTML tozalanadi (skript, rasm, havola, forma, `url(...)` olib tashlanadi), sahifa esa `sandbox` va `default-src 'none'` bilan alohida ramkada ko‘rsatiladi — shuning uchun topshiriq matniga yashiringan buyruq sahifa orqali ma’lumot chiqara olmaydi. Bir foydalanuvchida 20 tagacha sahifa; bir xil nom eskisini almashtiradi.
- Tayyor vositalar yetmaydigan tahlil/hisob so‘rovlari uchun agent suhbat ichida yangi `dyn_*` vositasini yaratib, shu zahoti chaqiradi (`core/dynamic_tools.py`). Vosita — Python/SQL emas, cheklangan deklarativ qadamlar: `query_tasks`, `filter_rows`, `group_rows`, `sort_rows`, `select_rows`, `calculate_rows`, mavjud o‘qish vositalari va oxirgi qadamda bitta `prepare_*` (faqat tasdiqlash uchun loyiha). Har qadam joriy foydalanuvchi huquqlari bilan bajariladi; 5000 tadan ortiq topshiriq bo‘lsa xato qaytadi, modelga ko‘pi bilan 40 qator yuboriladi. Bir suhbatda 10 tagacha vosita saqlanadi; ko‘rish so‘rovlarida yozuvchi vositalar yashiriladi; rol yoki bo‘linma o‘zgarsa vositalar ham tozalanadi.
- AI yozuvchi amallar uchun faqat loyiha tayyorlaydi. Tasdiqda mavjud rol, ijrochi va yuqori/quyi muddat qoidalari qayta tekshiriladi. Ovoz odatda tanilgach yuboriladi; «Eshitilgan matnni yuborishdan oldin tekshirish» rejimida qo‘lda tuzatib yuboriladi.
- Agent javobi avtomatik ovozda o‘qiladi; checkbox bilan o‘chirish, to‘xtatish yoki «Tinglash» orqali qayta tinglash mumkin. Avtomatik ijroni brauzer bloklasa, audio pleyer chiqadi. TTS ishlamasa matnli javob saqlanadi.
- TTS ovozi `MUXLISA_SPEAKER` bilan tanlanadi: `0` — ayol, `1` — erkak. Ovoz katalogi yo‘q. Javob matni 1000 belgidan uzun bo‘lsa, ovoz uchun qisqa variant olinadi, to‘liq matn ekranda qoladi.
- Ovozli nusxada Markdown belgilar olib tashlanadi; topshiriq kodi, sana, soat va sanoq sonlar o‘zbekcha so‘zlarga aylantiriladi. IT/ERP kabi qisqartmalar talaffuzga tayyorlanadi va ro‘yxat bandlari orasiga tinish belgisi qo‘yiladi. Ekrandagi asl matn saqlanadi. Oqimli ijro birinchi audio kelgan vaqtdan 450 ms bufer bilan boshlanadi; bu qisqa tarmoq kechikishlarida bo‘laklar orasidagi sun’iy pauzalarni kamaytiradi.
- Muxlisa TTS oqim bilan kelmaydi: server WAV’ni to‘liq olib, so‘ng brauzerga PCM bo‘laklarini uzatadi, shuning uchun uzun javobda ijro biroz kechroq boshlanadi. Namuna chastotasi WAV sarlavhasidan olinadi. Xato bo‘lsa avtomatik takrorlanmaydi; ruxsat, mablag‘, limit va format xatolari alohida ko‘rsatiladi. To‘xtatish so‘rovni bekor qiladi.
- Xom audio faqat Muxlisa’ga yuboriladi. OpenAI eshitilgan matn, suhbat konteksti va vositalar orqali foydalanuvchiga ruxsat etilgan topshiriq, tarix hamda xodim ma’lumotlarini oladi (`store=False`). TTS uchun agent javobining matni Muxlisa’ga yuboriladi. Muxlisa audio/transkripsiyani o‘z tarixida saqlashi mumkin.
- Agent va ovoz endpointlari faol login va POST uchun CSRF bilan himoyalangan. Ovozdan barcha rollar foydalanadi, har bir topshiriq amali mavjud rol qoidalarini tekshiradi. TTS faqat shu foydalanuvchiga server bergan imzoli agent javobini o‘qiydi. Audio/job ticket va javoblar 15 daqiqa amal qiladi. Pullik so‘rovlar bir foydalanuvchi uchun daqiqasiga 24 tagacha.

Hozirgi monolit bitta Uvicorn jarayonida ishlaydi. Ko‘p jarayonga deploy qilinganda rate limiting, bir martalik ticket nazorati va katalog keshi uchun umumiy Django cache backend sozlang.

`core/test_voice.py` va `core/test_agent.py` haqiqiy OpenAI SDK hamda sun’iy HTTP javoblari orqali tekshiradi: vositalar zanjiri, sahifa ochish, suhbat izolyatsiyasi, tasdiqlash/idempotency, eskirgan loyiha, STT/LLM/TTS ajratilishi, kalitlar, xatolar, CSRF, rol chegaralari, muddatlar va audio format. Bu testlar haqiqiy ovoz sifati yoki hisob kreditlarini tasdiqlamaydi. Jonli sinov uchun kalitlar, mavjud model/ovoz va foydalanuvchining nutq misollari kerak.

`core/test_realtime.py` sessiya/Origin/CSRF, ticket qayta ishlatilishi, upstream protokoli, audio oqimi, REST fallback va uzilishni tekshiradi. `node --test tests/voice-realtime.test.cjs tests/voice-neural.test.cjs` gap chegarasi, 44,1/48 kHz resampling, PCM ijrosi, qayta ulanish, javobni to‘xtatish va haqiqiy Silero modelida sukut/shovqin/sun’iy o‘zbekcha nutqni tekshiradi. Node faqat shu testlar uchun kerak.

2026-09-18 lokal tekshiruv: 98 Python va 10 JavaScript testi o‘tdi. Haqiqiy Muxlisa AI realtime TTS → sun’iy o‘zbekcha audio → Django WebSocket relay → Muxlisa AI STT → OpenAI topshiriq qidiruvi → Django orqali oqimli TTS ishladi. OpenAI erkin iboradan filtrlangan sahifa havolasini ham qaytardi. Brauzerda jonli mikrofon faol qolgan holda topshiriq sahifasi almashishi tekshirildi. Bu sinovlar barcha shevalar, shovqinli muhit yoki production yuklamasi uchun sifat kafolati emas.

So‘nggi tuzatishda 112 Python va 16 JavaScript testi o‘tdi. Haqiqiy OpenAI `Azamovni` va `Azizning` so‘rovidan A’zamov Azizning ro‘yxatini, `Akmalning` so‘rovidan Kamolov Akmalning ro‘yxatini ochdi. `Sherzod` uchun uch nomzodni aniqlashtirdi va keyingi tanlovni tushundi. Buzilgan `Bana unaqa topshiriqlarni och` matnidan xodim taxmin qilinmadi. Haqiqiy Muxlisa AI REST STT tayyor `Shu topshiriq kimga berilgan?` namunasini to‘g‘ri tanidi. TTS ismli namuna uchun WAV yaratdi, ammo uning STT job’i `stt_overloaded` (`scheduler`) bilan tugadi. Ilova bu holatni xizmat bandligi sifatida ko‘rsatadi; tugagan job’ni qayta so‘rash o‘rniga foydalanuvchining aniq qayta urinishida yangi so‘rov yuboradi. Bu natijalar foydalanuvchining haqiqiy mikrofon yozuvi yoki shevasidagi aniqlikni o‘lchamaydi.


Manbalar: [Muxlisa Sync API](https://muxlisa.uz), [OpenAI Responses API](https://platform.openai.com/docs/api-reference/responses). Integratsiya tafsilotlari: [docs/muxlisa-integration.md](docs/muxlisa-integration.md).

To‘qima javob tuzatishidan keyin 125 Python testi o‘tdi. Qo‘shimcha tekshiruvlar soxta xodim/son, mavjud bo‘lmagan yoki oldingi so‘rovdan olingan manba, eski tekshirilmagan tarix va ruxsatsiz filtrlarni qamrab oladi. Haqiqiy OpenAI bilan foydalanuvchining muammoli suhbat nusxasida `O‘zing so‘rayver` aniqlashtirishga olib keldi; `Azamovni topshiriqlarini och` haqiqiy A’zamov ro‘yxatini ochdi; ro‘yxat soni 1 ta qaytdi; `Sherzod` uchun faqat bazadagi uch nomzod ko‘rsatildi. Sinovda foydalanuvchining asl suhbat tarixi o‘zgartirilmadi.

Nutq filtri: [Silero VAD](https://github.com/snakers4/silero-vad), [ONNX Runtime Web](https://onnxruntime.ai/docs/tutorials/web/env-flags-and-session-options.html). V5 audio/state/context formati `@ricky0123/vad-web` 0.0.31 paketidagi model adapteri bilan tekshirildi.

## Eslatmalar

```powershell
.\.venv\Scripts\python.exe manage.py send_reminders
```

Bu buyruq muddat yaqinlashishi, buzilishi, zanjir bo‘yicha eskalatsiya va eskirgan muddatsiz vazifalar uchun **ilova ichidagi** xabarlarni yaratadi. Bir kunda takroriy ishga tushirish xabarlarni ko‘paytirmaydi. Doimiy ishlash uchun server scheduler/Windows Task Scheduler’da har soat ishga tushiring. Tizimda tashqi email/SMS/Telegram integratsiyasi yo‘q.

## Tekshirish

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
```

## Serverga joylash

1. `requirements-production.txt`ni o‘rnating; PostgreSQL bazasi va alohida foydalanuvchi yarating.
2. `.env.example`dan `.env` yarating. `DJANGO_DEBUG=0`, `DJANGO_SECRET_KEY`, domen uchun `DJANGO_ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` va DB qiymatlarini sozlang.
3. `python manage.py migrate` va `python manage.py collectstatic --noinput`.
4. `python manage.py createsuperuser`; admin orqali rais hisobi yarating.
5. `python serve.py --host 127.0.0.1 --port 8000`ni servis sifatida ishlating. Oldida HTTPS reverse proxy (Nginx/Caddy/IIS) bo‘lsin. `/voice/live-stream/` uchun WebSocket Upgrade, sessiya cookie va Origin headerlari o‘tsin. `/voice/realtime-speak/` javobini proxy buferlamasin; ulanish timeouti kamida 180 soniya bo‘lsin. `TRUST_PROXY_HTTPS=1`ni faqat proxy kiruvchi `X-Forwarded-Proto` headerini tozalab o‘zi yozsa yoqing.
6. `python manage.py check --deploy`, DB backup, login rate limiting (reverse proxy darajasida), log monitoring va `send_reminders` schedulerini sozlang.

Production HTTPS cookie/redirect/HSTS sozlamalari `DEBUG=0`da yoqiladi. Static fayllarni WhiteNoise beradi. PostgreSQL va haqiqiy HTTPS serverga deploy lokal tekshiruv doirasiga kirmaydi.

### Agentning sahifa konteksti

Agent har so‘rovda ruxsatli sahifaning asosiy matni va havolalarini serverdan qayta o‘qiydi: panel, topshiriqlar, tafsilot, xodimlar, struktura, zanjir, tarix, xabarnomalar va yaratish/tahrirlash formalari. Joriy ro‘yxat UI bilan bitta filtr funksiyasidan olinadi. Bitta topshiriq bo‘lsa “joriy sahifadagi topshiriqni och” uni ochadi; ko‘p bo‘lsa haqiqiy nomzodlardan tanlov so‘raladi. Brauzerda saqlanmagan forma qiymatlari yoki parollar modelga yuborilmaydi. Sahifa matni 24 000 belgi bilan chegaralangan; batafsil ma’lumot alohida o‘qish vositalaridan olinadi.
