from io import BytesIO
from contextlib import suppress
import logging
import os
from pathlib import PurePosixPath
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError
from pypdf import PdfReader
from pypdf.errors import PdfReadError
try:
    import pypdfium2 as pdfium
except Exception:  # pragma: no cover - optional fallback dependency
    pdfium = None
try:
    import fitz
except Exception:  # pragma: no cover - optional fallback dependency
    fitz = None


LOGGER = logging.getLogger(__name__)

SOURCE_BUCKET_ENV_VAR = "SOURCE_BUCKET_NAME"
DEST_BUCKET_ENV_VAR = "DESTINATION_BUCKET_NAME"
ENABLE_DIAGRAM_OCR_ENV_VAR = "ENABLE_DIAGRAM_OCR"
MAX_IMAGES_PER_PAGE_ENV_VAR = "MAX_IMAGES_PER_PAGE"
MAX_DIAGRAM_TEXT_CHARS_ENV_VAR = "MAX_DIAGRAM_TEXT_CHARS"
IMAGE_OUTPUT_PREFIX_ENV_VAR = "DIAGRAM_IMAGE_PREFIX"
VECTOR_OUTPUT_PREFIX_ENV_VAR = "VECTOR_DRAWING_PREFIX"
RENDER_PAGE_IMAGES_ENV_VAR = "RENDER_PAGE_IMAGES"
RENDER_PAGE_IMAGE_SCALE_ENV_VAR = "RENDER_PAGE_IMAGE_SCALE"
MAX_RENDERED_PAGES_ENV_VAR = "MAX_RENDERED_PAGES"
ENABLE_EMBEDDED_IMAGE_EXTRACTION_ENV_VAR = "ENABLE_EMBEDDED_IMAGE_EXTRACTION"
ENABLE_VECTOR_DRAWING_EXTRACTION_ENV_VAR = "ENABLE_VECTOR_DRAWING_EXTRACTION"
MAX_VECTOR_PAGES_ENV_VAR = "MAX_VECTOR_PAGES"
MAX_PDF_PAGES_ENV_VAR = "MAX_PDF_PAGES"

MALFORMED_EVENT_LOG_MSG = "Malformed event. Skipping this record."


class MissingEnvironmentVariable(Exception):
    """Raised if a required environment variable is missing"""


def _required_env(name):
    """Read a required environment variable or raise a clear error."""
    if not (value := os.environ.get(name)):
        raise MissingEnvironmentVariable(name)
    return value


def _as_bool(value, default=False):
    """Convert common env var string formats to bool."""
    if value is None:
        return default
    normalized = str(value).strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def _as_int(value, default):
    """Convert env var string to int with fallback."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value, default):
    """Convert env var string to float with fallback."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _apply_page_cap(total_pages, max_pdf_pages):
    """Apply optional global page cap; non-positive values disable the cap."""
    if max_pdf_pages and max_pdf_pages > 0:
        return min(total_pages, max_pdf_pages)
    return total_pages


def _silence_noisy_loggers():
    """Silence chatty libraries for better logging"""
    for logger in ['boto3', 'botocore',
                   'botocore.vendored.requests.packages.urllib3']:
        logging.getLogger(logger).setLevel(logging.WARNING)


def _configure_logger():
    """Configure python logger for lambda function"""
    default_log_args = {
        "level": logging.DEBUG if os.environ.get("VERBOSE", False) else logging.INFO,
        "format": "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        "datefmt": "%d-%b-%y %H:%M",
        "force": True,
    }
    logging.basicConfig(**default_log_args)


def _check_missing_field(validation_dict, extraction_key):
    """Check if a field exists in a dictionary

    :param validation_dict: Dictionary
    :param extraction_key: String

    :raises: KeyError
    """
    extracted_value = validation_dict.get(extraction_key)
    
    if not extracted_value:
        LOGGER.error(f"Missing '{extraction_key}' key in the dict")
        raise KeyError
    

def _validate_field(validation_dict, extraction_key, expected_value):
    """Validate the passed in field

    :param validation_dict: Dictionary
    :param extraction_key: String
    :param expected_value: String

    :raises: ValueError
    """
    extracted_value = validation_dict.get(extraction_key)
    _check_missing_field(validation_dict, extraction_key)
    
    if extracted_value != expected_value:
        LOGGER.error(f"Incorrect value found for '{extraction_key}' key")
        raise ValueError
    

def _record_validation(record, source_bucket_name):
    """Validate record
    
    :param record: Dictionary
    :param source_bucket_name: String

    :rtype: Boolean
    """
    # validate eventSource
    _validate_field(record, "eventSource", "aws:s3")
    
    # validate eventSource
    _check_missing_field(record, "eventName")
    if not record["eventName"].startswith("ObjectCreated"):
        LOGGER.warning("Found a non ObjectCreated event, ignoring this record")
        return False
        
    # check for 's3' in response elements
    _check_missing_field(record, "s3")
    
    s3_data = record["s3"]
    # validate s3 data
    _check_missing_field(s3_data, "bucket")
    _validate_field(s3_data["bucket"], "name", source_bucket_name)

    # check for object
    _check_missing_field(s3_data, "object")
    # check for key
    _check_missing_field(s3_data["object"], "key")

    return True


def _get_source_file_contents(client, bucket, filename):
    """Fetch the contents of the file

    :param client: boto3 Client Object (S3)
    :param bucket: String
    :param filename: String

    :rtype Bytes
    """
    resp = client.get_object(Bucket=bucket, Key=filename)

    _check_missing_field(resp, "ResponseMetadata")

    _validate_field(resp["ResponseMetadata"], "HTTPStatusCode", 200)

    _check_missing_field(resp, "Body")
    
    return resp["Body"].read()


def _extract_diagram_snippets(textract_client, page, page_number, max_images_per_page, max_chars):
    """Extract text-like content from embedded page images using Textract OCR."""
    snippets = []
    scanned_images = 0
    ocr_text_hits = 0

    try:
        page_images = list(getattr(page, "images", []))
    except Exception as err:
        LOGGER.warning("Unable to enumerate images for page %s: %s", page_number, err)
        return snippets, scanned_images, ocr_text_hits

    if not page_images:
        return snippets, scanned_images, ocr_text_hits

    for image_index, image in enumerate(page_images[:max_images_per_page], start=1):
        scanned_images += 1
        image_bytes = getattr(image, "data", None)
        if not image_bytes:
            continue

        try:
            response = textract_client.detect_document_text(
                Document={"Bytes": image_bytes}
            )
        except ClientError as err:
            LOGGER.warning(
                "Textract failed on page %s image %s: %s",
                page_number,
                image_index,
                err,
            )
            continue
        except Exception as err:
            LOGGER.warning(
                "Unexpected OCR error on page %s image %s: %s",
                page_number,
                image_index,
                err,
            )
            continue

        lines = [
            block["Text"]
            for block in response.get("Blocks", [])
            if block.get("BlockType") == "LINE" and block.get("Text")
        ]

        if lines and (snippet_text := " ".join(lines).strip()):
            ocr_text_hits += 1
            snippets.append(
                f"[Diagram OCR | page {page_number} image {image_index}] "
                f"{snippet_text[:max_chars]}"
            )

    if not snippets and page_images:
        snippets.append(
            f"[Diagram | page {page_number}] Non-textual diagram detected in source document."
        )

    return snippets, scanned_images, ocr_text_hits


def _upload_page_images(s3_resource, dest_bucket_name, pdf_file_name, page, page_number, max_images_per_page):
    """Extract and upload embedded page images; return URI marker lines for indexing."""
    image_markers = []
    image_prefix = os.environ.get(IMAGE_OUTPUT_PREFIX_ENV_VAR, "diagram-images").strip("/")

    try:
        page_images = list(getattr(page, "images", []))
    except Exception as err:
        LOGGER.warning("Unable to enumerate uploadable images for page %s: %s", page_number, err)
        return image_markers

    if not page_images:
        return image_markers

    safe_pdf_stem = PurePosixPath(pdf_file_name).name.replace(".pdf", "")
    for image_index, image in enumerate(page_images[:max_images_per_page], start=1):
        image_bytes = getattr(image, "data", None)
        if not image_bytes:
            continue

        image_name = getattr(image, "name", "") or ""
        image_ext = PurePosixPath(image_name).suffix.lower() or ".bin"

        image_key = (
            f"{image_prefix}/{safe_pdf_stem}/"
            f"page-{page_number:04d}-image-{image_index:03d}{image_ext}"
        )
        put_kwargs = {
            "Key": image_key,
            "Body": image_bytes,
        }
        if image_ext in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
            put_kwargs["ContentType"] = f"image/{image_ext.lstrip('.').replace('jpg', 'jpeg')}"

        s3_resource.Bucket(dest_bucket_name).put_object(**put_kwargs)
        image_markers.append(
            f"[Diagram Image URI | page {page_number} image {image_index}] "
            f"s3://{dest_bucket_name}/{image_key}"
        )

    return image_markers


def _upload_rendered_page_images(
    s3_resource,
    dest_bucket_name,
    pdf_file_name,
    file_contents,
    max_rendered_pages,
    render_scale,
    max_pdf_pages,
):
    """Render PDF pages to PNG and upload if embedded image extraction misses content."""
    if not pdfium:
        LOGGER.warning("pypdfium2 is unavailable; skipping rendered page image fallback")
        return []

    image_markers = []
    image_prefix = os.environ.get(IMAGE_OUTPUT_PREFIX_ENV_VAR, "diagram-images").strip("/")
    safe_pdf_stem = PurePosixPath(pdf_file_name).name.replace(".pdf", "")

    try:
        pdf_doc = pdfium.PdfDocument(file_contents)
    except Exception as err:
        LOGGER.warning("Could not open PDF for page rendering fallback: %s", err)
        return image_markers

    try:
        total_pages = _apply_page_cap(len(pdf_doc), max_pdf_pages)
        pages_to_render = min(total_pages, max_rendered_pages)

        for page_index in range(pages_to_render):
            page = pdf_doc[page_index]
            pil_image = page.render(scale=render_scale).to_pil()
            image_buffer = BytesIO()
            pil_image.save(image_buffer, format="PNG")
            image_bytes = image_buffer.getvalue()

            image_key = (
                f"{image_prefix}/{safe_pdf_stem}/"
                f"page-{page_index + 1:04d}-rendered.png"
            )
            s3_resource.Bucket(dest_bucket_name).put_object(
                Key=image_key,
                Body=image_bytes,
                ContentType="image/png",
            )
            image_markers.append(
                f"[Page Image URI | page {page_index + 1}] "
                f"s3://{dest_bucket_name}/{image_key}"
            )
    except Exception as err:
        LOGGER.warning("Rendered page image fallback failed for %s: %s", pdf_file_name, err)
    finally:
        with suppress(Exception):
            pdf_doc.close()

    return image_markers


def _upload_vector_drawing_pages(
    s3_resource,
    dest_bucket_name,
    pdf_file_name,
    file_contents,
    max_vector_pages,
    max_pdf_pages,
):
    """Extract page-level SVGs for pages containing vector drawings and upload to S3."""
    if not fitz:
        LOGGER.warning("PyMuPDF (fitz) is unavailable; skipping vector drawing extraction")
        return []

    markers = []
    vector_prefix = os.environ.get(VECTOR_OUTPUT_PREFIX_ENV_VAR, "vector-drawings").strip("/")
    safe_pdf_stem = PurePosixPath(pdf_file_name).name.replace(".pdf", "")

    try:
        pdf_doc = fitz.open(stream=file_contents, filetype="pdf")
    except Exception as err:
        LOGGER.warning("Could not open PDF for vector extraction fallback: %s", err)
        return markers

    try:
        pages_to_scan = min(_apply_page_cap(len(pdf_doc), max_pdf_pages), max_vector_pages)
        for page_index in range(pages_to_scan):
            page = pdf_doc.load_page(page_index)
            try:
                has_vectors = bool(page.get_drawings())
            except Exception:
                has_vectors = False

            if not has_vectors:
                continue

            try:
                svg_text = page.get_svg_image(text_as_path=False)
            except TypeError:
                svg_text = page.get_svg_image()
            except Exception as err:
                LOGGER.warning("Failed to export SVG for page %s: %s", page_index + 1, err)
                continue

            if not svg_text:
                continue

            svg_key = (
                f"{vector_prefix}/{safe_pdf_stem}/"
                f"page-{page_index + 1:04d}-drawing.svg"
            )
            s3_resource.Bucket(dest_bucket_name).put_object(
                Key=svg_key,
                Body=svg_text.encode("utf-8"),
                ContentType="image/svg+xml",
            )
            markers.append(
                f"[Vector Drawing URI | page {page_index + 1}] "
                f"s3://{dest_bucket_name}/{svg_key}"
            )
    except Exception as err:
        LOGGER.warning("Vector drawing extraction failed for %s: %s", pdf_file_name, err)
    finally:
        with suppress(Exception):
            pdf_doc.close()

    return markers


def lambda_handler(event, context):
    """What executes when the program is run"""

    # configure python logger for Lambda
    _configure_logger()
    # silence chatty libraries for better logging
    _silence_noisy_loggers()

    source_bucket_name = _required_env(SOURCE_BUCKET_ENV_VAR)
    LOGGER.info(f"source bucket: {source_bucket_name}")

    dest_bucket_name = _required_env(DEST_BUCKET_ENV_VAR)
    LOGGER.info(f"destination bucket: {dest_bucket_name}")

    enable_diagram_ocr = _as_bool(
        os.environ.get(ENABLE_DIAGRAM_OCR_ENV_VAR),
        default=False,
    )
    max_images_per_page = max(1, _as_int(os.environ.get(MAX_IMAGES_PER_PAGE_ENV_VAR), 4))
    max_diagram_text_chars = max(256, _as_int(os.environ.get(MAX_DIAGRAM_TEXT_CHARS_ENV_VAR), 2000))
    render_page_images = _as_bool(os.environ.get(RENDER_PAGE_IMAGES_ENV_VAR), default=True)
    render_page_scale = max(1.0, _as_float(os.environ.get(RENDER_PAGE_IMAGE_SCALE_ENV_VAR), 1.5))
    max_rendered_pages = max(1, _as_int(os.environ.get(MAX_RENDERED_PAGES_ENV_VAR), 20))
    enable_embedded_image_extraction = _as_bool(
        os.environ.get(ENABLE_EMBEDDED_IMAGE_EXTRACTION_ENV_VAR),
        default=False,
    )
    enable_vector_drawing_extraction = _as_bool(
        os.environ.get(ENABLE_VECTOR_DRAWING_EXTRACTION_ENV_VAR),
        default=True,
    )
    max_vector_pages = max(1, _as_int(os.environ.get(MAX_VECTOR_PAGES_ENV_VAR), 20))
    max_pdf_pages = _as_int(os.environ.get(MAX_PDF_PAGES_ENV_VAR), 0)

    LOGGER.info(
        "Image extraction mode: embedded=%s render_pages=%s max_rendered_pages=%s render_scale=%s",
        enable_embedded_image_extraction,
        render_page_images,
        max_rendered_pages,
        render_page_scale,
    )
    LOGGER.info(
        "Vector extraction mode: enabled=%s max_vector_pages=%s",
        enable_vector_drawing_extraction,
        max_vector_pages,
    )
    LOGGER.info(
        "Global page cap mode: max_pdf_pages=%s (0 means all pages)",
        max_pdf_pages,
    )

    if enable_diagram_ocr:
        LOGGER.info(
            "Diagram OCR enabled: max_images_per_page=%s, max_diagram_text_chars=%s",
            max_images_per_page,
            max_diagram_text_chars,
        )
    else:
        LOGGER.info("Diagram OCR disabled via environment variable")

    # check for 'records' field in the event
    _check_missing_field(event, "Records")
    records = event["Records"]

    if not isinstance(records, list):
        raise Exception("'Records' is not a list")
    LOGGER.info("Extracted 'Records' from the event")

    s3_client = boto3.client("s3")
    s3_resource = boto3.resource("s3")
    textract_client = boto3.client("textract") if enable_diagram_ocr else None
    docs_processed = 0
    total_pages = 0
    total_images_scanned = 0
    total_ocr_text_hits = 0
    total_output_chars = 0
    total_image_uris = 0
    
    for record in records:
        try:
            valid_record = _record_validation(record, source_bucket_name)
        except (KeyError, ValueError):
            LOGGER.warning(MALFORMED_EVENT_LOG_MSG)
            continue
        
        if not valid_record:
            LOGGER.warning("record could not be validated. Skipping this one.")
            continue
        # S3 event keys are URL-encoded (e.g., spaces as '+').
        file_name = unquote_plus(record["s3"]["object"]["key"])
        LOGGER.info(
            f"Valid record found. Will attempt to process the file: {file_name}")

        file_contents = _get_source_file_contents(
            s3_client, source_bucket_name, file_name)
        
        try:
            pdf = PdfReader(BytesIO(file_contents))
        except PdfReadError as err:
            LOGGER.error(err)
            LOGGER.warning(
                f"{file_name} is invalid and/or corrupt, skipping.")
            continue 

        LOGGER.info("Extracting text and diagram signals from pdf..")
        text_sections = []
        pages_in_doc = _apply_page_cap(len(pdf.pages), max_pdf_pages)
        images_scanned_in_doc = 0
        ocr_text_hits_in_doc = 0
        image_uris_in_doc = 0
        for page_number, page in enumerate(pdf.pages[:pages_in_doc], start=1):
            if page_text := (page.extract_text() or "").strip():
                text_sections.append(f"[Page {page_number} Text]\n{page_text}")

            embedded_image_markers = []
            if enable_embedded_image_extraction:
                embedded_image_markers = _upload_page_images(
                    s3_resource,
                    dest_bucket_name,
                    file_name,
                    page,
                    page_number,
                    max_images_per_page,
                )
            text_sections.extend(embedded_image_markers)
            image_uris_in_doc += len(embedded_image_markers)

            if textract_client:
                page_snippets, scanned_images, ocr_hits = _extract_diagram_snippets(
                    textract_client,
                    page,
                    page_number,
                    max_images_per_page,
                    max_diagram_text_chars,
                )
                text_sections.extend(page_snippets)
                images_scanned_in_doc += scanned_images
                ocr_text_hits_in_doc += ocr_hits

        if enable_vector_drawing_extraction:
            vector_markers = _upload_vector_drawing_pages(
                s3_resource,
                dest_bucket_name,
                file_name,
                file_contents,
                max_vector_pages,
                max_pdf_pages,
            )
            text_sections.extend(vector_markers)
            image_uris_in_doc += len(vector_markers)

        if render_page_images and image_uris_in_doc == 0:
            rendered_markers = _upload_rendered_page_images(
                s3_resource,
                dest_bucket_name,
                file_name,
                file_contents,
                max_rendered_pages,
                render_page_scale,
                max_pdf_pages,
            )
            text_sections.extend(rendered_markers)
            image_uris_in_doc += len(rendered_markers)

        text_file_contents = "\n\n".join(text_sections).strip()
        if not text_file_contents:
            LOGGER.warning("No text extracted from %s; writing empty placeholder", file_name)
            text_file_contents = "[No extractable text found in this PDF.]"

        output_chars = len(text_file_contents)

        LOGGER.debug("Writing file to S3")
        s3_resource.Bucket(dest_bucket_name).put_object(
            Key=file_name.replace(".pdf", ".txt"), 
            Body=text_file_contents.encode("utf-8"))
        LOGGER.info("Successfully converted pdf to txt, and uploaded to s3")
        LOGGER.info(
            "Ingestion stats | file=%s | pages=%s | images_scanned=%s | ocr_hits=%s | output_chars=%s",
            file_name,
            pages_in_doc,
            images_scanned_in_doc,
            ocr_text_hits_in_doc,
            output_chars,
        )
        LOGGER.info(
            "Image URI stats | file=%s | image_uris=%s | render_fallback=%s",
            file_name,
            image_uris_in_doc,
            render_page_images,
        )
        if images_scanned_in_doc > 0 and ocr_text_hits_in_doc == 0:
            LOGGER.warning(
                "Low OCR yield | file=%s | images_scanned=%s | ocr_hits=0 | consider higher scan quality or alternate OCR settings",
                file_name,
                images_scanned_in_doc,
            )

        docs_processed += 1
        total_pages += pages_in_doc
        total_images_scanned += images_scanned_in_doc
        total_ocr_text_hits += ocr_text_hits_in_doc
        total_output_chars += output_chars
        total_image_uris += image_uris_in_doc

    LOGGER.info(
        "Batch ingestion summary | docs_processed=%s | total_pages=%s | total_images_scanned=%s | total_ocr_hits=%s | total_output_chars=%s",
        docs_processed,
        total_pages,
        total_images_scanned,
        total_ocr_text_hits,
        total_output_chars,
    )
    LOGGER.info("Batch image uri summary | total_image_uris=%s", total_image_uris)
    if total_images_scanned > 0 and total_ocr_text_hits == 0:
        LOGGER.warning(
            "Batch OCR yielded no text from scanned images | total_images_scanned=%s",
            total_images_scanned,
        )
    LOGGER.debug("Closing s3 boto3 client")
    s3_client.close()
