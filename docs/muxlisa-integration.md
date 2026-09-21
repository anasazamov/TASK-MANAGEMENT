# Muxlisa AI ovozli integratsiyasi

Tekshiruv: 2026-09-21. Manba — hisobdagi «Sync API v2» va «Async API v1» hujjatlari.
STT va TTS uchun Muxlisa AI ishlatiladi; LLM uchun OpenAI. Bu yozuv audio sifati,
tezlik yoki sheva aniqligini o‘lchamaydi: ular jonli sinovda tekshiriladi.

## Ishlatilayotgan chaqiruvlar

- `POST https://service.muxlisa.uz/api/v2/stt` — `multipart/form-data`, `audio` maydoni.
  Javob: `{"text": "..."}`. Chegara: 5 MB, 60 soniya. Sekundlar bo‘yicha hisoblanadi.
- `POST https://service.muxlisa.uz/api/v2/tts` — `application/json`, `{"text": ..., "speaker": 0|1}`.
  Javob `.wav`. `speaker`: 0 — ayol, 1 — erkak (`MUXLISA_SPEAKER`). Belgilar bo‘yicha hisoblanadi.
- Kalit `x-api-key` sarlavhasida, faqat serverda (`MUXLISA_API_KEY`). Brauzerga yuborilmaydi.

Katta fayllar uchun `api/v1/async/*` (task + short polling yoki webhook) bor, lekin
ilovadagi eng uzun yozuv 29 soniya, shuning uchun faqat sinxron API ishlatiladi.

## Arxitekturaga ta’siri

- **Realtime socket yo‘q.** Jonli suhbatda brauzer avvalgidek o‘z serverimizga PCM
  yuboradi (`/voice/live-stream/`), gap tugagach server uni WAV qilib STT’ga yuboradi.
  Matn gap oxirida keladi; gapirish davomida oraliq matn ko‘rsatilmaydi.
- **Ovozli javob oqim bilan kelmaydi.** Server WAV’ni to‘liq olib, keyin brauzerga
  PCM bo‘laklarini uzatadi, shuning uchun uzun javobda ijro biroz kechroq boshlanadi.
  Namuna chastotasi WAV sarlavhasidan olinadi va brauzerga `ready` hodisasida beriladi.
- **Idempotentlik kaliti yo‘q.** Qayta urinish — yangi, alohida hisoblanadigan so‘rov.
  Shuning uchun kod hech qachon avtomatik takrorlamaydi: qayta urinishni foydalanuvchi bosadi.
- **Ovoz katalogi yo‘q.** Ovoz `speaker` raqami bilan tanlanadi; katalog so‘rovi olib tashlandi.
