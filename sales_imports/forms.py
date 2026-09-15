from django import forms
from django.conf import settings

from .validation import inspect_sales_file


class SalesImportUploadForm(forms.Form):
    source_system = forms.RegexField(
        regex=r"^[A-Za-z0-9]([A-Za-z0-9 ._:/-]*[A-Za-z0-9])?$",
        max_length=64,
        strip=True,
        help_text="Example: ERP, SAP, or DISTRIBUTOR-PORTAL.",
        error_messages={
            "invalid": (
                "Use letters, numbers, spaces, dots, underscores, colons, "
                "slashes, or hyphens."
            )
        },
    )
    file = forms.FileField(
        max_length=255,
        allow_empty_file=False,
        help_text=(
            "Upload a UTF-8 CSV or an .xlsx package. Row and column rules are "
            "checked during processing."
        ),
    )

    inspection = None

    def clean_source_system(self):
        return self.cleaned_data["source_system"].upper()

    def clean_file(self):
        uploaded_file = self.cleaned_data["file"]
        self.inspection = inspect_sales_file(
            uploaded_file,
            max_bytes=settings.SALES_IMPORT_MAX_UPLOAD_BYTES,
        )
        return uploaded_file
