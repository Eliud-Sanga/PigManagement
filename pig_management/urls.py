"""
Main URL configuration for Pig Management System.
"""

from django.contrib import admin
from django.urls import path, include

from .pwa import service_worker


urlpatterns = [

    path(
        "service-worker.js",
        service_worker,
        name="service_worker"
    ),

    path(
        "admin/",
        admin.site.urls
    ),

    path(
        "accounts/",
        include("apps.users.urls")
    ),

    path(
        "",
        include("apps.pigs.urls")
    ),

]