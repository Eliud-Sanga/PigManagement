"""
Main URL configuration for Pig Management System.
"""

from django.contrib import admin
from django.urls import path, include
from apps.users.views import login_view, logout_view

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('apps.users.urls')),  # ✅ BADILISHA - INCLUDE NAMESPACE
    path('', include('apps.pigs.urls')),
]