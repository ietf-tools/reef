# Copyright The IETF Trust 2026, All Rights Reserved
"""Where precomputed responses land.

Two backends behind one interface. S3 is the real one: an S3-compatible bucket
a CDN or Red reads straight out of, configured by REEF_PRECOMPUTE_S3_*. A local
directory is the fallback, used whenever the bucket is not configured, so that
a developer can run the precomputer and look at what it produced without any
object storage to hand.

Which one is in use is decided by configuration alone, never by an argument to
the command, so a deployment cannot be talked into writing its production
payloads to a directory inside the container.
"""

import logging
import os
import threading
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger("reef")


class BlobStore:
    """Put, list and delete, keyed by path-like strings.

    Keys are relative paths with forward slashes and no leading slash, the same
    in both backends, so the layout a local run produces is the layout the
    bucket gets.
    """

    def put(self, key, body, content_type="application/json"):
        raise NotImplementedError

    def list_keys(self):
        raise NotImplementedError

    def delete(self, key):
        raise NotImplementedError


class LocalBlobStore(BlobStore):
    """Writes into a directory. The development and no-credentials fallback."""

    def __init__(self, root):
        self.root = Path(root)

    def __str__(self):
        return f"local directory {self.root}"

    def _path(self, key):
        path = (self.root / key).resolve()
        root = self.root.resolve()
        # A key is built from a slug or a document identifier, both of which
        # are validated upstream, but this is the step that turns one into a
        # filesystem path and so is where an escape would be spent.
        if not path.is_relative_to(root):
            raise ValueError(f"Key escapes the output directory: {key!r}")
        return path

    def put(self, key, body, content_type="application/json"):
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Written beside the target and moved into place, so that a reader
        # never sees a half-written file and a crash leaves the previous
        # payload intact.
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_bytes(body)
        tmp.replace(path)

    def list_keys(self):
        if not self.root.exists():
            return []
        return [
            str(path.relative_to(self.root).as_posix())
            for path in self.root.rglob("*")
            if path.is_file() and not path.name.startswith(".")
        ]

    def delete(self, key):
        self._path(key).unlink(missing_ok=True)


class S3BlobStore(BlobStore):
    """Writes to an S3-compatible bucket.

    One client shared across the upload threads: boto3 clients are thread-safe
    for calls, and building one per key would re-resolve credentials on every
    upload. Retries are left to botocore, which already backs off and is what
    a signature clock skew or a 503 from the endpoint needs.
    """

    def __init__(
        self, *, endpoint_url, bucket, access_key_id, secret_access_key, region
    ):
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self.endpoint_url = endpoint_url
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(retries={"max_attempts": 5, "mode": "standard"}),
        )
        self._lock = threading.Lock()

    def __str__(self):
        where = self.endpoint_url or "AWS"
        return f"bucket {self.bucket} at {where}"

    def put(self, key, body, content_type="application/json"):
        self._client.put_object(
            Bucket=self.bucket, Key=key, Body=body, ContentType=content_type
        )

    def list_keys(self):
        keys = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        return keys

    def delete(self, key):
        self._client.delete_object(Bucket=self.bucket, Key=key)


def get_blob_store():
    """The configured store: S3 if a bucket is named, otherwise a directory."""
    bucket = settings.REEF_PRECOMPUTE_S3_BUCKET
    if not bucket:
        return LocalBlobStore(settings.REEF_PRECOMPUTE_DIR)

    missing = [
        name
        for name in (
            "REEF_PRECOMPUTE_S3_ACCESS_KEY_ID",
            "REEF_PRECOMPUTE_S3_SECRET_ACCESS_KEY",
        )
        if not getattr(settings, name)
    ]
    if missing:
        # Naming a bucket and leaving the credentials out is a deployment
        # mistake, not a request to fall back to the local directory: falling
        # back would quietly write production payloads nowhere anybody reads.
        raise ImproperlyConfigured(
            "REEF_PRECOMPUTE_S3_BUCKET is set but "
            + " and ".join(missing)
            + " is empty."
        )

    return S3BlobStore(
        endpoint_url=settings.REEF_PRECOMPUTE_S3_ENDPOINT,
        bucket=bucket,
        access_key_id=settings.REEF_PRECOMPUTE_S3_ACCESS_KEY_ID,
        secret_access_key=settings.REEF_PRECOMPUTE_S3_SECRET_ACCESS_KEY,
        region=settings.REEF_PRECOMPUTE_S3_REGION,
    )


__all__ = ["BlobStore", "LocalBlobStore", "S3BlobStore", "get_blob_store"]
