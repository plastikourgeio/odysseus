"""
Safe STL analysis tool for Odysseus.

Accepts only an attachment_id belonging to the current owner and
forwards only that attachment's bytes to the internal stl-analyzer.
"""

import asyncio
import os
from pathlib import Path

import httpx

from src.tool_utils import _parse_tool_args, get_upload_handler


STL_ANALYZER_URL = os.environ.get(
    "STL_ANALYZER_URL",
    "http://stl-analyzer:8787",
).rstrip("/")

MAX_STL_BYTES = 25 * 1024 * 1024


class STLAnalyzeTool:

    async def execute(self, content: str, ctx: dict) -> dict:
        try:
            args = _parse_tool_args(content)
        except Exception:
            return {
                "error": "Invalid stl_analyze arguments.",
                "exit_code": 1,
            }

        attachment_id = str(
            args.get("attachment_id") or ""
        ).strip()

        owner = str(
            ctx.get("owner") or ""
        ).strip()

        if not owner:
            return {
                "error": "Authenticated owner is required.",
                "exit_code": 1,
            }

        if not attachment_id:
            return {
                "error": "attachment_id is required.",
                "exit_code": 1,
            }

        upload_handler = get_upload_handler()

        if upload_handler is None:
            return {
                "error": "Upload service is unavailable.",
                "exit_code": 1,
            }

        # Owner-aware resolution.
        # Explicitly disable administrative bypass for this child-safe tool.
        info = upload_handler.resolve_upload(
            attachment_id,
            owner=owner,
            allow_admin=False,
        )

        if not info:
            return {
                "error": (
                    "Attachment not found or not owned "
                    "by the current user."
                ),
                "exit_code": 1,
            }

        original_name = str(
            info.get("original_name")
            or info.get("name")
            or ""
        )

        filename = os.path.basename(original_name)

        if not filename.lower().endswith(".stl"):
            return {
                "error": "Only .stl attachments are accepted.",
                "exit_code": 1,
            }

        path = str(info.get("path") or "")

        if not path:
            return {
                "error": "Resolved attachment has no file.",
                "exit_code": 1,
            }

        try:
            size = os.path.getsize(path)
        except OSError:
            return {
                "error": "Unable to read the STL attachment.",
                "exit_code": 1,
            }

        if size <= 0:
            return {
                "error": "The STL attachment is empty.",
                "exit_code": 1,
            }

        if size > MAX_STL_BYTES:
            return {
                "error": (
                    "STL exceeds the 25 MB analysis limit."
                ),
                "exit_code": 1,
            }

        try:
            payload = await asyncio.to_thread(
                Path(path).read_bytes
            )
        except OSError:
            return {
                "error": "Unable to read the STL attachment.",
                "exit_code": 1,
            }

        try:
            timeout = httpx.Timeout(
                35.0,
                connect=3.0,
            )

            async with httpx.AsyncClient(
                timeout=timeout
            ) as client:
                response = await client.post(
                    f"{STL_ANALYZER_URL}/analyze",
                    files={
                        "file": (
                            filename,
                            payload,
                            "model/stl",
                        )
                    },
                )

        except httpx.RequestError:
            return {
                "error": (
                    "STL analyzer service is unavailable."
                ),
                "exit_code": 1,
            }

        if response.status_code != 200:
            return {
                "error": "STL analyzer rejected the model.",
                "analyzer_status": response.status_code,
                "exit_code": 1,
            }

        try:
            analysis = response.json()
        except ValueError:
            return {
                "error": (
                    "STL analyzer returned invalid JSON."
                ),
                "exit_code": 1,
            }

        if not isinstance(analysis, dict):
            return {
                "error": (
                    "STL analyzer returned an invalid result."
                ),
                "exit_code": 1,
            }

        return {
            "ok": True,
            "file": {
                "name": filename,
                "size_bytes": size,
            },
            "units": {
                "declared_by_stl": False,
                "note": (
                    "STL geometry is unitless. "
                    "Do not claim millimetres unless the "
                    "user/export workflow confirms them."
                ),
            },
            "analysis": analysis,
        }
