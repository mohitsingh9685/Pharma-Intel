from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt, csrf_protect

from .forms import SalesImportUploadForm
from .models import SalesImport, SalesImportAttempt
from .permissions import administrator_required
from .services import receive_sales_import
from .storage import OriginalFileStorageError
from .upload_handlers import BoundedFileUploadHandler


@administrator_required
def import_list(request):
    imports = SalesImport.objects.select_related(
        "uploaded_by",
        "processing_job",
    ).all()
    page = Paginator(imports, 50).get_page(request.GET.get("page"))
    return render(request, "sales_imports/import_list.html", {"page": page})


@csrf_exempt
def import_create(request):
    """Install the sales-only streaming limit before CSRF parses the body."""

    if request.method == "POST":
        request.upload_handlers.insert(0, BoundedFileUploadHandler(request))
    return _protected_import_create(request)


@administrator_required
@csrf_protect
def _protected_import_create(request):
    form = SalesImportUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            result = receive_sales_import(
                uploaded_file=form.cleaned_data["file"],
                inspection=form.inspection,
                source_system=form.cleaned_data["source_system"],
                uploaded_by=request.user,
            )
        except OriginalFileStorageError:
            form.add_error(
                None,
                "Upload storage is not configured. Contact an administrator.",
            )
        else:
            if not result.created:
                messages.info(
                    request,
                    "This file was already submitted for the same source system.",
                )
            elif result.sales_import.status == SalesImport.Status.RECEIVED:
                messages.success(request, "The original file was stored securely.")
            elif result.sales_import.status == SalesImport.Status.RECEIVING:
                messages.warning(
                    request,
                    "The file is awaiting a storage reconciliation check.",
                )
            else:
                messages.error(
                    request,
                    f"Storage failed. Reference: {result.sales_import.pk}",
                )
            return redirect("sales_imports:detail", pk=result.sales_import.pk)
    return render(request, "sales_imports/import_form.html", {"form": form})


@administrator_required
def import_detail(request, pk):
    sales_import = get_object_or_404(
        SalesImport.objects.select_related("uploaded_by", "processing_job"),
        pk=pk,
    )
    processing_job = getattr(sales_import, "processing_job", None)
    latest_attempt = None
    issue_page = None
    if processing_job is not None:
        latest_attempt = (
            SalesImportAttempt.objects.filter(job=processing_job)
            .order_by("-attempt_number")
            .first()
        )
        if latest_attempt is not None:
            issue_page = Paginator(
                latest_attempt.issues.order_by("row_number", "id"),
                50,
            ).get_page(request.GET.get("issues_page"))
    return render(
        request,
        "sales_imports/import_detail.html",
        {
            "sales_import": sales_import,
            "processing_job": processing_job,
            "latest_attempt": latest_attempt,
            "issue_page": issue_page,
        },
    )
