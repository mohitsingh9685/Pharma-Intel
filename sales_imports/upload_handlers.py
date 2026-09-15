from django.conf import settings
from django.core.files.uploadhandler import FileUploadHandler, StopUpload


class BoundedFileUploadHandler(FileUploadHandler):
    """Stop a multipart file while it is arriving if it exceeds the limit."""

    def __init__(self, request=None):
        super().__init__(request)
        self._request_file_bytes = 0

    def new_file(self, *args, **kwargs):
        super().new_file(*args, **kwargs)
        if (
            self.content_length is not None
            and self.content_length > settings.SALES_IMPORT_MAX_UPLOAD_BYTES
        ):
            raise StopUpload(connection_reset=True)

    def receive_data_chunk(self, raw_data, start):
        self._request_file_bytes += len(raw_data)
        if self._request_file_bytes > settings.SALES_IMPORT_MAX_UPLOAD_BYTES:
            raise StopUpload(connection_reset=True)
        return raw_data

    def file_complete(self, file_size):
        return None
