"""
Django settings for pig_management project.

Pig Management System
"""
from pathlib import Path
import os
import dj_database_url
from dotenv import load_dotenv

# ============================================================
# BASE DIRECTORY
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


# ============================================================
# SECURITY
# ============================================================

# Production: set DJANGO_SECRET_KEY in environment.
# Development fallback keeps the current project runnable.
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-development-only-change-me",
)


# ============================================================
# DEBUG
# ============================================================

# Keep DEBUG enabled during development.
# Set DJANGO_DEBUG=False in production.
DEBUG = os.environ.get(
    "DJANGO_DEBUG",
    "False",
).lower() in ("true", "1", "yes")


ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    "192.168.0.110",
    "home-management-system-ycto.onrender.com",
]


CSRF_TRUSTED_ORIGINS = [
    "http://192.168.0.110:8000",
    "https://home-management-system-ycto.onrender.com",
]


# ============================================================
# APPLICATIONS
# ============================================================

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'apps.pigs.apps.PigsConfig',
    'apps.users.apps.UsersConfig'
]


# ============================================================
# ============================================================
# MIDDLEWARE
# ============================================================


#============================================================
# AUTHENTICATION BACKENDS
# ============================================================

AUTHENTICATION_BACKENDS = [
    'apps.users.backends.CustomUserBackend',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',

    'django.contrib.sessions.middleware.SessionMiddleware',

    'django.middleware.common.CommonMiddleware',

    'django.middleware.csrf.CsrfViewMiddleware',

    'django.contrib.auth.middleware.AuthenticationMiddleware',

    'apps.core.middleware.NoCacheAuthenticatedMiddleware',

    'django.contrib.messages.middleware.MessageMiddleware',

    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# ============================================================
# URL CONFIGURATION
# ============================================================

ROOT_URLCONF = "pig_management.urls"


# ============================================================
# TEMPLATES
# ============================================================

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",

        "DIRS": [
            BASE_DIR / "templates",
        ],

        "APP_DIRS": True,

        "OPTIONS": {
            "context_processors": [

                "django.template.context_processors.request",

                "django.contrib.auth.context_processors.auth",

                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


# ============================================================
# WSGI / ASGI
# ============================================================

WSGI_APPLICATION = "pig_management.wsgi.application"


# ============================================================
# DATABASE
# ============================================================

DATABASES = {
    "default": dj_database_url.parse(
        os.environ.get("DATABASE_URL"),
        conn_max_age=600,
        ssl_require=True,
    )
}


# ============================================================
# PASSWORD VALIDATION
# ============================================================

AUTH_PASSWORD_VALIDATORS = [

    {
        "NAME":
            "django.contrib.auth.password_validation."
            "UserAttributeSimilarityValidator",
    },

    {
        "NAME":
            "django.contrib.auth.password_validation."
            "MinimumLengthValidator",
        "OPTIONS": {
            "min_length": 10,
        },
    },

    {
        "NAME":
            "django.contrib.auth.password_validation."
            "CommonPasswordValidator",
    },

    {
        "NAME":
            "django.contrib.auth.password_validation."
            "NumericPasswordValidator",
    },
]


# ============================================================
# INTERNATIONALIZATION
# ============================================================

LANGUAGE_CODE = "en-us"

TIME_ZONE = "Africa/Dar_es_Salaam"

USE_I18N = True

USE_TZ = True


# ============================================================
# STATIC FILES
# ============================================================
STATIC_URL = "/static/"

STATICFILES_DIRS = [
    BASE_DIR / "static",
]

STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# ============================================================
# MEDIA FILES
# ============================================================

MEDIA_URL = "media/"

MEDIA_ROOT = BASE_DIR / "media"


# ============================================================
# DEFAULT PRIMARY KEY
# ============================================================

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ============================================================
# SECURITY SETTINGS
# ============================================================

# Browser should only communicate with the server over HTTPS
# when production is using HTTPS.
SECURE_SSL_REDIRECT = (
    not DEBUG
)

# Prevent browsers from guessing content types.
SECURE_CONTENT_TYPE_NOSNIFF = True

# Protect against clickjacking.
X_FRAME_OPTIONS = "DENY"

# Referrer policy.
SECURE_REFERRER_POLICY = "same-origin"


# ============================================================
# SESSION SECURITY
# ============================================================

SESSION_COOKIE_HTTPONLY = True

SESSION_COOKIE_SAMESITE = "Lax"

SESSION_COOKIE_SECURE = not DEBUG


# ============================================================
# CSRF SECURITY
# ============================================================

CSRF_COOKIE_HTTPONLY = False

CSRF_COOKIE_SAMESITE = "Lax"

CSRF_COOKIE_SECURE = not DEBUG


# ============================================================
# HSTS
# ============================================================

# Enabled only in production.
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0

SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG

SECURE_HSTS_PRELOAD = not DEBUG


# ============================================================
# LOGIN / AUTHENTICATION
# ============================================================

LOGIN_URL = "/accounts/login/"

LOGIN_REDIRECT_URL = "/"

LOGOUT_REDIRECT_URL = "/accounts/login/"


# ============================================================
# MESSAGE STORAGE
# ============================================================

MESSAGE_TAGS = {}


# ============================================================
# FILE UPLOAD LIMITS
# ============================================================

# Conservative limits for this small business system.
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024