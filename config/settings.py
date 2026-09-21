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
TASK_FILE_MAX_BYTES = 25 * 1024 * 1024
TASK_FILE_EXTENSIONS = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt',
                        '.rtf', '.csv', '.jpg', '.jpeg', '.png', '.heic', '.zip', '.rar', '.7z'}
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
DUE_SOON_DAYS = 3
STALE_DAYS = 14

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
VOICE_SPEAKER_MODEL = BASE_DIR / 'private_models' / '3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx'
VOICE_SPEAKER_THRESHOLD = 0.65
VOICE_SPEAKER_WINDOW_THRESHOLD = 0.60
