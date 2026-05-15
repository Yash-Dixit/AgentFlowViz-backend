import hashlib
import json
import re
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException, UploadFile, status
from sqlmodel import Session, select

from app.config import Settings, get_settings
from app.models import Attachment, AttachmentContext
from app.ollama_service import ollama_service
from app.runtime.context import truncate_to_tokens
from app.runtime.persistence import add_log, record_model_log
from app.serializers import attachment_context_to_dict, attachment_to_dict

try:
    from minio import Minio
except ImportError:  # pragma: no cover - exercised only when optional dependency is missing.
    Minio = None

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover - exercised only when optional dependency is missing.
    PdfReader = None


TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".log", ".py", ".yml", ".yaml"}
DOCUMENT_EXTENSIONS = TEXT_EXTENSIONS | {".pdf", ".doc", ".docx"}


def save_uploads_for_run(
    *,
    session: Session,
    run_id: int,
    source_channel: str,
    caption: str,
    uploads: list[UploadFile],
) -> list[dict]:
    saved: list[dict] = []
    settings = get_settings()
    for upload in uploads:
        payload = _read_upload(upload, settings)
        attachment = _store_attachment(
            session=session,
            run_id=run_id,
            source_channel=source_channel,
            filename=upload.filename or "upload.bin",
            mime_type=upload.content_type or "application/octet-stream",
            caption=caption,
            data=payload,
            settings=settings,
        )
        context = _create_attachment_context(session, attachment, payload, settings)
        saved.append(
            {
                "attachment": attachment_to_dict(attachment),
                "context": attachment_context_to_dict(context),
            }
        )
    return saved


def save_file_bytes_for_run(
    *,
    session: Session,
    run_id: int,
    source_channel: str,
    caption: str,
    filename: str,
    mime_type: str,
    data: bytes,
) -> dict:
    settings = get_settings()
    max_bytes = settings.attachment_max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"{filename} exceeds the {settings.attachment_max_upload_mb} MB limit.",
        )
    if not data:
        raise HTTPException(status_code=400, detail=f"{filename} is empty.")

    attachment = _store_attachment(
        session=session,
        run_id=run_id,
        source_channel=source_channel,
        filename=filename,
        mime_type=mime_type,
        caption=caption,
        data=data,
        settings=settings,
    )
    context = _create_attachment_context(session, attachment, data, settings)
    return {
        "attachment": attachment_to_dict(attachment),
        "context": attachment_context_to_dict(context),
    }


def get_run_attachments(session: Session, run_id: int) -> list[dict]:
    attachments = session.exec(
        select(Attachment).where(Attachment.run_id == run_id).order_by(Attachment.id)
    ).all()
    rows = []
    for attachment in attachments:
        context = session.exec(
            select(AttachmentContext).where(AttachmentContext.attachment_id == attachment.id)
        ).first()
        rows.append(
            {
                "attachment": attachment_to_dict(attachment),
                "context": attachment_context_to_dict(context) if context else None,
            }
        )
    return rows


def build_attachment_context(run_id: int) -> str:
    settings = get_settings()
    with Session(_engine()) as session:
        contexts = session.exec(
            select(AttachmentContext).where(AttachmentContext.run_id == run_id).order_by(AttachmentContext.id)
        ).all()
        if not contexts:
            return ""

        lines = ["Attachment Context:"]
        for context in contexts:
            attachment = session.get(Attachment, context.attachment_id)
            if not attachment:
                continue
            lines.append(
                "\n".join(
                    [
                        f"- File: {attachment.filename}",
                        f"  Type: {context.context_type} ({attachment.mime_type}, {attachment.file_size} bytes)",
                        f"  Stored: minio://{attachment.bucket}/{attachment.object_key}",
                        f"  Summary: {context.summary}",
                    ]
                )
            )
            if context.extracted_text.strip():
                clipped, _ = truncate_to_tokens(context.extracted_text, 900)
                lines.append(f"  Extracted text:\n{_indent(clipped, '  ')}")

        clipped_context, _ = truncate_to_tokens("\n".join(lines), settings.attachment_context_max_chars // 4)
        return clipped_context


def _engine():
    from app.database import engine

    return engine


def _read_upload(upload: UploadFile, settings: Settings) -> bytes:
    data = upload.file.read()
    max_bytes = settings.attachment_max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"{upload.filename or 'Upload'} exceeds the {settings.attachment_max_upload_mb} MB limit.",
        )
    if not data:
        raise HTTPException(status_code=400, detail=f"{upload.filename or 'Upload'} is empty.")
    return data


def _store_attachment(
    *,
    session: Session,
    run_id: int,
    source_channel: str,
    filename: str,
    mime_type: str,
    caption: str,
    data: bytes,
    settings: Settings,
) -> Attachment:
    checksum = hashlib.sha256(data).hexdigest()
    object_key = f"runs/{run_id}/{checksum[:16]}-{_safe_filename(filename)}"
    storage_status = "stored"

    if settings.minio_enabled:
        _put_object(object_key, data, mime_type, settings)
    else:
        storage_status = "metadata_only"

    attachment = Attachment(
        run_id=run_id,
        source_channel=source_channel,
        bucket=settings.minio_bucket,
        object_key=object_key,
        filename=filename,
        mime_type=mime_type,
        file_size=len(data),
        checksum_sha256=checksum,
        caption=caption,
        storage_status=storage_status,
    )
    session.add(attachment)
    session.commit()
    session.refresh(attachment)
    add_log(
        run_id,
        "attachment_stored",
        {
            "attachment_id": attachment.id,
            "filename": filename,
            "mime_type": mime_type,
            "file_size": len(data),
            "storage_status": storage_status,
        },
    )
    return attachment


def _put_object(object_key: str, data: bytes, mime_type: str, settings: Settings) -> None:
    if Minio is None:
        raise HTTPException(status_code=500, detail="MinIO dependency is not installed.")

    endpoint, secure = _minio_endpoint(settings)
    client = Minio(
        endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=secure,
    )
    if not client.bucket_exists(settings.minio_bucket):
        client.make_bucket(settings.minio_bucket)
    client.put_object(
        settings.minio_bucket,
        object_key,
        BytesIO(data),
        len(data),
        content_type=mime_type,
    )


def _minio_endpoint(settings: Settings) -> tuple[str, bool]:
    parsed = urlparse(settings.minio_endpoint)
    if parsed.scheme:
        return parsed.netloc, parsed.scheme == "https"
    return settings.minio_endpoint, settings.minio_secure


def _create_attachment_context(
    session: Session,
    attachment: Attachment,
    data: bytes,
    settings: Settings,
) -> AttachmentContext:
    context_type = _context_type(attachment.filename, attachment.mime_type)
    extracted_text = ""
    model = ""
    metadata = {
        "filename": attachment.filename,
        "mime_type": attachment.mime_type,
        "file_size": attachment.file_size,
        "storage_status": attachment.storage_status,
    }

    if attachment.caption:
        metadata["caption"] = attachment.caption

    if context_type == "document":
        extracted_text = _extract_document_text(attachment.filename, attachment.mime_type, data, settings)
        summary = _document_summary(attachment, extracted_text)
    elif context_type == "image":
        model = settings.ollama_vision_model
        summary = _image_summary(attachment, data, model) if model else _generic_image_summary(attachment)
    else:
        summary = _generic_summary(attachment)

    embedding = _safe_embedding(summary + "\n" + extracted_text)
    context = AttachmentContext(
        attachment_id=int(attachment.id or 0),
        run_id=attachment.run_id,
        context_type=context_type,
        model=model,
        summary=summary,
        extracted_text=extracted_text,
        metadata_json=json.dumps(metadata),
        embedding=embedding,
    )
    session.add(context)
    session.commit()
    session.refresh(context)
    add_log(
        attachment.run_id,
        "attachment_context_created",
        {
            "attachment_id": attachment.id,
            "context_id": context.id,
            "context_type": context_type,
            "extracted_chars": len(extracted_text),
            "embedded": bool(embedding),
        },
    )
    return context


def _context_type(filename: str, mime_type: str) -> str:
    suffix = Path(filename).suffix.lower()
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("text/") or suffix in DOCUMENT_EXTENSIONS:
        return "document"
    return "file"


def _extract_document_text(filename: str, mime_type: str, data: bytes, settings: Settings) -> str:
    suffix = Path(filename).suffix.lower()
    if mime_type == "application/pdf" or suffix == ".pdf":
        return _extract_pdf_text(data, settings)
    if mime_type.startswith("text/") or suffix in TEXT_EXTENSIONS:
        return _decode_text(data, settings.attachment_text_max_chars)
    return ""


def _extract_pdf_text(data: bytes, settings: Settings) -> str:
    if PdfReader is None:
        return "PDF text extraction is unavailable because pypdf is not installed."
    try:
        reader = PdfReader(BytesIO(data))
        pages = []
        for page in reader.pages[: settings.attachment_max_pdf_pages]:
            pages.append(page.extract_text() or "")
        text = "\n\n".join(page.strip() for page in pages if page.strip())
        return _truncate_chars(text, settings.attachment_text_max_chars)
    except Exception as exc:
        return f"PDF text extraction failed: {exc}"


def _decode_text(data: bytes, max_chars: int) -> str:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return _truncate_chars(data.decode(encoding), max_chars)
        except UnicodeDecodeError:
            continue
    return ""


def _document_summary(attachment: Attachment, extracted_text: str) -> str:
    if extracted_text.strip():
        clipped = " ".join(extracted_text.split())[:700]
        return f"Document text was extracted from {attachment.filename}. Preview: {clipped}"
    return f"Document {attachment.filename} was stored, but no readable text could be extracted."


def _image_summary(attachment: Attachment, data: bytes, model: str) -> str:
    prompt = (
        "Describe this image for an agent workflow. Mention visible text, objects, layout, "
        "and any uncertainty. Keep the description concise."
    )
    try:
        reply = ollama_service.describe_image(
            model=model,
            image_bytes=data,
            system_prompt="You create concise visual context for downstream agents.",
            user_prompt=prompt,
            num_predict=220,
        )
        record_model_log(attachment.run_id, "Attachment Vision", reply)
        if reply.content.strip() and not reply.fallback:
            return reply.content.strip()
    except Exception as exc:
        add_log(
            attachment.run_id,
            "attachment_vision_failed",
            {"attachment_id": attachment.id, "error": str(exc)},
            level="warning",
        )

    return _generic_image_summary(attachment)


def _generic_image_summary(attachment: Attachment) -> str:
    caption = f" Caption: {attachment.caption}" if attachment.caption else ""
    return (
        f"Image {attachment.filename} was stored as {attachment.mime_type}. "
        "No visual description was generated because OLLAMA_VISION_MODEL is not configured "
        "or the configured model could not process the image."
        f"{caption}"
    )


def _generic_summary(attachment: Attachment) -> str:
    return f"File {attachment.filename} was stored as {attachment.mime_type} for workflow reference."


def _safe_embedding(text: str) -> list[float] | None:
    if not text.strip():
        return None
    try:
        embedding = ollama_service.embed_text(text[:8000])
    except Exception:
        return None
    return embedding or None


def _safe_filename(filename: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", filename).strip("._") or "upload.bin"


def _truncate_chars(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line if line.strip() else line for line in text.splitlines())
