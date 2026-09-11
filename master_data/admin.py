from django.contrib import admin

from .models import (
    HealthcareProfessional,
    Hospital,
    Product,
    SalesRepresentative,
    Territory,
)


class CodedMasterDataAdmin(admin.ModelAdmin):
    immutable_fields = ("code",)
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

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return self.readonly_fields
        return (*self.readonly_fields, *self.immutable_fields)


@admin.register(Product)
class ProductAdmin(CodedMasterDataAdmin):
    pass


@admin.register(Territory)
class TerritoryAdmin(CodedMasterDataAdmin):
    immutable_fields = ("code", "level")
    list_display = ("code", "name", "level", "is_active", "updated_at")
    list_filter = ("level", "is_active")
    fieldsets = (
        (None, {"fields": ("code", "name", "level", "is_active")}),
        ("Record history", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(Hospital)
class HospitalAdmin(CodedMasterDataAdmin):
    pass


@admin.register(HealthcareProfessional)
class HealthcareProfessionalAdmin(CodedMasterDataAdmin):
    pass


@admin.register(SalesRepresentative)
class SalesRepresentativeAdmin(CodedMasterDataAdmin):
    autocomplete_fields = ("user",)
    list_display = ("code", "name", "user", "is_active", "updated_at")
    list_select_related = ("user",)
    search_fields = (*CodedMasterDataAdmin.search_fields, "user__email")
    fieldsets = (
        (None, {"fields": ("code", "name", "user", "is_active")}),
        ("Record history", {"fields": ("created_at", "updated_at")}),
    )
