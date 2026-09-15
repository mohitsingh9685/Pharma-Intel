from django.contrib import admin

from accounts.models import User

from .models import (
    SalesImport,
    SalesImportAttempt,
    SalesImportIssue,
    SalesImportJob,
    SalesImportStagedRow,
)


class AdministratorReadOnlyAdmin(admin.ModelAdmin):
    """Expose processing audit data without permitting manual mutation."""

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


@admin.register(SalesImport)
class SalesImportAdmin(AdministratorReadOnlyAdmin):
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


@admin.register(SalesImportJob)
class SalesImportJobAdmin(AdministratorReadOnlyAdmin):
    list_display = (
        "sales_import_id",
        "original_filename",
        "source_system",
        "status",
        "attempt_count",
        "total_rows",
        "invalid_rows",
        "inserted_rows",
        "reused_rows",
        "updated_at",
    )
    list_filter = ("status", "sales_import__source_system")
    search_fields = (
        "sales_import__id",
        "sales_import__original_filename",
        "sales_import__source_system",
    )
    list_select_related = ("sales_import",)
    ordering = ("-created_at",)
    readonly_fields = tuple(field.name for field in SalesImportJob._meta.fields)

    @admin.display(ordering="sales_import__original_filename")
    def original_filename(self, obj):
        return obj.sales_import.original_filename

    @admin.display(ordering="sales_import__source_system")
    def source_system(self, obj):
        return obj.sales_import.source_system


@admin.register(SalesImportAttempt)
class SalesImportAttemptAdmin(AdministratorReadOnlyAdmin):
    list_display = (
        "id",
        "job_id",
        "attempt_number",
        "worker_id",
        "phase",
        "outcome",
        "total_rows",
        "invalid_rows",
        "started_at",
        "finished_at",
    )
    list_filter = ("outcome", "phase")
    search_fields = (
        "id",
        "job__sales_import__id",
        "job__sales_import__original_filename",
        "worker_id",
        "error_code",
    )
    list_select_related = ("job", "job__sales_import")
    ordering = ("-started_at",)
    readonly_fields = tuple(field.name for field in SalesImportAttempt._meta.fields)
    raw_id_fields = ("job",)


@admin.register(SalesImportStagedRow)
class SalesImportStagedRowAdmin(AdministratorReadOnlyAdmin):
    list_display = (
        "id",
        "attempt_id",
        "row_number",
        "source_record_id",
        "transaction_date",
        "product_code",
        "territory_code",
        "outcome",
        "sales_transaction_id",
    )
    list_filter = ("outcome", "currency_code")
    search_fields = (
        "source_record_id",
        "product_code",
        "territory_code",
        "hospital_code",
        "sales_representative_code",
        "attempt__job__sales_import__id",
    )
    list_select_related = ("attempt",)
    ordering = ("-id",)
    readonly_fields = tuple(
        field.name for field in SalesImportStagedRow._meta.fields
    )
    raw_id_fields = (
        "attempt",
        "calendar_date",
        "product",
        "territory",
        "hospital",
        "sales_representative",
        "sales_transaction",
    )


@admin.register(SalesImportIssue)
class SalesImportIssueAdmin(AdministratorReadOnlyAdmin):
    list_display = (
        "id",
        "attempt_id",
        "row_number",
        "column",
        "code",
        "phase",
        "created_at",
    )
    list_filter = ("phase", "code")
    search_fields = (
        "attempt__job__sales_import__id",
        "code",
        "column",
        "message",
    )
    list_select_related = ("attempt",)
    ordering = ("-created_at", "-id")
    readonly_fields = tuple(field.name for field in SalesImportIssue._meta.fields)
    raw_id_fields = ("attempt",)
