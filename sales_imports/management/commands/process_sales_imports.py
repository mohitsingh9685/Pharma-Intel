"""Run bounded batches from the PostgreSQL-backed sales-import queue."""

import os
import socket
import uuid

from django.core.management.base import BaseCommand, CommandError

from sales_imports.models import SalesImportJob
from sales_imports.processing import process_claim
from sales_imports.queue import SalesImportClaimLost, claim_next_job
from sales_imports.storage import OriginalFileStorageError, get_sales_import_storage


class Command(BaseCommand):
    help = "Validate queued CSV imports and atomically publish accepted sales rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--max-jobs",
            type=int,
            default=1,
            help="Maximum jobs to process before exiting (default: 1).",
        )
        parser.add_argument(
            "--worker-id",
            default="",
            help="Stable worker label for diagnostics; generated when omitted.",
        )

    def handle(self, *args, **options):
        max_jobs = options["max_jobs"]
        if max_jobs < 1:
            raise CommandError("--max-jobs must be at least 1.")

        worker_id = options["worker_id"].strip() or (
            f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:12]}"
        )
        try:
            storage = get_sales_import_storage()
        except OriginalFileStorageError as error:
            raise CommandError(str(error)) from error

        processed = 0
        failed_job_ids = []
        for _ in range(max_jobs):
            try:
                claim = claim_next_job(worker_id=worker_id)
            except ValueError as error:
                raise CommandError(str(error)) from error
            if claim is None:
                break
            try:
                result = process_claim(claim, storage=storage)
            except SalesImportClaimLost as error:
                raise CommandError(str(error)) from error
            processed += 1
            if result.status == SalesImportJob.Status.FAILED:
                failed_job_ids.append(str(result.job_id))
            self.stdout.write(
                f"{result.job_id}: {result.status}; "
                f"rows={result.counts.total_rows}, "
                f"inserted={result.counts.inserted_rows}, "
                f"reused={result.counts.reused_rows}, "
                f"issues={result.counts.issue_count}"
            )

        if failed_job_ids:
            raise CommandError(
                "Sales-import processing failed for "
                f"{len(failed_job_ids)} job(s): {', '.join(failed_job_ids)}."
            )

        self.stdout.write(
            self.style.SUCCESS(f"Sales-import jobs processed: {processed}.")
        )
