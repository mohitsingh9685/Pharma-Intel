from datetime import date

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory, TestCase

from .models import (
    HealthcareProfessional,
    HealthcareProfessionalHospitalAffiliation,
    HealthcareProfessionalSpecialtyAssignment,
    Hospital,
    HospitalTerritoryAssignment,
    SalesRepresentative,
    SalesRepresentativeTerritoryAssignment,
    Specialty,
    Territory,
    TerritoryHierarchyAssignment,
)


class RelationshipFixtures(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.region = Territory.objects.create(
            code="REG-NORTH",
            name="North Region",
            level=Territory.Level.REGION,
        )
        cls.other_region = Territory.objects.create(
            code="REG-SOUTH",
            name="South Region",
            level=Territory.Level.REGION,
        )
        cls.territory = Territory.objects.create(
            code="TER-NORTH-01",
            name="North Territory 01",
            level=Territory.Level.TERRITORY,
        )
        cls.other_territory = Territory.objects.create(
            code="TER-NORTH-02",
            name="North Territory 02",
            level=Territory.Level.TERRITORY,
        )
        cls.hospital = Hospital.objects.create(
            code="HOS-001",
            name="Central Hospital",
        )
        cls.other_hospital = Hospital.objects.create(
            code="HOS-002",
            name="Community Hospital",
        )
        cls.third_hospital = Hospital.objects.create(
            code="HOS-003",
            name="Specialist Hospital",
        )
        cls.hcp = HealthcareProfessional.objects.create(
            code="HCP-001",
            name="Doctor One",
        )
        cls.cardiology = Specialty.objects.create(
            code="CARD",
            name="Cardiology",
        )
        cls.neurology = Specialty.objects.create(
            code="NEUR",
            name="Neurology",
        )
        cls.oncology = Specialty.objects.create(
            code="ONCO",
            name="Oncology",
        )
        cls.representative = SalesRepresentative.objects.create(
            code="REP-001",
            name="Representative One",
        )
        cls.other_representative = SalesRepresentative.objects.create(
            code="REP-002",
            name="Representative Two",
        )
        cls.third_representative = SalesRepresentative.objects.create(
            code="REP-003",
            name="Representative Three",
        )


class SpecialtyModelTests(RelationshipFixtures):
    def test_specialty_uses_shared_identity_rules(self):
        specialty = Specialty.objects.create(
            code="  derm ",
            name="  Dermatology  ",
        )

        self.assertEqual(specialty.code, "DERM")
        self.assertEqual(specialty.name, "Dermatology")

        with self.assertRaises(IntegrityError), transaction.atomic():
            Specialty.objects.create(code="derm", name="Duplicate")


class EffectiveDateTests(RelationshipFixtures):
    def test_invalid_date_order_is_rejected_by_model_and_database(self):
        assignment = HospitalTerritoryAssignment(
            hospital=self.hospital,
            territory=self.territory,
            effective_from=date(2026, 2, 1),
            effective_to=date(2026, 1, 31),
        )

        with self.assertRaises(ValidationError):
            assignment.full_clean()

        with self.assertRaises(IntegrityError), transaction.atomic():
            HospitalTerritoryAssignment.objects.create(
                hospital=self.hospital,
                territory=self.territory,
                effective_from=date(2026, 2, 1),
                effective_to=date(2026, 1, 31),
            )

    def test_as_of_query_uses_inclusive_boundaries(self):
        assignment = HospitalTerritoryAssignment.objects.create(
            hospital=self.hospital,
            territory=self.territory,
            effective_from=date(2026, 1, 1),
            effective_to=date(2026, 1, 31),
        )

        self.assertIn(
            assignment,
            HospitalTerritoryAssignment.objects.effective_on(date(2026, 1, 1)),
        )
        self.assertIn(
            assignment,
            HospitalTerritoryAssignment.objects.effective_on(date(2026, 1, 31)),
        )
        self.assertNotIn(
            assignment,
            HospitalTerritoryAssignment.objects.effective_on(date(2026, 2, 1)),
        )

    def test_inactive_identity_is_rejected_for_current_assignment(self):
        self.territory.is_active = False
        self.territory.save(update_fields=("is_active", "updated_at"))

        current_assignment = HospitalTerritoryAssignment(
            hospital=self.hospital,
            territory=self.territory,
            effective_from=date(2026, 1, 1),
        )
        with self.assertRaises(ValidationError):
            current_assignment.full_clean()

        historical_assignment = HospitalTerritoryAssignment(
            hospital=self.hospital,
            territory=self.territory,
            effective_from=date(2020, 1, 1),
            effective_to=date(2020, 12, 31),
        )
        historical_assignment.full_clean()

    def test_existing_assignment_can_be_closed_after_identity_is_deactivated(self):
        assignment = HospitalTerritoryAssignment.objects.create(
            hospital=self.hospital,
            territory=self.territory,
            effective_from=date(2026, 1, 1),
        )
        self.territory.is_active = False
        self.territory.save(update_fields=("is_active", "updated_at"))
        assignment.effective_to = date(2026, 12, 31)

        assignment.full_clean()


class TerritoryHierarchyTests(RelationshipFixtures):
    def test_higher_level_parent_is_required(self):
        valid = TerritoryHierarchyAssignment(
            parent=self.region,
            child=self.territory,
            effective_from=date(2026, 1, 1),
        )
        valid.full_clean()

        invalid = TerritoryHierarchyAssignment(
            parent=self.territory,
            child=self.region,
            effective_from=date(2026, 1, 1),
        )
        with self.assertRaises(ValidationError):
            invalid.full_clean()

        with self.assertRaises(IntegrityError), transaction.atomic():
            TerritoryHierarchyAssignment.objects.create(
                parent=self.territory,
                child=self.region,
                effective_from=date(2026, 1, 1),
            )

    def test_child_cannot_have_overlapping_parents(self):
        TerritoryHierarchyAssignment.objects.create(
            parent=self.region,
            child=self.territory,
            effective_from=date(2026, 1, 1),
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            TerritoryHierarchyAssignment.objects.create(
                parent=self.other_region,
                child=self.territory,
                effective_from=date(2026, 2, 1),
            )


class HospitalTerritoryAssignmentTests(RelationshipFixtures):
    def test_hospital_requires_leaf_territory(self):
        assignment = HospitalTerritoryAssignment(
            hospital=self.hospital,
            territory=self.region,
            effective_from=date(2026, 1, 1),
        )

        with self.assertRaises(ValidationError):
            assignment.full_clean()

        with self.assertRaises(IntegrityError), transaction.atomic():
            HospitalTerritoryAssignment.objects.create(
                hospital=self.hospital,
                territory=self.region,
                effective_from=date(2026, 1, 1),
            )

    def test_adjacent_assignments_are_allowed_but_overlaps_are_rejected(self):
        HospitalTerritoryAssignment.objects.create(
            hospital=self.hospital,
            territory=self.territory,
            effective_from=date(2026, 1, 1),
            effective_to=date(2026, 1, 31),
        )
        HospitalTerritoryAssignment.objects.create(
            hospital=self.hospital,
            territory=self.other_territory,
            effective_from=date(2026, 2, 1),
        )

        overlapping = HospitalTerritoryAssignment(
            hospital=self.hospital,
            territory=self.territory,
            effective_from=date(2026, 1, 15),
            effective_to=date(2026, 2, 15),
        )

        with self.assertRaises(ValidationError):
            overlapping.full_clean()

        with self.assertRaises(IntegrityError), transaction.atomic():
            HospitalTerritoryAssignment.objects.bulk_create([overlapping])

    def test_linked_identity_is_protected_from_deletion(self):
        HospitalTerritoryAssignment.objects.create(
            hospital=self.hospital,
            territory=self.territory,
            effective_from=date(2026, 1, 1),
        )

        with self.assertRaises(ProtectedError):
            self.hospital.delete()

    def test_territory_level_cannot_change_after_creation(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Territory.objects.filter(pk=self.other_territory.pk).update(
                level=Territory.Level.AREA
            )


class HcpAssignmentTests(RelationshipFixtures):
    def test_hcp_may_have_multiple_hospitals_but_only_one_primary(self):
        HealthcareProfessionalHospitalAffiliation.objects.create(
            healthcare_professional=self.hcp,
            hospital=self.hospital,
            is_primary=True,
            effective_from=date(2026, 1, 1),
        )
        HealthcareProfessionalHospitalAffiliation.objects.create(
            healthcare_professional=self.hcp,
            hospital=self.other_hospital,
            is_primary=False,
            effective_from=date(2026, 1, 1),
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            HealthcareProfessionalHospitalAffiliation.objects.create(
                healthcare_professional=self.hcp,
                hospital=self.third_hospital,
                is_primary=True,
                effective_from=date(2026, 2, 1),
            )

    def test_hcp_may_have_multiple_specialties_but_only_one_primary(self):
        HealthcareProfessionalSpecialtyAssignment.objects.create(
            healthcare_professional=self.hcp,
            specialty=self.cardiology,
            is_primary=True,
            effective_from=date(2026, 1, 1),
        )
        HealthcareProfessionalSpecialtyAssignment.objects.create(
            healthcare_professional=self.hcp,
            specialty=self.neurology,
            is_primary=False,
            effective_from=date(2026, 1, 1),
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            HealthcareProfessionalSpecialtyAssignment.objects.create(
                healthcare_professional=self.hcp,
                specialty=self.oncology,
                is_primary=True,
                effective_from=date(2026, 3, 1),
            )


class SalesRepresentativeAssignmentTests(RelationshipFixtures):
    def test_support_coverage_can_overlap_but_primary_ownership_cannot(self):
        SalesRepresentativeTerritoryAssignment.objects.create(
            sales_representative=self.representative,
            territory=self.territory,
            assignment_type=(
                SalesRepresentativeTerritoryAssignment.AssignmentType.PRIMARY
            ),
            effective_from=date(2026, 1, 1),
        )
        SalesRepresentativeTerritoryAssignment.objects.create(
            sales_representative=self.other_representative,
            territory=self.territory,
            assignment_type=(
                SalesRepresentativeTerritoryAssignment.AssignmentType.SUPPORT
            ),
            effective_from=date(2026, 1, 1),
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            SalesRepresentativeTerritoryAssignment.objects.create(
                sales_representative=self.third_representative,
                territory=self.territory,
                assignment_type=(
                    SalesRepresentativeTerritoryAssignment.AssignmentType.PRIMARY
                ),
                effective_from=date(2026, 2, 1),
            )

    def test_representative_requires_leaf_territory(self):
        assignment = SalesRepresentativeTerritoryAssignment(
            sales_representative=self.representative,
            territory=self.region,
            assignment_type=(
                SalesRepresentativeTerritoryAssignment.AssignmentType.PRIMARY
            ),
            effective_from=date(2026, 1, 1),
        )

        with self.assertRaises(ValidationError):
            assignment.full_clean()

        with self.assertRaises(IntegrityError), transaction.atomic():
            SalesRepresentativeTerritoryAssignment.objects.create(
                sales_representative=self.representative,
                territory=self.region,
                assignment_type=(
                    SalesRepresentativeTerritoryAssignment.AssignmentType.PRIMARY
                ),
                effective_from=date(2026, 1, 1),
            )


class RelationshipAdminTests(RelationshipFixtures):
    def test_change_form_keeps_immutable_fields_and_reports_overlap(self):
        HospitalTerritoryAssignment.objects.create(
            hospital=self.hospital,
            territory=self.territory,
            effective_from=date(2026, 1, 1),
            effective_to=date(2026, 1, 31),
        )
        assignment = HospitalTerritoryAssignment.objects.create(
            hospital=self.hospital,
            territory=self.other_territory,
            effective_from=date(2026, 2, 1),
            effective_to=date(2026, 2, 28),
        )
        HospitalTerritoryAssignment.objects.create(
            hospital=self.hospital,
            territory=self.territory,
            effective_from=date(2026, 3, 1),
        )

        request = RequestFactory().post("/")
        request.user = get_user_model().objects.create_superuser(
            email="relationship-admin@example.com",
            password="test-only-password",
        )
        model_admin = admin.site._registry[HospitalTerritoryAssignment]
        form_class = model_admin.get_form(request, assignment)
        form = form_class(
            data={"effective_to": "2026-03-15"},
            instance=assignment,
        )

        self.assertTrue(form.fields["hospital"].disabled)
        self.assertTrue(form.fields["territory"].disabled)
        self.assertTrue(form.fields["effective_from"].disabled)
        self.assertFalse(form.is_valid())
        self.assertIn("__all__", form.errors)
