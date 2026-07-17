"""Vercel Blob storage integration for PDF uploads.

Uses the official `vercel_blob` Python package which speaks the actual
Vercel Blob REST contract (PUT to blob.vercel-storage.com with raw body
and x-api-version / x-content-type headers). The previous version of
this file POSTed multipart/form-data to the base URL — that is NOT the
Vercel Blob API and Vercel returned 403 for every request regardless of
how valid the token was.
"""
import os
from datetime import date

import structlog
import vercel_blob

logger = structlog.get_logger(__name__)


def _put(pathname: str, body: bytes, token: str) -> str:
    """Upload bytes to Vercel Blob at the given pathname; return the public URL.

    `vercel_blob.put` reads the token from the BLOB_READ_WRITE_TOKEN env var,
    so we set it for the duration of this call instead of passing it via
    options (the package's `options.token` field is finicky across versions).
    """
    previous = os.environ.get("BLOB_READ_WRITE_TOKEN")
    os.environ["BLOB_READ_WRITE_TOKEN"] = token
    try:
        result = vercel_blob.put(
            pathname,
            body,
            options={
                "contentType": "application/pdf",
                "addRandomSuffix": "false",
                "allowOverwrite": "true",
                "cacheControlMaxAge": "31536000",
            },
        )
    finally:
        if previous is None:
            os.environ.pop("BLOB_READ_WRITE_TOKEN", None)
        else:
            os.environ["BLOB_READ_WRITE_TOKEN"] = previous

    url = result.get("url") if isinstance(result, dict) else None
    if not url:
        raise ValueError(f"Vercel Blob put returned no URL: {result!r}")
    return url


def upload_pdf_to_vercel_blob(
    pdf_binary: bytes, target_name: str, run_date: date, vercel_token: str
) -> str:
    """Upload a per-target PDF and return its public URL."""
    pathname = f"reports/{run_date}/{target_name}/{target_name}_{run_date}.pdf"
    try:
        url = _put(pathname, pdf_binary, vercel_token)
    except Exception as e:
        logger.error("vercel_blob.upload_failed", error=str(e), target=target_name, date=str(run_date))
        raise
    logger.info("vercel_blob.pdf_uploaded", target=target_name, date=str(run_date), url=url)
    return url


def upload_daily_summary_to_vercel_blob(
    pdf_binary: bytes, run_date: date, vercel_token: str
) -> str:
    """Upload the daily summary PDF and return its public URL."""
    pathname = f"reports/{run_date}/Daily_Summary_{run_date}.pdf"
    try:
        url = _put(pathname, pdf_binary, vercel_token)
    except Exception as e:
        logger.error("vercel_blob.daily_upload_failed", error=str(e), date=str(run_date))
        raise
    logger.info("vercel_blob.daily_summary_uploaded", date=str(run_date), url=url)
    return url


def upload_burning_topic_pdf(
    pdf_binary: bytes, topic_slug: str, report_id: int, vercel_token: str
) -> str:
    """Upload a burning-topic report PDF and return its public URL.

    Pathname includes the report id so successive reports for the same topic
    never overwrite each other (unlike the per-day report paths above)."""
    pathname = f"burning-topics/{topic_slug}/report_{report_id}.pdf"
    try:
        url = _put(pathname, pdf_binary, vercel_token)
    except Exception as e:
        logger.error("vercel_blob.burning_topic_upload_failed", error=str(e),
                     topic=topic_slug, report_id=report_id)
        raise
    logger.info("vercel_blob.burning_topic_uploaded", topic=topic_slug,
                report_id=report_id, url=url)
    return url
