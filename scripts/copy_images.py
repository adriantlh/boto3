#!/usr/bin/env python3
"""
Copy image objects from one S3 bucket to another, avoiding duplicates.

- Identifies images by extension and Content-Type.
- Uses a manifest prefix in the destination bucket to remember which source
  ETags have already been copied.
- Generates timestamp + UUID destination keys so every copy name is unique.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError

# Configure these via environment variables or edit directly.
SOURCE_BUCKET = os.environ.get("IMAGE_SOURCE_BUCKET", "canvastekk-app-files-dev")
DEST_BUCKET = os.environ.get("IMAGE_DEST_BUCKET", "canvastekk-defect-image-files-dev")
DEST_PREFIX = os.environ.get("IMAGE_DEST_PREFIX", "upload/11/")  # optional
MANIFEST_PREFIX = os.environ.get(
    "IMAGE_MANIFEST_PREFIX", f"{DEST_PREFIX}_copied/"
)
IMAGE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".webp",
    ".tiff",
    ".svg",
}

LOG_LEVEL = os.environ.get("IMAGE_COPY_LOG_LEVEL", "INFO").upper()

s3 = boto3.client("s3")
logger = logging.getLogger(__name__)
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")


def list_objects(bucket: str) -> Iterable[dict]:
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            yield obj


def head_object(bucket: str, key: str) -> dict | None:
    try:
        return s3.head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        if code == "404":
            logger.debug("head_object 404 for %s", key)
            return None
        logger.warning("Failed to head %s: %s", key, error)
        return None


def is_image(key: str, content_type: str | None) -> bool:
    suffix = Path(key).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return True
    if content_type and content_type.lower().startswith("image/"):
        return True
    return False


def destination_key(source_key: str) -> str:
    suffix = Path(source_key).suffix.lower()
    unique_part = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{uuid4().hex}"
    return f"{DEST_PREFIX}{unique_part}{suffix}"


def manifest_key(etag: str) -> str:
    return f"{MANIFEST_PREFIX}{etag}"


def already_copied(etag: str) -> bool:
    mk = manifest_key(etag)
    return head_object(DEST_BUCKET, mk) is not None


def mark_copied(etag: str) -> None:
    mk = manifest_key(etag)
    s3.put_object(Bucket=DEST_BUCKET, Key=mk, Body=b"", ContentType="text/plain")


def copy_object(source_bucket: str, dest_bucket: str, source_key: str, dest_key: str) -> None:
    logger.info(
        "Copying s3://%s/%s -> s3://%s/%s",
        source_bucket,
        source_key,
        dest_bucket,
        dest_key,
    )
    s3.copy(
        {"Bucket": source_bucket, "Key": source_key},
        dest_bucket,
        dest_key,
        ExtraArgs={"MetadataDirective": "COPY"},
    )


def main() -> None:
    if SOURCE_BUCKET == DEST_BUCKET:
        raise RuntimeError("SOURCE_BUCKET and DEST_BUCKET must be different.")

    for obj in list_objects(SOURCE_BUCKET):
        key = obj["Key"]
        head = head_object(SOURCE_BUCKET, key)
        if head is None:
            continue

        if not is_image(key, head.get("ContentType")):
            continue

        etag = head.get("ETag", "").strip('"')
        if not etag:
            logger.warning("Skipping %s (missing ETag)", key)
            continue

        if already_copied(etag):
            logger.debug("Skipping %s (already copied)", key)
            continue

        dest_key = destination_key(key)
        try:
            copy_object(SOURCE_BUCKET, DEST_BUCKET, key, dest_key)
            mark_copied(etag)
        except Exception as error:  # pragma: no cover - defensive logging
            logger.exception("Failed to copy %s: %s", key, error)


if __name__ == "__main__":
    main()
