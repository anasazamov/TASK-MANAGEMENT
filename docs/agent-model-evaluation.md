# Suhbat agenti — model yangilanishi

Sana: 2026-09-18. Model: `gpt-5.6-sol`, reasoning: `low`.
Oldingi konfiguratsiya: `gpt-4.1-mini`. STT va TTS: Muxlisa AI.

Haqiqiy OpenAI API bilan Django `/agent/message/` endpointi tekshirildi.
Sinov alohida vaqtinchalik suhbatda, raisning mavjud ko‘rish doirasida o‘tkazildi.
Topshiriq va xodim yozuvlari o‘zgartirilmadi; yozuvchi tool chaqiruvlari sinovda bloklandi.

| Buyruq / holat | Tekshirilgan natija |
| --- | --- |
| Bu yerdan menga muddati o'tgan topshiriqlarni ko'rsatdi | `/tasks/?filter=overdue`, bitta topshiriq sahifasi emas |
| Barchasini ko‘rsatadi, bittasini emas (T-118 sahifasidan) | Oldingi overdue filtri bilan ro‘yxat |
| Azamov onasini barcha muddati o'tib ketgan toshliqlarining ro'yxatini ko'rsatdi | `overdue&employee=21`, ism bazadan aniqlandi |
| Azamov anasini | Aniqlashtirish; avtomatik sahifa ochilmadi |
| Azamovni barcha muddati o'tib ketgan topshiriqlarni ochib ko'rsat | `overdue&employee=21` |
| Tasdiq kutilmoqda topshiriqlarning ichkiligi | `submitted&employee=21` |
| Tasdiq kutilmoqda topshiriqlarini ochib ko'rsat | `submitted&employee=21` |
| Muddatsiz topshiriqlarni ko'rsating | `undated&employee=21` |
| Men muddatsiz topshiriqlar dedim | `undated&employee=21` |
| Azamovni muddatsiz topshiriqlar ochiladi | `undated&employee=21` |
| O'zing so'rayver yaxshimikan | Aniqlashtirish; oldingi navigatsiya takrorlanmadi |

Yakuniy tekshiruv: **11/11**. Barcha javoblar HTTP 200, yozuvchi taklif va noto‘g‘ri
«huquqingiz yo‘q» javobi bo‘lmadi. So‘rovlar 2,76–13,60 soniya davom etdi; bu
STT/TTS vaqtini o‘z ichiga olmaydi. Birinchi sinovda bitta provayder xatosi kuzatildi.
Agent endi 5xx javobidan so‘ng faqat generatsiyani bir marta takrorlaydi;
autentifikatsiya, limit va noaniq tarmoq xatolari takrorlanmaydi.

Avtomatik tekshiruv: `python manage.py test --noinput` — **131 test o‘tdi**.
Yangi testlar reasoning javobini keyingi tool so‘roviga uzatish, eski modelga moslik,
ro‘yxat tuzatishlari, xodim ko‘rish huquqi va cheklangan qayta urinishni tekshiradi.

Bu matnga aylangan buyruqlarning cheklangan regressiya sinovi; mikrofon orqali
sheva, fon suhbati yoki Muxlisa transkripsiya aniqligi o‘lchovi emas.

Model hujjati: [GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol).
