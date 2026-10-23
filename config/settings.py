import mimetypes
import os
import secrets
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')
DEBUG = os.getenv('DJANGO_DEBUG', '1') == '1'
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', '')
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured('DJANGO_SECRET_KEY is required in production.')
    secret_path = BASE_DIR / '.dev-secret-key'
    try:
        with secret_path.open('x') as secret_file:
            secret_file.write(secrets.token_urlsafe(64))
    except FileExistsError:
        pass
    SECRET_KEY = secret_path.read_text().strip()

ALLOWED_HOSTS = os.getenv('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1,[::1]').split(',')
CSRF_TRUSTED_ORIGINS = [x.strip() for x in os.getenv('CSRF_TRUSTED_ORIGINS', '').split(',') if x.strip()]
CSRF_TRUST_ALL_ORIGINS = CSRF_TRUSTED_ORIGINS == ['*']
CSRF_FAILURE_VIEW = 'config.csrf.failure'
if CSRF_TRUST_ALL_ORIGINS:
    CSRF_TRUSTED_ORIGINS = []
INSTALLED_APPS = [
    'django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes',
    'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles',
    'core.apps.CoreConfig',
]
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware', 'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware', 'django.middleware.common.CommonMiddleware',
    'config.csrf.CsrfMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware', 'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
# W003 looks for Django's own CSRF middleware by path; config.csrf.CsrfMiddleware
# is that middleware, subclassed to honour CSRF_TRUSTED_ORIGINS='*'.
SILENCED_SYSTEM_CHECKS = ['security.W003']
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [BASE_DIR / 'templates'],
    'APP_DIRS': True, 'OPTIONS': {'context_processors': [
        'django.template.context_processors.request', 'django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages', 'core.context_processors.navigation',
    ]},
}]
WSGI_APPLICATION = 'config.wsgi.application'
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'db.sqlite3', 'OPTIONS': {'timeout': 20}}}
if os.getenv('DB_ENGINE') == 'postgresql':
    DATABASES['default'] = {
        'ENGINE': 'django.db.backends.postgresql', 'NAME': os.getenv('DB_NAME', 'topshiriqlar'),
        'USER': os.getenv('DB_USER', 'postgres'), 'PASSWORD': os.getenv('DB_PASSWORD', ''),
        'HOST': os.getenv('DB_HOST', 'localhost'), 'PORT': os.getenv('DB_PORT', '5432'),
        'CONN_MAX_AGE': 60,
    }
AUTH_USER_MODEL = 'core.User'
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
LANGUAGE_CODE = 'uz'
TIME_ZONE = 'Asia/Tashkent'
USE_I18N = True
USE_TZ = True
# A Linux image without /etc/mime.types serves these as application/octet-stream,
# and the browser then refuses the module script and the wasm binary that the
# local speech filter is built from. Registered before WhiteNoise reads the files.
mimetypes.add_type('text/javascript', '.mjs', True)
mimetypes.add_type('application/wasm', '.wasm', True)

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
MEDIA_ROOT = BASE_DIR / 'media'
MEDIA_URL = '/media/'
# Task files live in S3/MinIO when an endpoint is configured; locally they stay on disk.
S3_ENDPOINT_URL = os.getenv('S3_ENDPOINT_URL', '').strip()
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}
if S3_ENDPOINT_URL:
    STORAGES['default'] = {'BACKEND': 'storages.backends.s3.S3Storage', 'OPTIONS': {
        'endpoint_url': S3_ENDPOINT_URL,
        'access_key': os.getenv('S3_ACCESS_KEY', '').strip(),
        'secret_key': os.getenv('S3_SECRET_KEY', '').strip(),
        'bucket_name': os.getenv('S3_BUCKET', 'topshiriqlar').strip(),
        'region_name': os.getenv('S3_REGION', 'us-east-1').strip(),
        'addressing_style': os.getenv('S3_ADDRESSING_STYLE', 'path').strip(),
        'querystring_auth': True,      # Private bucket: links are signed, never public.
        'querystring_expire': 900,
        'file_overwrite': False,
        'default_acl': None,
        'signature_version': 's3v4',
    }}
# Telegram Mini App and bot. The token authenticates the bot and signs Mini App data.
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
TELEGRAM_BOT_USERNAME = os.getenv('TELEGRAM_BOT_USERNAME', '').strip().lstrip('@')
TELEGRAM_WEBHOOK_SECRET = os.getenv('TELEGRAM_WEBHOOK_SECRET', '').strip()
TELEGRAM_APP_URL = os.getenv('TELEGRAM_APP_URL', '').strip().rstrip('/')
if os.getenv('TELEGRAM_EMBED', '0') == '1':
    # Telegram Web runs the Mini App in an iframe, a third-party context: the
    # session cookie only travels there as SameSite=None, which needs HTTPS.
    SESSION_COOKIE_SAMESITE = CSRF_COOKIE_SAMESITE = 'None'
    SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = True

# Browser push notifications. Generate the pair with: manage.py push_keys
VAPID_PUBLIC_KEY = os.getenv('VAPID_PUBLIC_KEY', '').strip()
VAPID_PRIVATE_KEY = os.getenv('VAPID_PRIVATE_KEY', '').strip()
VAPID_SUBJECT = os.getenv('VAPID_SUBJECT', 'mailto:admin@example.uz').strip()
TASK_FILE_MAX_BYTES = 25 * 1024 * 1024
TASK_FILE_EXTENSIONS = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt',
                        '.rtf', '.csv', '.jpg', '.jpeg', '.png', '.heic', '.zip', '.rar', '.7z'}
# Without this, the app's own warnings (voice, push, Telegram, agent) reach no
# handler in production. DJANGO_LOG_FILE adds a rotating file next to the console.
LOG_FILE = os.getenv('DJANGO_LOG_FILE', '').strip()
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {'standard': {'format': '{asctime} {levelname} {name}: {message}', 'style': '{'}},
    'handlers': {'console': {'class': 'logging.StreamHandler', 'formatter': 'standard'}},
    'root': {'handlers': ['console'], 'level': os.getenv('DJANGO_LOG_LEVEL', 'INFO')},
    # httpx logs every request URL at INFO, and a Telegram URL carries the bot token.
    'loggers': {'django.request': {'handlers': ['console'], 'level': 'ERROR', 'propagate': False},
                'httpx': {'level': 'WARNING'}},
}
if LOG_FILE:
    LOGGING['handlers']['file'] = {'class': 'logging.handlers.RotatingFileHandler', 'filename': LOG_FILE,
                                   'maxBytes': 5 * 1024 * 1024, 'backupCount': 5, 'formatter': 'standard',
                                   'encoding': 'utf-8'}
    LOGGING['root']['handlers'].append('file')
    LOGGING['loggers']['django.request']['handlers'].append('file')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'login'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = 60 * 60 * 8
SESSION_COOKIE_SAMESITE = 'Lax'
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
if os.getenv('TRUST_PROXY_HTTPS') == '1':
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
# TypeSafe (Jev) answers two narrow questions: is this speech meant for us, and
# which employee does a command mean when several share a name. Without a key
# both fall back to what the code did before: listen to everything, and ask.
TYPESAFE_API_KEY = os.getenv('TYPESAFE_API_KEY', '').strip()
TYPESAFE_MODEL = os.getenv('TYPESAFE_MODEL', 'jev-latest').strip()
# Room speech measured around 0.19, real commands around 0.81; the cut sits low
# so an unusual command is still heard.
TYPESAFE_COMMAND_MIN = float(os.getenv('TYPESAFE_COMMAND_MIN', '0.4'))
TYPESAFE_PERSON_MIN = float(os.getenv('TYPESAFE_PERSON_MIN', '0.85'))

DUE_SOON_DAYS = 3
STALE_DAYS = 14

# --- Davomat (yo‘qlama): Hikvision eshik stansiyasi ---
# Push rejimi (asosiy): domofon har bir hodisani /attendance/hook/<secret>/ ga
# o‘zi POST qiladi. Qurilmada sessiya yo‘q — yo‘ldagi maxfiy satr va ixtiyoriy
# manba-IP ro‘yxati yagona haqiqiylik dalili. Bo‘sh bo‘lsa endpoint 404 qaytaradi.
ATTENDANCE_WEBHOOK_SECRET = os.getenv('ATTENDANCE_WEBHOOK_SECRET', '').strip()
ATTENDANCE_ALLOWED_IPS = os.getenv('ATTENDANCE_ALLOWED_IPS', '').strip()
# Yo'qlama ertalab shu soatdan (Toshkent vaqti) boshlab hisoblanadi; undan
# oldingi kirishlar hodisa sifatida yoziladi, lekin kunlik davomatga kirmaydi.
ATTENDANCE_DAY_START_HOUR = int(os.getenv('ATTENDANCE_DAY_START_HOUR', '7') or '7')
# Toshkent vaqti: shu soatdan kelmaganlarga «kechikyapsiz» (xodimга), keyingi
# soatdan kelmaganlar ro'yxati (kotibга) yuboriladi. manage.py attendance_alerts.
ATTENDANCE_LATE_HOUR = int(os.getenv('ATTENDANCE_LATE_HOUR', '9') or '9')
ATTENDANCE_ABSENT_HOUR = int(os.getenv('ATTENDANCE_ABSENT_HOUR', '11') or '11')
# Kechikayotganlarga eslatma shu soatdan (09:00 dan keyingi kirish baribir «Kechikdi» bo‘ladi).
ATTENDANCE_LATE_ALERT_HOUR = int(os.getenv('ATTENDANCE_LATE_ALERT_HOUR', '10') or '10')
# Dam olish kunlari (0 — dushanba … 6 — yakshanba): eslatma va «kelmadi» xabarlari yuborilmaydi.
ATTENDANCE_WEEKEND_DAYS = {int(d) for d in os.getenv('ATTENDANCE_WEEKEND_DAYS', '5,6').split(',') if d.strip()}
# Davomat yuritilmaydigan rollar (vergul bilan): ularning kirishi faqat eshik jurnaliga yoziladi,
# kunlik davomat, Excel va kelish/kechikish/kelmaganlar xabarlarida ko‘rinmaydi.
ATTENDANCE_EXEMPT_ROLES = [r.strip() for r in os.getenv('ATTENDANCE_EXEMPT_ROLES', 'chair').split(',') if r.strip()]
# Hikvision access-hodisasining "major" turi (5 = access controller event).
ATTENDANCE_DOOR_MAJOR = 5
# Yuz taqqoslash (OpenCV YuNet + SFace). Faqat opencv-python kerak; model fayllari
# scripts/setup_face_models.py bilan yuklanadi. Fayllar yo'q bo'lsa tizim karta-only
# rejimiga tushadi. Moslik shu chegaradan past bo'lsa (kun birinchi kirishida) surat
# saqlanadi va kotibga review tushadi. SFACE kosinus chegarasi ~0.363.
FACE_MODELS_DIR = os.getenv('FACE_MODELS_DIR', str(BASE_DIR / 'face_models')).strip()
FACE_DETECTOR_MODEL = os.getenv('FACE_DETECTOR_MODEL',
                                str(Path(FACE_MODELS_DIR) / 'face_detection_yunet_2023mar.onnx')).strip()
FACE_RECOGNIZER_MODEL = os.getenv('FACE_RECOGNIZER_MODEL',
                                  str(Path(FACE_MODELS_DIR) / 'face_recognition_sface_2021dec.onnx')).strip()
FACE_MATCH_THRESHOLD = float(os.getenv('FACE_MATCH_THRESHOLD', '0.363'))
# GPU kerak emas: bitta tekshiruv CPU'da ~50 ms, kuniga xodim boshiga bir marta.
FACE_CPU_THREADS = int(os.getenv('FACE_CPU_THREADS', '1') or '1')
# Domofon kamerasining haqiqiy eni:bo‘yi. DS-KV6124 standart surati 704x576 (16:9 sensordan
# yon tomonga siqilgan) — server uni shu nisbatga qaytarib solishtiradi. Bo‘sh: o‘chiq.
ATTENDANCE_FRAME_ASPECT = os.getenv('ATTENDANCE_FRAME_ASPECT', '16:9').strip()
# Pull rejimi (ixtiyoriy): server domofonga o‘zi ulanib ISAPI alertStream’ni
# o‘qiydi (manage.py attendance_listen). HIKVISION_HOST bo‘sh bo‘lsa o‘chiq.
HIKVISION_HOST = os.getenv('HIKVISION_HOST', '').strip()
HIKVISION_PORT = int(os.getenv('HIKVISION_PORT', '80') or '80')
HIKVISION_HTTPS = os.getenv('HIKVISION_HTTPS', '0') == '1'
# Qurilmaning o‘z-o‘zidan imzolangan sertifikati odatiy; tekshiruv faqat haqiqiy
# sertifikat o‘rnatilgan bo‘lsa yoqiladi.
HIKVISION_VERIFY_TLS = os.getenv('HIKVISION_VERIFY_TLS', '0') == '1'
HIKVISION_USER = os.getenv('HIKVISION_USER', 'admin').strip()
HIKVISION_PASSWORD = os.getenv('HIKVISION_PASSWORD', '')
HIKVISION_ALERT_PATH = os.getenv('HIKVISION_ALERT_PATH', '/ISAPI/Event/notification/alertStream').strip()
# Format aniqlash bosqichida xom hodisalar (JSON/XML va face capture) shu yerga
# yoziladi. Papka shaxsiy ma’lumot saqlaydi — git’ga kiritilmaydi.
ATTENDANCE_CAPTURE_DIR = os.getenv('ATTENDANCE_CAPTURE_DIR', str(BASE_DIR / 'attendance-capture')).strip()

# Server-side credentials only; never included in templates or API responses.
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '').strip()
OPENAI_TASK_MODEL = os.getenv('OPENAI_TASK_MODEL', 'gpt-5.6-sol').strip()
OPENAI_TASK_REASONING = os.getenv('OPENAI_TASK_REASONING', 'low').strip()
MUXLISA_API_KEY = os.getenv('MUXLISA_API_KEY', '').strip()
# Muxlisa speaker id: 0 — ayol ovozi, 1 — erkak ovozi.
MUXLISA_SPEAKER = 1 if os.getenv('MUXLISA_SPEAKER', '0').strip() == '1' else 0
VOICE_MAX_AUDIO_BYTES = 5 * 1024 * 1024
VOICE_MAX_SECONDS = 29
VOICE_REQUESTS_PER_MINUTE = 24
