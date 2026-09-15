from django.contrib import admin

from accounts.models import User

from .models import SalesImport


@admin.register(SalesImport)
class SalesImportAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "original_filename",
        "source_system",
        "file_format",
        "row_contract",
        "status",
        "uploaded_by",
        "created_at",
    )
    list_filter = ("status", "file_format", "row_contract", "source_system")
    search_fields = ("id", "original_filename", "source_system", "sha256")
    list_select_related = ("uploaded_by",)
    ordering = ("-created_at",)
    readonly_fields = tuple(field.name for field in SalesImport._meta.fields)
    list_per_page = 50
    show_full_result_count = False

    @staticmethod
    def _is_application_administrator(request):
        return request.user.is_active and request.user.role == User.Role.ADMIN

    def has_module_permission(self, request):
        return self._is_application_administrator(request)

    def has_view_permission(self, request, obj=None):
        return self._is_application_administrator(request) and super().has_view_permission(
            request,
            obj,
        )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        if not self._is_application_administrator(request):
            return False
        if obj is not None:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False
