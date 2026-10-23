"""The in-app user guide and the first-login checklist, per role.

The guide is plain data: each section names the roles it is for, so a person
sees their own part first and can still read what the others do. Keep it in step
with the code — permissions.py decides who may do what; this only explains it.
"""
from django.conf import settings
from django.urls import reverse

ALL = ('chair', 'deputy', 'head', 'office', 'secretary', 'employee')
ASSIGNERS = ('chair', 'deputy', 'head', 'office', 'secretary')

ROLES = {
    'chair': {
        'label': 'Boshqaruv raisi',
        'summary': 'Butun jamiyat bo‘yicha topshiriq beradi va ijroni nazorat qiladi, tashkilot tuzilmasini boshqaradi.',
        'can': [
            'Istalgan xodimga topshiriq berish, ijroni qabul qilish yoki qaytarish, muddatni o‘zgartirish.',
            'Barcha topshiriqlar, statistika, nazorat zanjiri va harakatlar tarixini ko‘rish.',
            '«Struktura» sahifasida bo‘linma va xodim qo‘shish, parolni tiklash, hisobni bloklash.',
            'Davomat: barcha xodimlar, yuz tekshiruvlari va ruxsat so‘rovlari bo‘yicha qaror qabul qilish.',
        ],
    },
    'deputy': {
        'label': 'Rais o‘rinbosari',
        'summary': 'O‘zi nazorat qiladigan bo‘linmalar bo‘yicha topshiriq beradi va ijroni kuzatadi.',
        'can': [
            'Nazoratdagi bo‘linmalar xodimlari va boshliqlariga topshiriq berish.',
            'Shu bo‘linmalar ijrochilarining topshiriqlarini ko‘rish — ularda avtomatik nazoratchisiz.',
            'Bo‘linmalar statistikasi, nazorat zanjiri va davomatini ko‘rish.',
        ],
    },
    'head': {
        'label': 'Bo‘lim boshlig‘i',
        'summary': 'O‘z bo‘limi xodimlariga topshiriq beradi, olgan topshiriqlarini bajaradi va bo‘limga taqsimlaydi.',
        'can': [
            'Bo‘lim xodimlariga topshiriq berish va ijroni qabul qilish.',
            'Olingan topshiriqni bo‘lim xodimlariga quyi topshiriq sifatida taqsimlash.',
            'Bo‘lim statistikasi va davomatini ko‘rish, xodimga uzrli sabab kiritish.',
        ],
    },
    'office': {
        'label': 'Devonxona mudiri',
        'summary': 'Kelgan xatlarni topshiriq sifatida ro‘yxatga oladi va bo‘lim boshliqlariga yo‘naltiradi.',
        'can': [
            'Bo‘lim boshliqlariga topshiriq berish — xat raqami, sanasi va kimdan kelgani bilan.',
            'O‘zi bergan topshiriqlarning ijrosini kuzatish va qabul qilish.',
        ],
    },
    'secretary': {
        'label': 'Kotib referent',
        'summary': 'Barcha topshiriqlarni ko‘radi, topshiriq beradi va xodimlar davomatini yuritadi.',
        'can': [
            'Barcha topshiriqlar va statistikani ko‘rish, istalgan xodimga topshiriq berish.',
            'Davomat: yuzlarni biriktirish, yuz tekshiruvlari va ruxsat so‘rovlari bo‘yicha qaror.',
            'Telegram orqali: har bir xodim kelgani, tekshiruv kerakligi, 11:00 dagi kelmaganlar ro‘yxati.',
        ],
    },
    'employee': {
        'label': 'Xodim',
        'summary': 'O‘ziga berilgan topshiriqlarni bajaradi va o‘z davomatini kuzatadi.',
        'can': [
            'Topshiriqni ko‘rish, izoh va fayl qo‘shish, ijroni topshirish, muddat uzaytirishni so‘rash.',
            'Qo‘shimcha ijrochi sifatida o‘z qismini bajarish.',
            '«Mening davomatim» sahifasida kirishlarni ko‘rish, ruxsat so‘rash yoki kelmagan kun sababini bildirish.',
        ],
    },
}

SECTIONS = [
    {
        'id': 'start', 'title': 'Tizimga kirish', 'icon': 'exit', 'roles': ALL,
        'intro': 'Tizimga brauzerdan yoki Telegram ichidagi ilovadan kirasiz.',
        'steps': [
            'Brauzerda saytni oching va rahbariyat bergan login hamda parolni kiriting.',
            'Birinchi kirishdan keyin chap pastdagi «Parolni o‘zgartirish» orqali o‘z parolingizni qo‘ying.',
            'Telegram’da botni ulasangiz (pastdagi bo‘lim), ilovaga parolsiz kirasiz.',
            'Parolni unutsangiz — Boshqaruv raisiga murojaat qiling, u yangi vaqtinchalik parol beradi.',
            '5 daqiqada 10 marta noto‘g‘ri kiritilsa, kirish vaqtincha bloklanadi.',
        ],
    },
    {
        'id': 'telegram', 'title': 'Telegram botni ulash', 'icon': 'bell', 'roles': ALL,
        'intro': 'Bot topshiriqlar va davomat haqidagi xabarlarni yuboradi va ilovani Telegram ichida ochadi.',
        'steps': [
            'Telegram’da botni oching va «/start» yuboring.',
            '«📱 Raqamni yuborish» tugmasini bosing — faqat o‘z raqamingizni yuboring.',
            'Raqam xodimlar ro‘yxatidagi telefoningiz bilan solishtiriladi va hisobingiz bog‘lanadi.',
            '«Bu raqam tizimda topilmadi» chiqsa — rahbaringizga ayting: ro‘yxatdagi telefon raqamingiz tuzatilishi kerak.',
            'Bog‘langach, botdagi «Topshiriqlar» tugmasi ilovani parolsiz ochadi.',
        ],
    },
    {
        'id': 'notifications', 'title': 'Xabarnomalar', 'icon': 'bell', 'roles': ALL,
        'intro': 'Muhim voqealar uch joyda ko‘rinadi: yuqoridagi qo‘ng‘iroqcha, brauzer bildirishnomasi va Telegram.',
        'steps': [
            'Qo‘ng‘iroqcha yonidagi raqam — o‘qilmagan xabarlar soni; sahifa o‘zi yangilanib turadi.',
            '«Xabarnomalar» sahifasida «Bildirishnomani yoqish» tugmasi brauzer bildirishnomasini yoqadi; «Sinov xabarnomasi» bilan tekshiring.',
            'Yangi topshiriq, izoh, fayl, ijro topshirilishi yoki qaytarilishi, muddat o‘zgarishi haqida xabar keladi.',
            'Muddat yaqinlashganda yoki o‘tganda tizim har soat eslatadi (har holat uchun kuniga bir marta).',
            'Davomat xabarlari faqat Telegram orqali keladi — botni ulashni unutmang.',
        ],
    },
    {
        'id': 'dashboard', 'title': 'Bosh panel', 'icon': 'grid', 'roles': ALL,
        'intro': 'Kirganda ochiladigan sahifa — ijroning qisqa manzarasi.',
        'steps': [
            'Yuqoridagi to‘rt karta: faol, muddati o‘tgan, tasdiq kutilayotgan va qabul qilingan topshiriqlar. Kartani bosing — ro‘yxat ochiladi.',
            '«Sizning qaroringiz kutilmoqda» — siz qabul qilishingiz kerak bo‘lgan ijro va muddat so‘rovlari.',
            '«Diqqat talab qiladi» — muddati o‘tgan, yaqin yoki uzoq turib qolgan topshiriqlar.',
        ],
    },
    {
        'id': 'my-tasks', 'title': 'Menga berilgan topshiriqni bajarish', 'icon': 'tasks', 'roles': ALL,
        'intro': 'Topshiriq «Jarayonda» → «Tasdiq kutilmoqda» → «Qabul qilingan» bosqichlaridan o‘tadi.',
        'steps': [
            '«Topshiriqlar» bo‘limida topshiriqni oching — ochganingiz «tanishildi» deb belgilanadi.',
            'Savol yoki oraliq natija bo‘lsa, izoh yozing; kerakli hujjatlarni fayl sifatida biriktiring (20 tagacha, har biri 25 MB gacha).',
            'Muddatga ulgurmasangiz, «Muddatni uzaytirishni so‘rash» orqali yangi sana va sababni yuboring — topshiriq bergan rahbar qaror qiladi.',
            'Ish tugagach, «Ijro tugadi — topshirish» tugmasini bosing va hisobot yozing.',
            'Rahbar qabul qilsa, topshiriq yopiladi; qaytarsa — sabab bilan yana «Jarayonda» bo‘ladi.',
            'Rahbar «haftalik hisobot» so‘rasa, «Hisobot yuborish» orqali javob bering.',
            'Agar sizni «qo‘shimcha ijrochi» qilishsa, faqat o‘z qismingizni bajarib «Qismni topshirish» qilasiz.',
            '«Nazoratchi» bo‘lsangiz — ijroni kuzatasiz, izoh yozasiz va hisobot so‘raysiz, lekin ijroni qabul qilmaysiz.',
        ],
    },
    {
        'id': 'assign', 'title': 'Topshiriq berish', 'icon': 'plus', 'roles': ASSIGNERS,
        'intro': 'Yuqoridagi «Topshiriq berish» tugmasi yangi topshiriq formasini ochadi.',
        'steps': [
            'Mazmun va izohni yozing, ijrochini tanlang.',
            'Muddatni tanlang yoki tezkor tugmalardan foydalaning: 2 soat, 4 soat, Ertaga, 3 kun. Bo‘sh qoldirsangiz — «Muddatsiz» bo‘ladi.',
            'Kerakli hujjatlarni biriktiring.',
            'Kimga topshiriq bera olasiz: rais va kotib — hammaga; o‘rinbosar — nazoratdagi bo‘linmalarga; bo‘lim boshlig‘i — o‘z bo‘limi xodimlariga; devonxona — bo‘lim boshliqlariga.',
            'Devonxona formasida qo‘shimcha ravishda xat raqami, sanasi va kimdan kelgani kiritiladi.',
            'O‘zingizga berilgan topshiriqni bo‘lim xodimlariga bo‘lish uchun uning sahifasida «Quyi topshiriqqa taqsimlash» tugmasini bosing.',
            'Mikrofon orqali ham topshiriq berish mumkin — «Ovozli agent» bo‘limiga qarang.',
        ],
    },
    {
        'id': 'manage', 'title': 'Ijroni qabul qilish va boshqarish', 'icon': 'check', 'roles': ASSIGNERS,
        'intro': 'Siz bergan topshiriqlar bo‘yicha qarorlar topshiriq sahifasida qabul qilinadi.',
        'steps': [
            'Ijrochi ijroni topshirganda xabar keladi: hisobotni o‘qing va «Qabul qilish» yoki «Sabab bilan qaytarish»ni tanlang.',
            'Muddat uzaytirish so‘rovini «Tasdiqlash» yoki «Rad etish» (sabab majburiy) bilan hal qiling — bosh paneldagi «MUDDAT SO‘ROVI» kartasidan ham.',
            '«Muddatni o‘zgartirish» bilan muddatni o‘zingiz surishingiz yoki olib tashlashingiz mumkin.',
            '«Haftalik hisobot so‘rash» ijrochidan oraliq hisobot talab qiladi.',
            'Ishtirokchi qo‘shish: «Qo‘shimcha ijrochi» — ishning bir qismi bilan (qismni siz qabul qilasiz); «Nazoratchi» — faqat kuzatish uchun.',
            'Asosiy ijrochi barcha quyi topshiriqlar va qismlar qabul qilinmaguncha ijroni topshira olmaydi.',
        ],
    },
    {
        'id': 'control', 'title': 'Nazorat va statistika', 'icon': 'link', 'roles': ASSIGNERS,
        'intro': 'Ijroning umumiy holatini kuzatish vositalari.',
        'steps': [
            '«Nazorat zanjiri» — topshiriqlar daraxti: kim kimga berdi va quyi zanjirda qayerda muddat buzilgan.',
            '«Harakatlar tarixi» — barcha amallar: 7 kun, 30 kun yoki butun davr, harakat turi bo‘yicha filtr.',
            '«Xodimlar → Statistika» — har bir xodimning topshiriqlari, bajarilmoqda, bajarilmagan va tanishilmagan soni; hafta bo‘yicha va Excel’ga yuklash.',
        ],
    },
    {
        'id': 'my-attendance', 'title': 'Mening davomatim', 'icon': 'clock',
        'roles': ('deputy', 'head', 'office', 'secretary', 'employee'),
        'intro': 'Yo‘qlama eshikdagi domofon orqali avtomatik olinadi: kartangizni yoki PIN kodingizni kiritganingizda.',
        'steps': [
            'Kartani bosganda kamera yuzingizni oladi va tizimdagi rasmingiz bilan solishtiradi — kameraga to‘g‘ri qarang.',
            'Yo‘qlama Toshkent vaqti bilan 07:00 dan hisoblanadi; 09:00 dan keyingi birinchi kirish «Kechikdi» bo‘ladi.',
            'Yuz aniq tanilmasa, kirish kotib referent tekshiruviga tushadi va sizga Telegram’da xabar keladi. Keyingi kirishda yuzingizni aniq ko‘rsatsangiz, avtomatik tasdiqlanadi.',
            'Kelganingizda Telegram’da «yo‘qlamangiz qabul qilindi» xabari keladi. 10:00 gacha kirmasangiz — «kechikyapsiz» eslatmasi, 11:00 gacha ham kirmasangiz — «kelmagan deb belgilandingiz» xabari (sababini shu xabardan bildirasiz). Ruxsatingiz bo‘lsa va dam olish kunlari bu xabarlar kelmaydi.',
            '«Mening davomatim» sahifasida oyma-oy kirishlar, kechikishlar va holatni ko‘rasiz.',
            'Kelolmasangiz yoki kechiksangiz (kasal, kechroq kelish, xizmat safari, ta’til, boshqa) — «Ruxsat so‘rash» tugmasini bosing, bir yoki bir necha kunga. O‘tib ketgan kun uchun o‘sha kun qatoridagi «Sababini bildirish» havolasidan foydalaning.',
            'So‘rov kotib referentga boradi; qaror Telegram orqali keladi. Ko‘rib chiqilmagan so‘rovni bekor qilishingiz mumkin.',
        ],
    },
    {
        'id': 'staff-attendance', 'title': 'Xodimlar davomati', 'icon': 'users', 'roles': ('chair', 'deputy', 'head', 'secretary'),
        'intro': '«Xodimlar → Davomat» — o‘zingiz ko‘radigan xodimlarning kunlik davomati.',
        'steps': [
            'Kun chiplari yoki sana orqali kunni tanlang; bugungi ro‘yxat o‘zi yangilanadi.',
            'Har bir xodim uchun: kirishlar soni, birinchi va oxirgi kirish, kechikish va holat.',
            'Xodim sababini aytgan bo‘lsa, qatordagi «Sabab kiritish» orqali kiriting — kechikish xabari unga bormaydi.',
            '«Excel yuklab olish» — kunlik hisobot.',
            '«Eshik ochilishlari» yorlig‘ida barcha kirishlar vaqt oralig‘i bo‘yicha.',
        ],
    },
    {
        'id': 'attendance-decisions', 'title': 'Davomat bo‘yicha qarorlar', 'icon': 'check', 'roles': ('chair', 'secretary'),
        'intro': 'Kotib referent va rais uchun «Davomat» ichida qo‘shimcha bo‘limlar bor.',
        'steps': [
            '«Yuzlar» — har bir xodimga aniq, yorug‘ rasm biriktiring; rasmsiz xodim faqat karta bo‘yicha hisoblanadi.',
            '«Tekshiruvlar» — yuzi tanilmagan kirishlar: ikki rasmni solishtirib «Tasdiqlash» yoki sababini yozib «Rad etish».',
            '«Ruxsat so‘rovlari» — xodimlar yuborgan so‘rovlar: «Tasdiqlash» (kunlar «Uzrli» bo‘ladi) yoki sabab bilan «Rad etish». Qaror xodimga Telegram’da boradi.',
            'Telegram orqali kotib referentga: har bir xodim kelgani, tekshiruv yoki ruxsat so‘rovi kelgani va 11:00 dagi kelmaganlar ro‘yxati.',
            'Barcha qarorlar «Ko‘rib chiqilganlar» tarixida kim, qachon va nima sababdan qilgani bilan saqlanadi.',
        ],
    },
    {
        'id': 'structure', 'title': 'Struktura va xodimlar', 'icon': 'structure', 'roles': ('chair',),
        'intro': '«Struktura» sahifasi faqat Boshqaruv raisida bor.',
        'steps': [
            '«Bo‘linma qo‘shish» — yangi bo‘lim; har bir bo‘limda bitta boshliq bo‘ladi.',
            '«Xodim qo‘shish» — F.I.Sh., lavozim, rol, bo‘lim, telefon (Telegram uchun 998XXXXXXXXX) va vaqtinchalik parol.',
            'Rais o‘rinbosariga u nazorat qiladigan bo‘linmalarni belgilang.',
            'Xodim qatorida: «Tahrirlash», «Parolni tiklash», «Bloklash/Faollashtirish».',
            'Faol topshiriqlari bor xodimning bo‘limini almashtirib bo‘lmaydi — avval topshiriqlarni yakunlang yoki boshqaga bering.',
        ],
    },
    {
        'id': 'agent', 'title': 'Ovozli agent', 'icon': 'mic', 'roles': ALL,
        'intro': 'Agent — o‘zbek tilida gapirib yoki yozib ishlaydigan yordamchi. U faqat sizning huquqingiz doirasida ishlaydi.',
        'steps': [
            'Yuqoridagi «Agent» tugmasini bosing: yozing yoki «Gapirish»ni bosib ayting.',
            'Misollar: «Bugun muddati o‘tgan topshiriqlarim qaysi?», «Karimovga ertagacha hisobot tayyorlash topshirig‘ini ber», «Bu hafta kechikkanlar bo‘yicha sahifa tayyorla».',
            'Agent biror narsani o‘zgartirishdan oldin ko‘rib chiqish oynasini ko‘rsatadi — «Tasdiqlayman» demaguningizcha hech narsa saqlanmaydi.',
            'Agent yaratgan sahifalar «Agent sahifalari» bo‘limida saqlanadi; raqamlar har ochilganda yangilanadi.',
            '«Yangi suhbat» suhbat tarixini tozalaydi. Agent hech qachon parolingizni so‘ramaydi.',
        ],
    },
]


# Shown to whoever carries the HR flag, whatever their role.
HR_SECTION = {
    'id': 'hr', 'title': 'HR: xodimlar davomati va statistikasi', 'icon': 'users', 'roles': (),
    'intro': 'Sizda HR (kadrlar xizmati) huquqi bor: butun tashkilot bo‘yicha ko‘rasiz va qaror qilasiz.',
    'steps': [
        '«Xodimlar → Statistika» — barcha xodimlarning topshiriq yuklamasi, hafta bo‘yicha va Excel’ga.',
        '«Xodimlar → Davomat» — barcha xodimlarning kunlik davomati; «Belgilangan Excel» kelmaganlarni bo‘yab beradi.',
        '«Tekshiruvlar» — yuzi tanilmagan kirishlarni tasdiqlash yoki sabab bilan rad etish.',
        '«Ruxsat so‘rovlari» — xodimlar yuborgan so‘rovlarni tasdiqlash yoki sabab bilan rad etish; qaror xodimga Telegram’da boradi.',
        '«Yuzlar» — xodimga yuz rasmini biriktirish; qatordagi «Sabab kiritish» — xodim aytgan sababni yozish.',
        'Telegram orqali sizga ham «Yuz tekshiruvi kerak» va «Ruxsat so‘rovi» xabarlari keladi.',
    ],
}


def sections_for(role, is_hr=False):
    sections = [section for section in SECTIONS if role in section['roles']]
    return sections + [HR_SECTION] if is_hr else sections


def onboarding_steps(user):
    """The welcome checklist for this person; `done` is None where it cannot be told."""
    from .models import PushSubscription, Task
    bot = settings.TELEGRAM_BOT_USERNAME
    steps = [
        {'title': 'O‘z parolingizni qo‘ying', 'text': 'Vaqtinchalik parolni faqat o‘zingiz biladigan parolga almashtiring.',
         'url': reverse('password_change'), 'action': 'Parolni o‘zgartirish', 'done': bool(user.password_changed_at)},
        {'title': 'Telegram botni ulang', 'text': 'Xabarlar va davomat ogohlantirishlari shu orqali keladi; ilovaga parolsiz kirasiz.',
         'url': f'https://t.me/{bot}?start=link' if bot else reverse('guide') + '#telegram',
         'action': 'Botni ochish', 'done': bool(user.telegram_id), 'external': bool(bot)},
        {'title': 'Brauzer bildirishnomalarini yoqing', 'text': 'Kompyuterda ishlasangiz, yangi topshiriq darhol ko‘rinadi.',
         'url': reverse('notifications'), 'action': 'Yoqish',
         'done': PushSubscription.objects.filter(user=user, failed_at__isnull=True).exists()},
    ]
    if user.can_assign:
        steps.append({'title': 'Birinchi topshiriqni bering', 'text': 'Ijrochini va muddatni tanlang — qolganini tizim kuzatadi.',
                      'url': reverse('task_create'), 'action': 'Topshiriq berish',
                      'done': Task.objects.filter(issuer=user).exists()})
    else:
        steps.append({'title': 'Topshiriqlaringizni ko‘ring', 'text': 'Sizga berilgan topshiriqlar va ularning muddatlari.',
                      'url': reverse('tasks'), 'action': 'Topshiriqlar', 'done': None})
    if user.can_manage_attendance:
        steps.append({'title': 'Davomat nazoratini ko‘ring', 'text': 'Yuzlar, tekshiruvlar va ruxsat so‘rovlari shu yerda.',
                      'url': reverse('attendance_daily'), 'action': 'Davomat', 'done': None})
    else:
        steps.append({'title': 'Davomatingizni ko‘ring', 'text': 'Kirishlaringiz, kechikishlar va ruxsat so‘rash.',
                      'url': reverse('my_attendance'), 'action': 'Mening davomatim', 'done': None})
    steps.append({'title': 'Qo‘llanmani o‘qing', 'text': 'Rolingiz uchun qisqa yo‘riqnoma — 5 daqiqa.',
                  'url': reverse('guide'), 'action': 'Qo‘llanma', 'done': None})
    return steps
