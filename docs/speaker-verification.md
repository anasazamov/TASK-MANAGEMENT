# Foydalanuvchining ovozini tekshirish

Ovoz namunasi yo‘q hisob login’dan so‘ng `/account/voice/` sahifasiga yo‘naltiriladi.
Har foydalanuvchi uchta 8 soniyalik yozuvni o‘zi yozadi va foydalanishga rozilik
beradi. Matnli agentdan namunani yozmasdan ham foydalanish mumkin. Mavjud hisoblar
uchun topshiriqlar sahifasida ham sozlash havolasi chiqadi.

Ovoz namunasi faqat autentifikatsiya qilingan hisobga bog‘lanadi. `VoiceProfile`
jadvalida uchta 192 o‘lchamli embedding Fernet bilan shifrlanadi. Kalit Django
`SECRET_KEY`dan ajratiladi; ushbu server siri almashtirilsa, ovozni qayta tanitish
kerak. Namuna audiosi ilova tomonidan saqlanmaydi yoki tashqi API’ga yuborilmaydi.
Namuna qayta yozilganda uning versiyasi yangilanadi; o‘chirish faqat joriy hisobga
ta’sir qiladi. Eski jonli ulanish va STT ticketlari boshqa versiyaga o‘tmaydi.

## Ishga tushirish

```powershell
.venv/Scripts/python.exe -m pip install -r requirements.lock.txt
.venv/Scripts/python.exe scripts/setup_speaker_model.py
.venv/Scripts/python.exe manage.py migrate
.venv/Scripts/python.exe manage.py collectstatic --noinput
```

Model `private_models/` ichida, web static katalogidan tashqarida turadi. Yuklash
skripti model SHA-256 qiymatini tekshiradi. Ilova ish vaqtida model yuklab olmaydi.
Silero VAD fayli mavjud `static/vendor/voice-filter-v1/` katalogidan olinadi.

Model: `3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx`,
SHA-256: `aa3cfc16963a10586a9393f5035d6d6b57e98d358b347f80c2a30bf4f00ceba2`.
Runtime: sherpa-onnx 1.13.8, CPU, ikki oqim.
[Rasmiy speaker-identification hujjati](https://k2-fsa.github.io/sherpa/onnx/speaker-identification/index.html),
[model va ochiq sinov yozuvlari](https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models).

## Buyruq yo‘li

1. Brauzer audio yozuvni 16 kHz mono PCM16 ga aylantiradi. Jonli audio Django
   xotirasida to‘planadi; fayl va oddiy mikrofon yozuvi ayni formatdagi WAV bo‘ladi.
2. Mahalliy Silero nutq qismlarini ajratadi. Juda qisqa, sust yoki buzilgan ovoz
   qayta yozishni talab qiladi. Namuna uchun kamida 3 soniya, buyruq uchun 1,2
   soniya nutq kerak.
3. CAMPPlus umumiy embedding va kesishuvchi 2,5 soniyalik bo‘laklarni namunaning
   markazi bilan solishtiradi. Hozirgi chegaralar: umumiy 0,65; har bo‘lak 0,60.
4. Faqat mos kelgan audio VoiceLab STT’ga yuboriladi. Mos kelmagan ovoz OpenAI’ga
   buyruq bermaydi va agentning ovozli javobini to‘xtatmaydi. Jonli mikrofon
   keyingi gapni tinglashda davom etadi.

Mijoz uzilsa tekshiruvdan keyingi provayder chaqiruvi bekor qilinadi. Model yoki
namuna ishlamasa tekshiruv o‘tkazib yuborilmaydi. Login va topshiriq huquqlari
avvalgi Django mexanizmlari bilan tekshiriladi; bu parol o‘rnidagi autentifikatsiya
yoki qayta ijro qilingan/sun’iy ovozga qarshi liveness tekshiruvi emas.

## Tekshiruv — 2026-09-18

- 143 Django testi: login yo‘nalishi, rozilik, CSRF, hisob doirasi, shifrlash,
  yozuvni yangilash/o‘chirish, audio formatlari, ikki ovoz aralashishi va STT’dan
  oldingi bloklash; mavjud topshiriq va voice testlari ham o‘tdi.
- 19 JavaScript testi: mavjud VAD, PCM, qayta ulanish va ovozni to‘xtatish testlari,
  boshqa gapiruvchi nutqini buyruqsiz rad qilish va WAV formati o‘tdi.
- Haqiqiy mahalliy model bilan uchta ochiq yozuvdan vaqtinchalik hisobga namuna
  yaratish muvaffaqiyatli bo‘ldi. Mos ovoz qabul qilindi; boshqa gapiruvchining ikki
  yozuvi, ketma-ket gapiruvchi almashishi, sukut va qisqa nutq rad etildi: 6/6.
  Tekshirish 0,10–0,51 soniya, uchta namunani saqlash taxminan 3,15 soniya oldi.
- Ikki ovozni bir vaqtda aralashtirish bo‘yicha qo‘shimcha mahalliy tekshiruvda
  boshqa ovozning targetga nisbati 0,5, 1 va 1,5 bo‘lgan uch holat ham tanlangan
  chegaralardan o‘tmadi.
- Brauzerda mavjud foydalanuvchiga sozlash taklifi, ovoz tugmalarining namunagacha
  bloklanishi va uch bosqichli yozish sahifasi tekshirildi. Haqiqiy foydalanuvchi
  uchun sinov audio yozuvlari bilan namuna yaratilmagan.

Bu kichik texnik sinov, o‘zbekcha real ofis audiosidagi aniqlik o‘lchovi emas.
Bir-biriga o‘xshash ovoz, juda past fon suhbati va ustma-ust gaplashishda xatolik
qolishi mumkin. Mikrofon masofasi va foydalanuvchining haqiqiy namunasi bilan
sinash lozim; moslik chegarasini pasaytirish boshqa ovozni qabul qilishni oshiradi.
