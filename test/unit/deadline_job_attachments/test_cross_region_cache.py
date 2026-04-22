# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""Tests for cross_region_cache module."""

import math
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch
import pytest
from botocore.exceptions import ClientError

from deadline.job_attachments.cross_region_cache import (
    MULTIPART_CHUNK_SIZE,
    _multipart_server_copy,
    _server_side_copy,
    warm_cache,
)


def _make_client_error(code: str, message: str = "error") -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": message}, "ResponseMetadata": {"HTTPStatusCode": 412}},
        "operation",
    )


class TestMultipartServerCopy:
    def test_single_part(self):
        """File smaller than chunk size should use one part."""
        client = MagicMock()
        client.create_multipart_upload.return_value = {"UploadId": "uid-1"}
        client.upload_part_copy.return_value = {
            "CopyPartResult": {"ETag": '"etag1"'}
        }

        with ThreadPoolExecutor(max_workers=4) as executor:
            _multipart_server_copy("src-bkt", "key1", "dst-bkt", "key1", 1024, client, executor)

        client.create_multipart_upload.assert_called_once_with(Bucket="dst-bkt", Key="key1")
        client.upload_part_copy.assert_called_once()
        client.complete_multipart_upload.assert_called_once()
        args = client.complete_multipart_upload.call_args
        assert args[1]["MultipartUpload"]["Parts"] == [{"PartNumber": 1, "ETag": '"etag1"'}]

    def test_multiple_parts(self):
        """File larger than chunk size should be split into multiple parts."""
        client = MagicMock()
        client.create_multipart_upload.return_value = {"UploadId": "uid-2"}
        client.upload_part_copy.return_value = {
            "CopyPartResult": {"ETag": '"etag"'}
        }

        size = MULTIPART_CHUNK_SIZE * 3 + 100
        with ThreadPoolExecutor(max_workers=4) as executor:
            _multipart_server_copy("src", "k", "dst", "k", size, client, executor)

        assert client.upload_part_copy.call_count == 4
        parts = client.complete_multipart_upload.call_args[1]["MultipartUpload"]["Parts"]
        assert [p["PartNumber"] for p in parts] == [1, 2, 3, 4]

    def test_byte_ranges_correct(self):
        """Byte ranges should cover the entire file without gaps or overlaps."""
        client = MagicMock()
        client.create_multipart_upload.return_value = {"UploadId": "uid-3"}
        client.upload_part_copy.return_value = {
            "CopyPartResult": {"ETag": '"e"'}
        }

        size = MULTIPART_CHUNK_SIZE * 2 + 1000
        with ThreadPoolExecutor(max_workers=4) as executor:
            _multipart_server_copy("src", "k", "dst", "k", size, client, executor)

        ranges = sorted(
            (c[1]["CopySourceRange"] for c in client.upload_part_copy.call_args_list),
            key=lambda r: int(r.split("=")[1].split("-")[0]),
        )

        assert ranges == [
            f"bytes=0-{MULTIPART_CHUNK_SIZE - 1}",
            f"bytes={MULTIPART_CHUNK_SIZE}-{2 * MULTIPART_CHUNK_SIZE - 1}",
            f"bytes={2 * MULTIPART_CHUNK_SIZE}-{size - 1}",
        ]

    def test_aborts_on_failure(self):
        """Should abort multipart upload if a part copy fails."""
        client = MagicMock()
        client.create_multipart_upload.return_value = {"UploadId": "uid-fail"}
        client.upload_part_copy.side_effect = _make_client_error("500", "Internal")

        with ThreadPoolExecutor(max_workers=4) as executor:
            with pytest.raises(ClientError):
                _multipart_server_copy("src", "k", "dst", "k", MULTIPART_CHUNK_SIZE + 1, client, executor)

        client.abort_multipart_upload.assert_called_once_with(
            Bucket="dst", Key="k", UploadId="uid-fail"
        )
        client.complete_multipart_upload.assert_not_called()


class TestServerSideCopy:
    def test_small_file_uses_copy_object(self):
        """Files <= chunk size should use single CopyObject."""
        client = MagicMock()
        with ThreadPoolExecutor(max_workers=4) as executor:
            _server_side_copy("src", "k", "dst", "k", 1024, client, executor)
        client.copy_object.assert_called_once()
        client.create_multipart_upload.assert_not_called()

    def test_large_file_uses_multipart(self):
        """Files > chunk size should use multipart copy."""
        client = MagicMock()
        client.create_multipart_upload.return_value = {"UploadId": "uid"}
        client.upload_part_copy.return_value = {"CopyPartResult": {"ETag": '"e"'}}

        with ThreadPoolExecutor(max_workers=4) as executor:
            _server_side_copy("src", "k", "dst", "k", MULTIPART_CHUNK_SIZE + 1, client, executor)
        client.copy_object.assert_not_called()
        client.create_multipart_upload.assert_called_once()


class TestWarmCache:
    def test_small_file_cache_miss(self):
        """Small file not in cache should be copied with IfNoneMatch."""
        client = MagicMock()
        client.copy_object.return_value = {}

        with ThreadPoolExecutor(max_workers=4) as executor:
            warm_cache("src", "key", "cache", "key", 1024, client, executor)

        client.copy_object.assert_called_once_with(
            CopySource={"Bucket": "src", "Key": "key"},
            Bucket="cache",
            Key="key",
            IfNoneMatch="*",
        )

    def test_small_file_already_cached(self):
        """Small file already in cache should be handled via PreconditionFailed."""
        client = MagicMock()
        client.copy_object.side_effect = _make_client_error("PreconditionFailed")

        with ThreadPoolExecutor(max_workers=4) as executor:
            warm_cache("src", "key", "cache", "key", 1024, client, executor)

        # Should not raise — PreconditionFailed is expected

    def test_large_file_cache_miss(self):
        """Large file not in cache should be multipart copied."""
        client = MagicMock()
        size = MULTIPART_CHUNK_SIZE + 1
        client.create_multipart_upload.return_value = {"UploadId": "uid"}
        client.upload_part_copy.return_value = {"CopyPartResult": {"ETag": '"e"'}}

        with ThreadPoolExecutor(max_workers=4) as executor:
            warm_cache("src", "key", "cache", "key", size, client, executor)

        client.create_multipart_upload.assert_called_once()

    def test_large_file_failure_propagates(self):
        """If multipart copy fails, exception should propagate (caller handles fallback)."""
        client = MagicMock()
        size = MULTIPART_CHUNK_SIZE + 1
        client.create_multipart_upload.return_value = {"UploadId": "uid"}
        client.upload_part_copy.side_effect = _make_client_error("500", "Internal")

        with ThreadPoolExecutor(max_workers=4) as executor:
            with pytest.raises(ClientError):
                warm_cache("src", "key", "cache", "key", size, client, executor)

        client.head_object.assert_not_called()
