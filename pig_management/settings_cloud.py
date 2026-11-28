INSTALLED_APPS = ['django.contrib.admin',
 'django.contrib.auth',
 'django.contrib.contenttypes',
 'django.contrib.sessions',
 'django.contrib.messages',
 'django.contrib.staticfiles',
 'django.contrib.humanize',
 'apps.pigs.apps.PigsConfig',
 'apps.users.apps.UsersConfig',
 'apps.companies.apps.CompaniesConfig',
 'apps.transport.apps.TransportConfig',
 'apps.control_center.apps.ControlCenterConfig',
 'apps.mobile_api',
 'apps.core']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware',
 'whitenoise.middleware.WhiteNoiseMiddleware',
 'django.contrib.sessions.middleware.SessionMiddleware',
 'django.middleware.locale.LocaleMiddleware',
 'django.middleware.common.CommonMiddleware',
 'django.middleware.csrf.CsrfViewMiddleware',
 'django.contrib.auth.middleware.AuthenticationMiddleware',
 'django.contrib.messages.middleware.MessageMiddleware',
 'apps.core.middleware.NoCacheAuthenticatedMiddleware',
 'apps.control_center.middleware.SessionPolicyMiddleware',
 'apps.control_center.middleware.ForcePasswordChangeMiddleware',
 'apps.companies.middleware.CompanyContextMiddleware',
 'django.middleware.clickjacking.XFrameOptionsMiddleware']
AUTHENTICATION_BACKENDS = ['apps.companies.backends.CompanyRoleBackend']
AUTH_PASSWORD_VALIDATORS = [{'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
 {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
  'OPTIONS': {'min_length': 10}},
 {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
 {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'}]
AUTH_USER_MODEL = 'users.User'
ROOT_URLCONF = 'pig_management.urls'
WSGI_APPLICATION = 'pig_management.wsgi.application'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Africa/Dar_es_Salaam'
USE_I18N = True
USE_TZ = True
LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/accounts/login/'
SESSION_COOKIE_NAME = 'pigs_sessionid'
CSRF_COOKIE_NAME = 'pigs_csrftoken'
CSRF_COOKIE_HTTPONLY = False
LANGUAGE_COOKIE_NAME = 'django_language'
LANGUAGES = [['af', 'Afrikaans'],
 ['ar', 'Arabic'],
 ['ar-dz', 'Algerian Arabic'],
 ['ast', 'Asturian'],
 ['az', 'Azerbaijani'],
 ['bg', 'Bulgarian'],
 ['be', 'Belarusian'],
 ['bn', 'Bengali'],
 ['br', 'Breton'],
 ['bs', 'Bosnian'],
 ['ca', 'Catalan'],
 ['ckb', 'Central Kurdish (Sorani)'],
 ['cs', 'Czech'],
 ['cy', 'Welsh'],
 ['da', 'Danish'],
 ['de', 'German'],
 ['dsb', 'Lower Sorbian'],
 ['el', 'Greek'],
 ['en', 'English'],
 ['en-au', 'Australian English'],
 ['en-gb', 'British English'],
 ['eo', 'Esperanto'],
 ['es', 'Spanish'],
 ['es-ar', 'Argentinian Spanish'],
 ['es-co', 'Colombian Spanish'],
 ['es-mx', 'Mexican Spanish'],
 ['es-ni', 'Nicaraguan Spanish'],
 ['es-ve', 'Venezuelan Spanish'],
 ['et', 'Estonian'],
 ['eu', 'Basque'],
 ['fa', 'Persian'],
 ['fi', 'Finnish'],
 ['fr', 'French'],
 ['fy', 'Frisian'],
 ['ga', 'Irish'],
 ['gd', 'Scottish Gaelic'],
 ['gl', 'Galician'],
 ['he', 'Hebrew'],
 ['hi', 'Hindi'],
 ['hr', 'Croatian'],
 ['hsb', 'Upper Sorbian'],
 ['ht', 'Haitian Creole'],
 ['hu', 'Hungarian'],
 ['hy', 'Armenian'],
 ['ia', 'Interlingua'],
 ['id', 'Indonesian'],
 ['ig', 'Igbo'],
 ['io', 'Ido'],
 ['is', 'Icelandic'],
 ['it', 'Italian'],
 ['ja', 'Japanese'],
 ['ka', 'Georgian'],
 ['kab', 'Kabyle'],
 ['kk', 'Kazakh'],
 ['km', 'Khmer'],
 ['kn', 'Kannada'],
 ['ko', 'Korean'],
 ['ky', 'Kyrgyz'],
 ['lb', 'Luxembourgish'],
 ['lt', 'Lithuanian'],
 ['lv', 'Latvian'],
 ['mk', 'Macedonian'],
 ['ml', 'Malayalam'],
 ['mn', 'Mongolian'],
 ['mr', 'Marathi'],
 ['ms', 'Malay'],
 ['my', 'Burmese'],
 ['nb', 'Norwegian Bokmål'],
 ['ne', 'Nepali'],
 ['nl', 'Dutch'],
 ['nn', 'Norwegian Nynorsk'],
 ['os', 'Ossetic'],
 ['pa', 'Punjabi'],
 ['pl', 'Polish'],
 ['pt', 'Portuguese'],
 ['pt-br', 'Brazilian Portuguese'],
 ['ro', 'Romanian'],
 ['ru', 'Russian'],
 ['sk', 'Slovak'],
 ['sl', 'Slovenian'],
 ['sq', 'Albanian'],
 ['sr', 'Serbian'],
 ['sr-latn', 'Serbian Latin'],
 ['sv', 'Swedish'],
 ['sw', 'Swahili'],
 ['ta', 'Tamil'],
 ['te', 'Telugu'],
 ['tg', 'Tajik'],
 ['th', 'Thai'],
 ['tk', 'Turkmen'],
 ['tr', 'Turkish'],
 ['tt', 'Tatar'],
 ['udm', 'Udmurt'],
 ['ug', 'Uyghur'],
 ['uk', 'Ukrainian'],
 ['ur', 'Urdu'],
 ['uz', 'Uzbek'],
 ['vi', 'Vietnamese'],
 ['zh-hans', 'Simplified Chinese'],
 ['zh-hant', 'Traditional Chinese']]
DATA_UPLOAD_MAX_MEMORY_SIZE = 5242880
FILE_UPLOAD_MAX_MEMORY_SIZE = 5242880
MESSAGE_TAGS = {}
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates',
  'DIRS': ['templates'],
  'APP_DIRS': True,
  'OPTIONS': {'context_processors': ['django.template.context_processors.request',
                                     'django.contrib.auth.context_processors.auth',
                                     'django.contrib.messages.context_processors.messages']}}]
STATICFILES_DIRS = ['static']

# Cloud-only configuration; no local database fallback.
import os
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, unquote
from django.core.exceptions import ImproperlyConfigured
import dj_database_url
BASE_DIR = Path(__file__).resolve().parent.parent
LOCALE_PATHS = [BASE_DIR / "locale"]
DEBUG = False
SECRET_KEY = os.environ['DJANGO_SECRET_KEY']
if len(SECRET_KEY) < 50:
    raise ImproperlyConfigured('A strong DJANGO_SECRET_KEY is required.')
SECRET_KEY_FALLBACKS = []
ALLOWED_HOSTS = ['home-management-system-ycto.onrender.com']
CSRF_TRUSTED_ORIGINS = ['https://home-management-system-ycto.onrender.com']
url = os.environ['DATABASE_URL']
parsed = urlsplit(url)
if (parsed.scheme not in ('postgres','postgresql') or not (parsed.hostname or '').endswith('.neon.tech')
        or unquote(parsed.path.lstrip('/')) != 'bizmanager_v2'
        or parse_qs(parsed.query).get('sslmode', [''])[0] not in ('require','verify-ca','verify-full')):
    raise ImproperlyConfigured('Use the dedicated Neon bizmanager_v2 database with TLS.')
DATABASES = {'default': dj_database_url.parse(url, conn_max_age=0)}
if DATABASES['default']['ENGINE'] != 'django.db.backends.postgresql':
    raise ImproperlyConfigured('PostgreSQL required.')
for t in TEMPLATES:
    t['DIRS'] = [BASE_DIR / p for p in t['DIRS']]
STATICFILES_DIRS = [BASE_DIR / p for p in STATICFILES_DIRS]
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
MEDIA_ROOT = Path(os.environ.get('BIZMANAGER_MEDIA_ROOT', str(BASE_DIR / 'media')))
STORAGES = {'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'}}
if 'whitenoise.middleware.WhiteNoiseMiddleware' not in MIDDLEWARE:
    MIDDLEWARE.insert(MIDDLEWARE.index('django.middleware.security.SecurityMiddleware') + 1, 'whitenoise.middleware.WhiteNoiseMiddleware')
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO','https')
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_DOMAIN = None
CSRF_COOKIE_DOMAIN = None
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
SECURE_HSTS_SECONDS = 3600
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = False
X_FRAME_OPTIONS = 'DENY'
CENTRALAUTH_ENABLED = False
BIZMANAGER_AUTO_CARGO_ROUTE = True
BIZMANAGER_GOOGLE_CLIENT_IDS = tuple(x.strip() for x in os.environ.get('BIZMANAGER_GOOGLE_CLIENT_IDS','').split(',') if x.strip())
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = os.environ.get('EMAIL_HOST','localhost')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT','587'))
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER','')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD','')
EMAIL_USE_TLS = True
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL','no-reply@example.invalid')
BIZMANAGER_ANDROID_UPDATE_ENABLED = os.environ.get('BIZMANAGER_ANDROID_UPDATE_ENABLED','0') == '1'
for key in ('LATEST_VERSION_CODE','MINIMUM_VERSION_CODE','LATEST_VERSION_NAME','APK_URL','APK_SHA256','RELEASE_NOTES','RELEASE_PUBLISHED_AT'):
    globals()['BIZMANAGER_ANDROID_'+key] = os.environ.get('BIZMANAGER_ANDROID_'+key,'')
