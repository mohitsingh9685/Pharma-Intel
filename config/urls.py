"""Top-level URL routes for the application."""

from django.contrib import admin
from django.urls import include, path

from . import views


urlpatterns = [
    path("", views.home, name="home"),
    path("accounts/", include("django.contrib.auth.urls")),
    path("sales-imports/", include("sales_imports.urls")),
    path("admin/", admin.site.urls),
]
