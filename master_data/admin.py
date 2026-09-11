from django.contrib import admin

from .models import Product


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("code", "name")
    ordering = ("name", "code")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("code", "name", "is_active")}),
        ("Record history", {"fields": ("created_at", "updated_at")}),
    )

    def has_delete_permission(self, request, obj=None):
        return False

