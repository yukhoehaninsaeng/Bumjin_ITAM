import hashlib
import io
import warnings
import zipfile
from pathlib import PurePath
from PIL import Image, UnidentifiedImageError
from rest_framework.exceptions import ValidationError

MAX_SIZE = 20 * 1024 * 1024
MAX_FILES = 5
MEDIA = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "pdf": "application/pdf",
    "txt": "text/plain",
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def validate_upload(upload):
    name = PurePath(upload.name.replace("\\", "/")).name
    if len(name) > 255:
        raise ValidationError("파일명은 255자 이하여야 합니다.")
    ext = name.rsplit(".", 1)[-1].lower()
    if ext not in MEDIA:
        raise ValidationError("PNG/JPG/WEBP, PDF/TXT/CSV/XLSX/DOCX 파일을 선택하세요.")
    if not 0 < upload.size <= MAX_SIZE:
        raise ValidationError("비어 있지 않은 20MB 이하 파일만 첨부할 수 있습니다.")
    data = upload.read(MAX_SIZE + 1)
    upload.seek(0)
    if len(data) > MAX_SIZE:
        raise ValidationError("파일당 20MB를 초과했습니다.")
    image = ext in ["png", "jpg", "jpeg", "webp"]
    try:
        if image:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as img:
                    expected = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "webp": "WEBP"}[ext]
                    if img.format != expected or img.width * img.height > 40_000_000:
                        raise ValueError("이미지 형식 또는 크기 오류")
                    img.verify()
        elif ext == "pdf":
            if not data.startswith(b"%PDF-"):
                raise ValueError("PDF 형식 오류")
        elif ext in ["txt", "csv"]:
            if b"\x00" in data:
                raise ValueError("텍스트 형식 오류")
            data.decode("utf-8-sig")
        else:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = archive.namelist()
                prefix = "xl/" if ext == "xlsx" else "word/"
                if "[Content_Types].xml" not in names or not any(
                    n.startswith(prefix) for n in names
                ):
                    raise ValueError("Office 형식 오류")
                if (
                    len(names) > 2000
                    or sum(i.file_size for i in archive.infolist()) > 100 * 1024 * 1024
                ):
                    raise ValueError("압축 내용이 너무 큽니다.")
    except (
        ValueError,
        UnicodeError,
        UnidentifiedImageError,
        OSError,
        zipfile.BadZipFile,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValidationError("파일 내용이 올바르지 않거나 지원 한도를 초과했습니다.") from exc
    return {
        "name": name,
        "size": len(data),
        "checksum": hashlib.sha256(data).hexdigest(),
        "media_type": MEDIA[ext],
        "is_image": image,
    }
