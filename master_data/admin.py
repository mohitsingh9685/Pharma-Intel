from copy import deepcopy

from django.contrib import admin
from django.utils import timezone

from .models import (
    HealthcareProfessional,
    HealthcareProfessionalHospitalAffiliation,
    HealthcareProfessionalSpecialtyAssignment,
    Hospital,
    HospitalTerritoryAssignment,
    Product,
    SalesRepresentative,
    SalesRepresentativeTerritoryAssignment,
    Specialty,
    Territory,
    TerritoryHierarchyAssignment,
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


@admin.register(Specialty)
class SpecialtyAdmin(CodedMasterDataAdmin):
    pass


class EffectiveStatusFilter(admin.SimpleListFilter):
    title = "effective status"
    parameter_name = "effective_status"

    def lookups(self, request, model_admin):
        return (
            ("current", "Current"),
            ("upcoming", "Upcoming"),
            ("ended", "Ended"),
        )

    def queryset(self, request, queryset):
        today = timezone.localdate()
        if self.value() == "current":
            return queryset.effective_on(today)
        if self.value() == "upcoming":
            return queryset.filter(effective_from__gt=today)
        if self.value() == "ended":
            return queryset.filter(effective_to__lt=today)
        return queryset


class EffectiveDatedRelationshipAdmin(admin.ModelAdmin):
    relationship_fields = ()
    classification_fields = ()
    list_filter = (EffectiveStatusFilter, "effective_from", "effective_to")
    date_hierarchy = "effective_from"
    ordering = ("-effective_from",)
    readonly_fields = ("created_at", "updated_at")
    list_per_page = 50
    show_full_result_count = False
    save_on_top = True

    def has_delete_permission(self, request, obj=None):
        return False

    def get_form(self, request, obj=None, **kwargs):
        base_form = super().get_form(request, obj, **kwargs)

        class RelationshipForm(base_form):
            pass

        RelationshipForm.base_fields = deepcopy(base_form.base_fields)
        if obj is None:
            for field_name in self.relationship_fields:
                field = RelationshipForm.base_fields.get(field_name)
                if field is not None and hasattr(field.queryset.model, "is_active"):
                    field.queryset = field.queryset.filter(is_active=True)
            return RelationshipForm

        for field_name in (
            *self.relationship_fields,
            *self.classification_fields,
            "effective_from",
        ):
            field = RelationshipForm.base_fields.get(field_name)
            if field is not None:
                field.disabled = True
        return RelationshipForm

    def get_fieldsets(self, request, obj=None):
        return (
            (
                "Relationship",
                {
                    "fields": (
                        *self.relationship_fields,
                        *self.classification_fields,
                    )
                },
            ),
            (
                "Effective period",
                {
                    "fields": ("effective_from", "effective_to"),
                    "description": (
                        "Both dates are included. Leave the end date blank while "
                        "the assignment is ongoing."
                    ),
                },
            ),
            ("Record history", {"fields": ("created_at", "updated_at")}),
        )

    @admin.display(description="Status")
    def effective_status(self, obj):
        today = timezone.localdate()
        if obj.effective_from > today:
            return "Upcoming"
        if obj.effective_to and obj.effective_to < today:
            return "Ended"
        return "Current"


@admin.register(TerritoryHierarchyAssignment)
class TerritoryHierarchyAssignmentAdmin(EffectiveDatedRelationshipAdmin):
    relationship_fields = ("parent", "child")
    autocomplete_fields = relationship_fields
    list_display = (
        "parent",
        "child",
        "effective_from",
        "effective_to",
        "effective_status",
        "updated_at",
    )
    list_select_related = relationship_fields
    search_fields = (
        "parent__code",
        "parent__name",
        "child__code",
        "child__name",
    )


@admin.register(HospitalTerritoryAssignment)
class HospitalTerritoryAssignmentAdmin(EffectiveDatedRelationshipAdmin):
    relationship_fields = ("hospital", "territory")
    autocomplete_fields = relationship_fields
    list_display = (
        "hospital",
        "territory",
        "effective_from",
        "effective_to",
        "effective_status",
        "updated_at",
    )
    list_select_related = relationship_fields
    search_fields = (
        "hospital__code",
        "hospital__name",
        "territory__code",
        "territory__name",
    )


@admin.register(HealthcareProfessionalHospitalAffiliation)
class HealthcareProfessionalHospitalAffiliationAdmin(
    EffectiveDatedRelationshipAdmin
):
    relationship_fields = ("healthcare_professional", "hospital")
    classification_fields = ("is_primary",)
    autocomplete_fields = relationship_fields
    list_display = (
        "healthcare_professional",
        "hospital",
        "is_primary",
        "effective_from",
        "effective_to",
        "effective_status",
        "updated_at",
    )
    list_filter = (*EffectiveDatedRelationshipAdmin.list_filter, "is_primary")
    list_select_related = relationship_fields
    search_fields = (
        "healthcare_professional__code",
        "healthcare_professional__name",
        "hospital__code",
        "hospital__name",
    )


@admin.register(HealthcareProfessionalSpecialtyAssignment)
class HealthcareProfessionalSpecialtyAssignmentAdmin(
    EffectiveDatedRelationshipAdmin
):
    relationship_fields = ("healthcare_professional", "specialty")
    classification_fields = ("is_primary",)
    autocomplete_fields = relationship_fields
    list_display = (
        "healthcare_professional",
        "specialty",
        "is_primary",
        "effective_from",
        "effective_to",
        "effective_status",
        "updated_at",
    )
    list_filter = (*EffectiveDatedRelationshipAdmin.list_filter, "is_primary")
    list_select_related = relationship_fields
    search_fields = (
        "healthcare_professional__code",
        "healthcare_professional__name",
        "specialty__code",
        "specialty__name",
    )


@admin.register(SalesRepresentativeTerritoryAssignment)
class SalesRepresentativeTerritoryAssignmentAdmin(
    EffectiveDatedRelationshipAdmin
):
    relationship_fields = ("sales_representative", "territory")
    classification_fields = ("assignment_type",)
    autocomplete_fields = relationship_fields
    list_display = (
        "sales_representative",
        "territory",
        "assignment_type",
        "effective_from",
        "effective_to",
        "effective_status",
        "updated_at",
    )
    list_filter = (
        *EffectiveDatedRelationshipAdmin.list_filter,
        "assignment_type",
    )
    list_select_related = relationship_fields
    search_fields = (
        "sales_representative__code",
        "sales_representative__name",
        "sales_representative__user__email",
        "territory__code",
        "territory__name",
    )
