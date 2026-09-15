import codecs
import hashlib
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

from django.core.exceptions import ValidationError


_CHUNK_SIZE = 64 * 1024
_MAX_XLSX_MEMBERS = 10_000
_MAX_XLSX_EXPANSION_FACTOR = 100
_MAX_XLSX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
_XLSX_REQUIRED_MEMBERS = {"[Content_Types].xml", "xl/workbook.xml"}


@dataclass(frozen=True)
class InspectedSalesFile:
    file_format: str
    size_bytes: int
    sha256: str


def normalized_filename(uploaded_file) -> str:
    filename = (
        str(uploaded_file.name).replace("\\", "/").rsplit("/", 1)[-1].strip()
    )
    if not filename or len(filename) > 255:
        raise ValidationError("The filename must contain between 1 and 255 characters.")
    return filename


def _validate_csv(uploaded_file, *, max_bytes: int) -> None:
    decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
    decoded_non_whitespace = False
    bytes_read = 0
    uploaded_file.seek(0)
    try:
        for chunk in uploaded_file.chunks(chunk_size=_CHUNK_SIZE):
            bytes_read += len(chunk)
            if bytes_read > max_bytes:
                raise ValidationError("The file exceeds the configured upload limit.")
            if b"\x00" in chunk:
                raise ValidationError("CSV files cannot contain null bytes.")
            text = decoder.decode(chunk)
            decoded_non_whitespace = decoded_non_whitespace or any(
                not character.isspace() for character in text
            )
        tail = decoder.decode(b"", final=True)
        decoded_non_whitespace = decoded_non_whitespace or any(
            not character.isspace() for character in tail
        )
    except UnicodeDecodeError as error:
        raise ValidationError("CSV files must use UTF-8 text encoding.") from error
    finally:
        uploaded_file.seek(0)
    if not decoded_non_whitespace:
        raise ValidationError("The CSV file contains no data.")


def _validate_xlsx(uploaded_file, *, max_bytes: int) -> None:
    uploaded_file.seek(0)
    try:
        if not zipfile.is_zipfile(uploaded_file):
            raise ValidationError("The file is not a valid .xlsx workbook.")
        uploaded_file.seek(0)
        with zipfile.ZipFile(uploaded_file) as workbook:
            members = workbook.infolist()
            if len(members) > _MAX_XLSX_MEMBERS:
                raise ValidationError("The workbook contains too many internal files.")

            names = {member.filename for member in members}
            if not _XLSX_REQUIRED_MEMBERS.issubset(names):
                raise ValidationError("The file is not a valid .xlsx workbook.")

            total_uncompressed = 0
            for member in members:
                path = PurePosixPath(member.filename)
                if path.is_absolute() or ".." in path.parts:
                    raise ValidationError("The workbook contains an unsafe path.")
                if member.flag_bits & 0x1:
                    raise ValidationError("Encrypted workbooks are not supported.")
                if member.filename.casefold().endswith("vbaproject.bin"):
                    raise ValidationError("Macro-enabled workbooks are not supported.")
                total_uncompressed += member.file_size
                expansion_limit = min(
                    max_bytes * _MAX_XLSX_EXPANSION_FACTOR,
                    _MAX_XLSX_UNCOMPRESSED_BYTES,
                )
                if total_uncompressed > expansion_limit:
                    raise ValidationError("The workbook expands beyond the safety limit.")
    except zipfile.BadZipFile as error:
        raise ValidationError("The file is not a valid .xlsx workbook.") from error
    finally:
        uploaded_file.seek(0)


def inspect_sales_file(uploaded_file, *, max_bytes: int) -> InspectedSalesFile:
    """Validate a bounded original file and calculate its lineage checksum."""
    filename = normalized_filename(uploaded_file)
    suffix = PurePosixPath(filename).suffix.casefold()
    format_by_suffix = {".csv": "csv", ".xlsx": "xlsx"}
    file_format = format_by_suffix.get(suffix)
    if file_format is None:
        raise ValidationError("Upload a .csv or .xlsx file.")

    declared_size = getattr(uploaded_file, "size", None)
    if declared_size is not None and declared_size > max_bytes:
        raise ValidationError("The file exceeds the configured upload limit.")

    digest = hashlib.sha256()
    size_bytes = 0
    uploaded_file.seek(0)
    try:
        for chunk in uploaded_file.chunks(chunk_size=_CHUNK_SIZE):
            size_bytes += len(chunk)
            if size_bytes > max_bytes:
                raise ValidationError("The file exceeds the configured upload limit.")
            digest.update(chunk)
    finally:
        uploaded_file.seek(0)

    if size_bytes == 0:
        raise ValidationError("The uploaded file is empty.")

    if file_format == "csv":
        _validate_csv(uploaded_file, max_bytes=max_bytes)
    else:
        _validate_xlsx(uploaded_file, max_bytes=max_bytes)

    return InspectedSalesFile(
        file_format=file_format,
        size_bytes=size_bytes,
        sha256=digest.hexdigest(),
    )
