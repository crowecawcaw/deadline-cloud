# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""
Cross-region read-through cache for job attachments.

When a satellite bucket is configured, the download path becomes:
1. Try GET from cache bucket (fast, in-region)
2. On 404: server-side copy from home bucket → cache bucket, then GET from cache

CAS semantics guarantee immutability — same hash = same content — so no invalidation is needed.
"""

import logging
import math
import time
import threading
from concurrent.futures import ThreadPoolExecutor, Future, as_completed
from typing import Optional

from botocore.exceptions import ClientError
from botocore.client import BaseClient

logger = logging.getLogger(__name__)

MULTIPART_CHUNK_SIZE = 10 * 1024 * 1024  # 10 MB — sweet spot for cross-region parallelism
MAX_RETRIES = 5
SINGLE_COPY_THRESHOLD = MULTIPART_CHUNK_SIZE


def _retry_on_throttle(fn, *args, **kwargs):
    for attempt in range(MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code in ("SlowDown", "Throttling", "RequestLimitExceeded", "InternalError") and attempt < MAX_RETRIES:
                time.sleep(min(2 ** attempt * 0.2, 10))
                continue
            raise


def _multipart_server_copy(
    source_bucket: str,
    source_key: str,
    dest_bucket: str,
    dest_key: str,
    size: int,
    dest_s3_client: BaseClient,
    executor: ThreadPoolExecutor,
) -> None:
    chunk_size = MULTIPART_CHUNK_SIZE
    num_parts = math.ceil(size / chunk_size)

    mpu = dest_s3_client.create_multipart_upload(Bucket=dest_bucket, Key=dest_key)
    upload_id = mpu["UploadId"]

    try:
        def _do_part(part_num, start, end):
            return _retry_on_throttle(
                dest_s3_client.upload_part_copy,
                Bucket=dest_bucket, Key=dest_key, UploadId=upload_id,
                PartNumber=part_num,
                CopySource={"Bucket": source_bucket, "Key": source_key},
                CopySourceRange=f"bytes={start}-{end}",
            )

        futures: dict[Future, int] = {}
        for i in range(num_parts):
            start = i * chunk_size
            end = min(start + chunk_size - 1, size - 1)
            part_num = i + 1
            futures[executor.submit(_do_part, part_num, start, end)] = part_num

        parts = []
        for f in as_completed(futures):
            resp = f.result()
            parts.append({
                "PartNumber": futures[f],
                "ETag": resp["CopyPartResult"]["ETag"],
            })

        parts.sort(key=lambda p: p["PartNumber"])
        dest_s3_client.complete_multipart_upload(
            Bucket=dest_bucket, Key=dest_key, UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )
    except Exception:
        dest_s3_client.abort_multipart_upload(
            Bucket=dest_bucket, Key=dest_key, UploadId=upload_id
        )
        raise


def warm_cache(
    source_bucket: str,
    source_key: str,
    cache_bucket: str,
    cache_key: str,
    source_size: int,
    cache_s3_client: BaseClient,
    executor: ThreadPoolExecutor,
) -> None:
    if source_size <= SINGLE_COPY_THRESHOLD:
        try:
            _retry_on_throttle(
                cache_s3_client.copy_object,
                CopySource={"Bucket": source_bucket, "Key": source_key},
                Bucket=cache_bucket, Key=cache_key,
                IfNoneMatch="*",
            )
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code in ("PreconditionFailed", "412"):
                return
            raise
    else:
        _multipart_server_copy(
            source_bucket, source_key, cache_bucket, cache_key,
            source_size, cache_s3_client, executor,
        )
