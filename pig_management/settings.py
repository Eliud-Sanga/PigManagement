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
# ENVIRONMENT
# ============================================================

# Supported environments:
#
# Development:
#     DJANGO_ENV=development
#
# Production:
#     DJANGO_ENV=production
#
# The default is development so that the project can run
# safely on a local machine without forcing HTTPS.
DJANGO_ENV = os.environ.get(
    "DJANGO_ENV",
    "development",
).strip().lower()


# ============================================================
# DEBUG
# ============================================================

# Development:
#     DJANGO_DEBUG=True
#
# Production:
#     DJANGO_DEBUG=False
#
# If DJANGO_DEBUG is not explicitly defined:
#
#     development -> True
#     production  -> False
#
DEBUG = os.environ.get(
    "DJANGO_DEBUG",
    "True" if DJANGO_ENV == "development" else "False",
).strip().lower() in (
    "true",
    "1",
    "yes",
)


# ============================================================
# PRODUCTION FLAG
# ============================================================

# This flag is intentionally based on the environment rather
# than directly on DEBUG.
#
# This gives us a clear separation between local development
# and production security behavior.
IS_PRODUCTION = (
    DJANGO_ENV == "production"
)


# ============================================================
# SECURITY
# ============================================================

# Production: set DJANGO_SECRET_KEY in environment.
#
# Development fallback keeps the current project runnable.
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-development-only-change-me",
)


# ============================================================
# ALLOWED HOSTS
# ============================================================

ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    "192.168.0.110",
    "home-management-system-ycto.onrender.com",
]


# ============================================================
# CSRF TRUSTED ORIGINS
# ============================================================

CSRF_TRUSTED_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://192.168.0.110:8000",
    "https://home-management-system-ycto.onrender.com",
]


# ============================================================
# APPLICATIONS
# ============================================================

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",

    "apps.pigs.apps.PigsConfig",
    "apps.users.apps.UsersConfig",
]


# ============================================================
# MIDDLEWARE
# ============================================================

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",

    "whitenoise.middleware.WhiteNoiseMiddleware",

    "django.contrib.sessions.middleware.SessionMiddleware",

    "django.middleware.common.CommonMiddleware",

    "django.middleware.csrf.CsrfViewMiddleware",

    "django.contrib.auth.middleware.AuthenticationMiddleware",

    "apps.core.middleware.NoCacheAuthenticatedMiddleware",

    "django.contrib.messages.middleware.MessageMiddleware",

    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# ============================================================
# AUTHENTICATION BACKENDS
# ============================================================

AUTHENTICATION_BACKENDS = [
    "apps.users.backends.CustomUserBackend",
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
        "BACKEND":
            "django.template.backends.django.DjangoTemplates",

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

DATABASE_URL = os.environ.get(
    "DATABASE_URL"
)


if DATABASE_URL:

    # --------------------------------------------------------
    # PostgreSQL / Neon / Production Database
    # --------------------------------------------------------

    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,

            conn_max_age=600,

            # DATABASE_URL already contains the required
            # SSL configuration for the hosted database.
            ssl_require=True,
        )
    }

else:

    # --------------------------------------------------------
    # LOCAL FALLBACK DATABASE
    # --------------------------------------------------------
    #
    # This fallback exists only when DATABASE_URL is missing.
    #
    # If your local .env contains DATABASE_URL, PostgreSQL
    # will still be used.
    #
    DATABASES = {
        "default": {
            "ENGINE":
                "django.db.backends.sqlite3",

            "NAME":
                BASE_DIR / "db.sqlite3",
        }
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

STATIC_ROOT = (
    BASE_DIR / "staticfiles"
)


# ============================================================
# STATIC FILE STORAGE
# ============================================================

STORAGES = {
    "default": {
        "BACKEND":
            "django.core.files.storage.FileSystemStorage",
    },

    "staticfiles": {
        "BACKEND":
            "whitenoise.storage."
            "CompressedManifestStaticFilesStorage",
    },
}


# ============================================================
# MEDIA FILES
# ============================================================

MEDIA_URL = "/media/"

MEDIA_ROOT = (
    BASE_DIR / "media"
)


# ============================================================
# DEFAULT PRIMARY KEY
# ============================================================

DEFAULT_AUTO_FIELD = (
    "django.db.models.BigAutoField"
)


# ============================================================
# SECURITY SETTINGS
# ============================================================

# ------------------------------------------------------------
# HTTPS REDIRECT
# ------------------------------------------------------------
#
# LOCAL DEVELOPMENT:
#
#     http://127.0.0.1:8000
#
#     SECURE_SSL_REDIRECT = False
#
# PRODUCTION:
#
#     https://home-management-system-ycto.onrender.com
#
#     SECURE_SSL_REDIRECT = True
#
# This is the important fix for the local development problem.
#
SECURE_SSL_REDIRECT = IS_PRODUCTION


# ------------------------------------------------------------
# REVERSE PROXY / RENDER HTTPS
# ------------------------------------------------------------
#
# Render terminates HTTPS at its proxy before forwarding
# the request to Django.
#
# This tells Django that the original client connection
# was HTTPS when Render sends:
#
#     X-Forwarded-Proto: https
#
SECURE_PROXY_SSL_HEADER = (
    "HTTP_X_FORWARDED_PROTO",
    "https",
)


# ------------------------------------------------------------
# CONTENT TYPE SECURITY
# ------------------------------------------------------------

SECURE_CONTENT_TYPE_NOSNIFF = True


# ------------------------------------------------------------
# CLICKJACKING PROTECTION
# ------------------------------------------------------------

X_FRAME_OPTIONS = "DENY"


# ------------------------------------------------------------
# REFERRER POLICY
# ------------------------------------------------------------

SECURE_REFERRER_POLICY = "same-origin"


# ============================================================
# SESSION SECURITY
# ============================================================

SESSION_COOKIE_HTTPONLY = True

SESSION_COOKIE_SAMESITE = "Lax"


# Local:
#
#     False
#
# Production:
#
#     True
#
# This allows normal HTTP cookies during local development
# while protecting authentication cookies in production.
SESSION_COOKIE_SECURE = IS_PRODUCTION


# ============================================================
# CSRF SECURITY
# ============================================================

# JavaScript is allowed to read the CSRF cookie when needed.
CSRF_COOKIE_HTTPONLY = False

CSRF_COOKIE_SAMESITE = "Lax"


# Local:
#
#     False
#
# Production:
#
#     True
#
CSRF_COOKIE_SECURE = IS_PRODUCTION


# ============================================================
# HSTS
# ============================================================

# HSTS is intentionally disabled during local development.
#
# Production:
#
#     31536000 seconds = 1 year
#
# Local:
#
#     0
#
SECURE_HSTS_SECONDS = (
    31536000
    if IS_PRODUCTION
    else 0
)


SECURE_HSTS_INCLUDE_SUBDOMAINS = (
    IS_PRODUCTION
)


SECURE_HSTS_PRELOAD = (
    IS_PRODUCTION
)


# ============================================================
# LOGIN / AUTHENTICATION
# ============================================================

LOGIN_URL = "/accounts/login/"

LOGIN_REDIRECT_URL = "/"

LOGOUT_REDIRECT_URL = (
    "/accounts/login/"
)


# ============================================================
# MESSAGE STORAGE
# ============================================================

MESSAGE_TAGS = {}


# ============================================================
# FILE UPLOAD LIMITS
# ============================================================

# Conservative limits for this small business system.

DATA_UPLOAD_MAX_MEMORY_SIZE = (
    5 * 1024 * 1024
)

FILE_UPLOAD_MAX_MEMORY_SIZE = (
    5 * 1024 * 1024
)