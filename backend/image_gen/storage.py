from __future__ import annotations

import base64
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

logger = logging.getLogger(__name__)

_CONTENT_TYPE_TO_EXTENSION: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_EXTENSION_TO_CONTENT_TYPE: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class ImageStorage:
    storage_dir: Path
    ttl_hours: int

    def __init__(self, storage_dir: Path | None = None, ttl_hours: int = 24) -> None:
        base_dir = Path(__file__).resolve().parent.parent.parent
        self.storage_dir = storage_dir or (base_dir / "data" / "generated_images")
        self.ttl_hours = ttl_hours
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def save_image(self, image_b64: str, metadata: dict[str, object]) -> str:
        image_id = str(uuid.uuid4())
        content_type, file_extension = self._resolve_content_type_and_extension(
            image_b64=image_b64,
            metadata=metadata,
        )
        image_path = self._image_path(image_id, file_extension)
        metadata_path = self._metadata_path(image_id)

        image_bytes = self._decode_image_b64(image_b64)
        _ = image_path.write_bytes(image_bytes)

        now = datetime.now(timezone.utc)
        metadata_payload = {
            "image_id": image_id,
            "created_at": now.isoformat(),
            "content_type": content_type,
            "file_extension": file_extension,
            **metadata,
        }
        _ = metadata_path.write_text(
            json.dumps(metadata_payload, indent=2), encoding="utf-8"
        )
        return image_id

    def get_image(self, image_id: str) -> tuple[bytes, str]:
        safe_id = self._validate_image_id(image_id)
        metadata = self.get_metadata(safe_id)

        extension = metadata.get("file_extension")
        extension_str = extension if isinstance(extension, str) else ".png"
        image_path = self._image_path(safe_id, extension_str)
        if not image_path.exists() or not image_path.is_file():
            image_path = self._find_existing_image_path(safe_id)
        if not image_path.exists() or not image_path.is_file():
            raise FileNotFoundError(f"Image not found for id={safe_id}")

        metadata_content_type = metadata.get("content_type")
        if isinstance(metadata_content_type, str):
            content_type = metadata_content_type
        else:
            content_type = _EXTENSION_TO_CONTENT_TYPE.get(
                image_path.suffix.lower(), "image/png"
            )

        return image_path.read_bytes(), content_type

    def get_metadata(self, image_id: str) -> dict[str, object]:
        safe_id = self._validate_image_id(image_id)
        metadata_path = self._metadata_path(safe_id)
        if not metadata_path.exists() or not metadata_path.is_file():
            raise FileNotFoundError(f"Metadata not found for id={safe_id}")

        payload = cast(object, json.loads(metadata_path.read_text(encoding="utf-8")))
        if not isinstance(payload, dict):
            raise ValueError(f"Metadata payload is not an object for id={safe_id}")
        return cast(dict[str, object], payload)

    def cleanup_expired(self) -> dict[str, int]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.ttl_hours)
        scanned = 0
        deleted = 0

        for file_path in self.storage_dir.iterdir():
            if not file_path.is_file():
                continue

            scanned += 1
            file_mtime = datetime.fromtimestamp(file_path.stat().st_mtime, timezone.utc)
            if file_mtime >= cutoff:
                continue

            try:
                file_path.unlink(missing_ok=True)
                deleted += 1
                logger.info(
                    "Deleted expired generated image artifact: %s", file_path.name
                )
            except Exception as exc:
                logger.warning(
                    "Failed to delete expired image artifact %s: %s",
                    file_path.name,
                    exc,
                )

        return {"scanned": scanned, "deleted": deleted}

    def _validate_image_id(self, image_id: str) -> str:
        if not _UUID_PATTERN.match(image_id):
            raise ValueError("Invalid image_id format")
        return image_id

    def _image_path(self, image_id: str, extension: str) -> Path:
        normalized_extension = (
            extension if extension.startswith(".") else f".{extension}"
        )
        path = (self.storage_dir / f"{image_id}{normalized_extension}").resolve()
        self._ensure_in_storage_dir(path)
        return path

    def _find_existing_image_path(self, image_id: str) -> Path:
        for candidate in self.storage_dir.glob(f"{image_id}.*"):
            if candidate.suffix.lower() == ".json":
                continue
            resolved = candidate.resolve()
            self._ensure_in_storage_dir(resolved)
            return resolved
        return self._image_path(image_id, ".png")

    def _metadata_path(self, image_id: str) -> Path:
        path = (self.storage_dir / f"{image_id}.json").resolve()
        self._ensure_in_storage_dir(path)
        return path

    def _ensure_in_storage_dir(self, path: Path) -> None:
        storage_root = self.storage_dir.resolve()
        if storage_root != path and storage_root not in path.parents:
            raise ValueError("Resolved file path escapes storage directory")

    def _decode_image_b64(self, image_b64: str) -> bytes:
        b64_payload = image_b64
        if "," in image_b64 and image_b64.startswith("data:image"):
            b64_payload = image_b64.split(",", 1)[1]

        try:
            return base64.b64decode(b64_payload, validate=True)
        except Exception as exc:
            raise ValueError("Invalid base64 image payload") from exc

    def _resolve_content_type_and_extension(
        self,
        *,
        image_b64: str,
        metadata: dict[str, object],
    ) -> tuple[str, str]:
        metadata_content_type = metadata.get("content_type")
        if isinstance(metadata_content_type, str):
            normalized_content_type = metadata_content_type.lower()
            extension = _CONTENT_TYPE_TO_EXTENSION.get(normalized_content_type)
            if extension:
                return normalized_content_type, extension

        if image_b64.startswith("data:image") and ";base64," in image_b64:
            header = image_b64.split(";", 1)[0]
            content_type = header.removeprefix("data:").lower()
            extension = _CONTENT_TYPE_TO_EXTENSION.get(content_type)
            if extension:
                return content_type, extension

        return "image/png", ".png"
