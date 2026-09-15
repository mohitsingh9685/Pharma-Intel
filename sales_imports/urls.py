from django.urls import path

from . import views


app_name = "sales_imports"

urlpatterns = [
    path("", views.import_list, name="list"),
    path("new/", views.import_create, name="create"),
    path("<uuid:pk>/", views.import_detail, name="detail"),
]
