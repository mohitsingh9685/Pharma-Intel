"""Explicitly requeue a recoverable terminal sales-import job."""

import uuid

from django.core.management.base import BaseCommand, CommandError

from sales_imports.models import SalesImportJob
from sales_imports.queue import SalesImportQueueStateError, requeue_job


class Command(BaseCommand):
    help = "Requeue one failed or rejected CSV sales import for another attempt."

    def add_arguments(self, parser):
        parser.add_argument(
            "sales_import_id",
            help="UUID of the sales import (also the processing job ID).",
        )
        parser.add_argument(
            "--additional-attempts",
            type=int,
            default=1,
            help="Number of attempts to add to the job budget (default: 1).",
        )

    def handle(self, *args, **options):
        raw_id = options["sales_import_id"]
        try:
            sales_import_id = uuid.UUID(str(raw_id))
        except (AttributeError, TypeError, ValueError) as error:
            raise CommandError("sales_import_id must be a valid UUID.") from error

        additional_attempts = options["additional_attempts"]
        try:
            job = requeue_job(
                sales_import_id,
                additional_attempts=additional_attempts,
            )
        except SalesImportJob.DoesNotExist as error:
            raise CommandError(
                f"No sales-import job exists for {sales_import_id}."
            ) from error
        except (SalesImportQueueStateError, ValueError) as error:
            raise CommandError(str(error)) from error

        self.stdout.write(
            self.style.SUCCESS(
                f"Sales-import job {job.pk} queued; "
                f"attempts used={job.attempt_count}, "
                f"attempt limit={job.max_attempts}."
            )
        )
