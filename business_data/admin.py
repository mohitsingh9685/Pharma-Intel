"""Safe Admin views for Calendar and immutable Sales facts."""

from datetime import date

from django.contrib import admin

from .models import CalendarDate, SalesTransaction


@admin.register(CalendarDate)
class CalendarDateAdmin(admin.ModelAdmin):
    list_display = (
        "date",
        "weekday_name",
        "year_month_label",
        "year_quarter_label",
        "iso_week",
        "is_weekend",
    )
    list_filter = ("year", "quarter", "month", "is_weekend")
    search_fields = ("year_month_label", "year_quarter_label")
    ordering = ("-date",)
    list_per_page = 50
    show_full_result_count = False

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_search_results(self, request, queryset, search_term):
        queryset, may_have_duplicates = super().get_search_results(
            request,
            queryset,
            search_term,
        )
        normalized_term = search_term.strip()
        exact_date = None
        try:
            exact_date = date.fromisoformat(normalized_term)
        except ValueError:
            if len(normalized_term) == 8 and normalized_term.isascii():
                try:
                    exact_date = date(
                        int(normalized_term[:4]),
                        int(normalized_term[4:6]),
                        int(normalized_term[6:]),
                    )
                except ValueError:
                    pass
        if exact_date is not None:
            queryset |= self.model.objects.filter(date=exact_date)
        return queryset, may_have_duplicates


@admin.register(SalesTransaction)
class SalesTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "calendar_date",
        "source_system",
        "source_record_id",
        "product",
        "territory",
        "quantity",
        "revenue_amount",
        "currency_code",
    )
    list_filter = (
        "currency_code",
        "source_system",
        ("product", admin.RelatedOnlyFieldListFilter),
        ("territory", admin.RelatedOnlyFieldListFilter),
    )
    search_fields = (
        "source_system",
        "source_record_id",
        "product__code",
        "product__name",
        "territory__code",
        "territory__name",
        "hospital__code",
        "hospital__name",
        "sales_representative__code",
        "sales_representative__name",
    )
    autocomplete_fields = (
        "calendar_date",
        "product",
        "territory",
        "hospital",
        "sales_representative",
    )
    list_select_related = (
        "calendar_date",
        "product",
        "territory",
        "hospital",
        "sales_representative",
        "created_by",
    )
    ordering = ("-calendar_date", "-id")
    readonly_fields = ("created_by", "created_at")
    list_per_page = 50
    show_full_result_count = False
    save_on_top = True
    fieldsets = (
        (
            "Source identity",
            {"fields": ("source_system", "source_record_id")},
        ),
        (
            "Business dimensions",
            {
                "fields": (
                    "calendar_date",
                    "product",
                    "territory",
                    "hospital",
                    "sales_representative",
                )
            },
        ),
        (
            "Measures",
            {"fields": ("quantity", "revenue_amount", "currency_code")},
        ),
        ("Record history", {"fields": ("created_by", "created_at")}),
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        if obj is not None:
            return False
        return super().has_change_permission(request, obj)

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return self.readonly_fields
        return tuple(field.name for field in self.model._meta.fields)
