# VoiceLab va Django ovozli yordamchi

Tekshiruv: 2026-09-17. Manbalar — login qilingan Developer sahifasi va rasmiy VoiceLab hujjatlari. Hozircha hujjatlar o‘rganildi; VoiceLab API bilan audio sifati, tezlik yoki topshiriq ajratish aniqligi sinovdan o‘tkazilmadi. Ushbu yozuv integratsiya amalga oshirilganini anglatmaydi.

Keyingi qaror: foydalanuvchi STT/TTS uchun VoiceLab, LLM uchun OpenAI’ni tanladi. Shu integratsiya kodi qo‘shildi; amaldagi sozlash va testlar [README](../README.md)da. Quyidagi yagona provayder haqidagi tahlil tadqiqot paytidagi variant bo‘lib qoladi.

## Xulosa

VoiceLab texnik jihatdan Django monolitga mos. O‘zbekcha transkripsiya, LLM function calling va ovozli javobni bir provayder orqali ishlatish mumkin. Avval STT va topshiriq ajratishni haqiqiy sheva/sleng misollari bilan tekshirish kerak. Hujjatlar OpenAI’dan yaxshiroq aniqlikni yoki barcha shevalar uchun kafolatni bermaydi.

## Ovoz → matn

[STT hujjati](https://docs.voicelab.uz/api/stt)

- `POST https://api.voicelab.uz/v1/stt`.
- `Authorization: Bearer ...`; har yangi audio uchun UUID shaklidagi `Idempotency-Key`.
- Multipart: `audio`, `language=uz`, `include_speakers=false`.
- MP3, WAV, M4A/AAC, OGG/Opus, WebM/Opus, FLAC qo‘llanadi. Codec ham tekshiriladi.
- Sinxron yo‘l: 0.5–30 soniya, ko‘pi bilan 10 MiB; natijada `transcript` qaytadi.
- 30 soniyadan uzun audio: uzun yozuv xizmati yoqilgan bo‘lsa `202` va job ID; natijani `GET /v1/stt/transcriptions/{id}` orqali kutish kerak. Xizmat mavjudligi hisobda alohida tekshiriladi.
- Bir xil yozuvni timeoutdan keyin qayta yuborishda ayni idempotency kaliti saqlanadi.
- Audio/transkripsiya tarixga saqlanishi mumkin. Maxfiylik matni bunga mos bo‘lishi kerak.
- Ko‘rsatilgan STT maydonlari orasida maxsus lug‘at yoki familiyalarni `prompt` orqali berish parametri yo‘q; bunday parametrni taxminan qo‘shmaslik kerak.

## Matn → topshiriq maydonlari

[LLM hujjati](https://docs.voicelab.uz/api/llm)

- Modellar: `GET /v1/models`; hisobdagi amaldagi model ID va narx shu katalogdan olinadi.
- Tahlil: `POST /v1/chat/completions`, `messages`, `tools`, nomlangan `tool_choice`.
- `draft_task` funksiyasi orqali ijrochi, mazmun, tavsif, muddat va aniqlashtirishni olish mumkin. Bu loyiha uchun taklif; tayyor VoiceLab funksiyasi emas.
- Funksiya argumentlarini bajarish va tekshirish Django zimmasida. Model tanlagan ijrochi foydalanuvchining ruxsat etilgan ro‘yxatiga kirishi, muddat esa mavjud qoidalardan o‘tishi kerak.
- `response_format`/OpenAI Responses API bilan to‘liq moslik hujjatlashtirilmagan. Mavjud OpenAI `responses.parse` chaqiruvini faqat base URL o‘zgartirib ulash to‘g‘ri emas.
- LLM idempotency STT’dan farq qiladi: qayta so‘rov eski matnni qaytarmaydi, `409` va asl so‘rov identifikatorini beradi. Natijani ilova o‘zi saqlashi kerak.
- Hujjatdagi limitlar: 64 KiB messages/tools, 4096 chiqish tokeni, hisob uchun 2 parallel generatsiya. Model reklamasidagi context hajmi bu endpoint limitini oshirmaydi.

## Realtime va ovozli javob

[Realtime STT](https://docs.voicelab.uz/api/realtime-stt), [TTS](https://docs.voicelab.uz/api/tts)

Realtime STT brauzerning WebM yozuvini to‘g‘ridan-to‘g‘ri qabul qilmaydi: 16 kHz mono PCM16 kerak. Django qisqa muddatli ticket yaratadi, brauzer WebSocket’ga shu ticket bilan ulanadi. Har bo‘lak 100 ms–35 soniya; oraliq matn yo‘q, faqat yakuniy transkript qaytadi. Dastlabki topshiriq kiritish oqimi uchun oddiy 30 soniyalik yozuv + HTTP kamroq murakkablik talab qiladi.

TTS orqali agent savolini ovozda ayttirish mumkin. `/v1/tts/languages` va `/v1/voices` kataloglaridan til/ovoz tanlanadi. Oddiy TTS so‘rovi 1000 UTF-8 baytgacha; 24 kHz WAV qaytaradi. Ovozli javob keyingi bosqich bo‘lishi mumkin.

## Kalit va xarajat

[Kalit ruxsatlari](https://docs.voicelab.uz/api/authentication), [hisobdagi narx sahifasi](https://voicelab.uz/app/developer/api-pricing), [Python SDK](https://docs.voicelab.uz/libraries/sdk)

API kaliti Django serverining `.env` faylida `VOICELAB_API_KEY` orqali beriladi. Brauzerga yuborilmaydi. STT yozuvi va uzun audio natijasini o‘qish uchun `speech_to_text: access`; model katalogi va tahlil uchun `llm: access` mos. Realtime va TTS ruxsatlari faqat ulardan foydalanilganda kerak.

Developer sahifasida bitta mavjud kalit ko‘rindi. Uning maxfiy qiymati olinmadi, ruxsatlari o‘zgartirilmadi va yangi kalit yaratilmagan.

Ko‘rilgan paytda Free hisobda 2000 kredit bor edi. Sahifa faqat STT uchun taxminan 0.08 soatni (4.8 daqiqa) ko‘rsatdi; STT, LLM va TTS bir balansni ishlatadi. Aisha Comet uchun ko‘rsatilgan narxlar: 1M kirish tokeni $0.45, chiqish tokeni $0.70. Bu narxlar va foydalanish imkoniyati integratsiya paytida qayta tekshiriladi.

## Taklif qilingan oqim

1. Foydalanuvchi mikrofonni bosadi va 30 soniyagacha gapiradi.
2. Django audio yozuvni VoiceLab STT’ga yuboradi.
3. Eshitilgan matn foydalanuvchiga ko‘rsatiladi va tahrirlanadi.
4. LLM ruxsat etilgan ijrochilar va Toshkent vaqti bilan topshiriq loyihasini tayyorlaydi; noaniq joyda savol beradi.
5. Django ma’lumotlarni tekshiradi. Foydalanuvchi tayyor maydonlarni ko‘rib yuboradi.
6. Mavjud `create_task` xizmati topshiriq, tarix va xabarnomani saqlaydi.

Sinov to‘plamida adabiy o‘zbekcha, Samarqand/Toshkent shevasi, ruscha aralash gap, tez nutq va shovqin bo‘lishi kerak. Familiya, ish mazmuni hamda sana/vaqt to‘g‘riligi alohida baholanadi. STT xatosini LLM har doim tiklay oladi deb hisoblamaslik kerak.
