"""BatchSubmitter: submits requests to the Anthropic Message Batches API and polls results."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING

import aiosqlite
from anthropic import AsyncAnthropic

from ahsoka import database as db
from ahsoka.models import PersonalizedVerdict, Post, UserConfig
from ahsoka.pipeline.batch_queue import BatchRequest
from ahsoka.pipeline.scorer import build_personalized_prompt, parse_verdict

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class BatchSubmitter:
    """Wraps the Anthropic messages.batches API.

    Each instance owns one aiosqlite connection and one AsyncAnthropic client.
    Call submit() to dispatch a batch and get back a batch_id.
    Call poll_and_process() to wait for completion and parse results.
    """

    def __init__(
        self,
        client: AsyncAnthropic,
        conn: aiosqlite.Connection,
        model: str,
        poll_interval_seconds: int = 60,
        max_wait_seconds: int = 1800,
    ) -> None:
        self._client = client
        self._conn = conn
        self._model = model
        self._poll_interval = poll_interval_seconds
        self._max_wait_seconds = max_wait_seconds

    async def submit(self, requests: list[BatchRequest]) -> str:
        """Create a batch via the Anthropic API and persist its state.

        Returns the batch_id on success. Raises on API errors after exponential
        backoff (callers should catch and retry or persist for later).
        """
        if not requests:
            raise ValueError("Cannot submit an empty batch")

        # Build the Anthropic request list — inject the model from settings.
        api_requests = []
        request_map: dict[str, tuple[int, int, int]] = {}
        for req in requests:
            prompt_dict = build_personalized_prompt(req.post, req.content, req.config)
            prompt_dict["params"]["model"] = self._model
            api_requests.append(prompt_dict)
            cid, mid, uid = req.custom_id.split("_")
            request_map[req.custom_id] = (int(cid), int(mid), int(uid))

        last_exc: Exception | None = None
        for attempt in range(4):
            try:
                batch = await self._client.messages.batches.create(
                    requests=api_requests  # type: ignore[arg-type]
                )
                batch_id: str = batch.id
                logger.info(
                    "batch submitted batch_id=%s size=%d", batch_id, len(requests)
                )
                await db.save_pending_batch(
                    self._conn,
                    batch_id=batch_id,
                    request_map=request_map,
                )
                return batch_id
            except Exception as exc:
                last_exc = exc
                wait = 2 ** attempt
                logger.warning(
                    "Batch submit attempt %d failed (%s), retrying in %ds",
                    attempt + 1, exc, wait,
                )
                await asyncio.sleep(wait)

        raise RuntimeError(
            f"Batch submission failed after 4 attempts: {last_exc}"
        ) from last_exc

    async def poll_and_process(
        self,
        batch_id: str,
        requests: list[BatchRequest],
    ) -> list[tuple[Post, UserConfig, PersonalizedVerdict]]:
        """Poll until the batch completes, then parse and return verdicts.

        requests must be the same list passed to submit() for this batch_id
        so we can map custom_ids back to (Post, UserConfig) objects.
        """
        # Build lookup: custom_id -> (post, config)
        lookup: dict[str, tuple[Post, UserConfig]] = {
            r.custom_id: (r.post, r.config) for r in requests
        }

        start = time.monotonic()
        while True:
            try:
                batch = await self._client.messages.batches.retrieve(batch_id)
            except Exception as exc:
                logger.warning("Error retrieving batch %s: %s", batch_id, exc)
                await asyncio.sleep(self._poll_interval)
                continue

            status = batch.processing_status
            logger.debug("Batch %s status: %s", batch_id, status)

            if status == "ended":
                break

            elapsed = time.monotonic() - start
            if elapsed >= self._max_wait_seconds:
                logger.error(
                    "Batch %s timed out after %.0fs — marking failed", batch_id, elapsed
                )
                await db.mark_batch_complete(self._conn, batch_id, status="failed")
                return []

            await asyncio.sleep(self._poll_interval)

        # Fetch results
        results: list[tuple[Post, UserConfig, PersonalizedVerdict]] = []
        total_input = total_output = total_cache_write = total_cache_read = succeeded_count = 0
        try:
            async for result in await self._client.messages.batches.results(batch_id):
                custom_id = result.custom_id
                # Accumulate token usage before converting to plain dict
                usage = getattr(getattr(result.result, "message", None), "usage", None)
                if usage:
                    total_input      += getattr(usage, "input_tokens", 0) or 0
                    total_output     += getattr(usage, "output_tokens", 0) or 0
                    total_cache_write += getattr(usage, "cache_creation_input_tokens", 0) or 0
                    total_cache_read  += getattr(usage, "cache_read_input_tokens", 0) or 0
                    succeeded_count  += 1
                # parse the result — the SDK object has .result.type / .result.message
                result_dict = _sdk_result_to_dict(result)
                uid_str = custom_id.split("_")[-1] if "_" in custom_id else "0"
                user_id = int(uid_str)
                verdict = parse_verdict(result_dict, user_id)

                pair = lookup.get(custom_id)
                if pair is None:
                    logger.warning("Unknown custom_id in batch results: %s", custom_id)
                    continue
                post, config = pair
                results.append((post, config, verdict))
        except Exception as exc:
            logger.error("Failed to fetch results for batch %s: %s", batch_id, exc)
            await db.mark_batch_complete(self._conn, batch_id, status="failed")
            return results

        duration_s = int(time.monotonic() - start)
        logger.info(
            "batch complete batch_id=%s duration_s=%d verdicts=%d",
            batch_id, duration_s, len(results),
        )
        await db.mark_batch_complete(self._conn, batch_id, status="complete")
        await db.save_batch_usage(
            self._conn, batch_id, self._model,
            total_input, total_output, total_cache_write, total_cache_read, succeeded_count,
        )
        return results


    async def recover(self, batch_id: str, request_map: dict) -> None:
        """Poll a recovered batch to completion and store verdicts (no notifications).

        request_map values are (channel_id, message_id, user_id) tuples as stored
        in the pending_batches table. Original Post/UserConfig objects are gone, so
        verdicts are stored for auditability only — no fan-out is performed.
        """
        start = time.monotonic()
        while True:
            try:
                batch = await self._client.messages.batches.retrieve(batch_id)
            except Exception as exc:
                logger.warning("Recovery: error retrieving batch %s: %s", batch_id, exc)
                await asyncio.sleep(self._poll_interval)
                continue

            if batch.processing_status == "ended":
                break

            elapsed = time.monotonic() - start
            if elapsed >= self._max_wait_seconds:
                logger.error(
                    "Recovery: batch %s timed out after %.0fs — marking failed",
                    batch_id, elapsed,
                )
                await db.mark_batch_complete(self._conn, batch_id, status="failed")
                return

            await asyncio.sleep(self._poll_interval)

        total_input = total_output = total_cache_write = total_cache_read = succeeded_count = 0
        try:
            async for result in await self._client.messages.batches.results(batch_id):
                custom_id = result.custom_id
                mapping = request_map.get(custom_id)
                if mapping is None:
                    logger.warning("Recovery: unknown custom_id %s in batch %s", custom_id, batch_id)
                    continue
                channel_id, message_id, user_id = mapping
                # Accumulate token usage
                usage = getattr(getattr(result.result, "message", None), "usage", None)
                if usage:
                    total_input      += getattr(usage, "input_tokens", 0) or 0
                    total_output     += getattr(usage, "output_tokens", 0) or 0
                    total_cache_write += getattr(usage, "cache_creation_input_tokens", 0) or 0
                    total_cache_read  += getattr(usage, "cache_read_input_tokens", 0) or 0
                    succeeded_count  += 1
                result_dict = _sdk_result_to_dict(result)
                verdict = parse_verdict(result_dict, user_id)
                await db.store_verdict(self._conn, verdict, channel_id, message_id)
        except Exception as exc:
            logger.error("Recovery: batch %s failed to fetch results: %s", batch_id, exc)
            await db.mark_batch_complete(self._conn, batch_id, status="failed")
            return

        await db.mark_batch_complete(self._conn, batch_id, status="complete")
        await db.save_batch_usage(
            self._conn, batch_id, self._model,
            total_input, total_output, total_cache_write, total_cache_read, succeeded_count,
        )
        logger.info("Recovery: batch %s completed and verdicts stored", batch_id)


def _sdk_result_to_dict(result: object) -> dict:
    """Normalise an SDK MessageBatchIndividualResponse object to a plain dict
    so parse_verdict doesn't need to import SDK types."""
    r = getattr(result, "result", None)
    if r is None:
        return {"result": {"type": "error", "error": {"message": "missing result"}}}

    result_type = getattr(r, "type", None)
    if result_type == "succeeded":
        msg = getattr(r, "message", None)
        if msg is None:
            return {"result": {"type": "error", "error": {"message": "missing message"}}}
        # Convert content blocks to plain dicts
        content_blocks = []
        for block in getattr(msg, "content", []):
            if hasattr(block, "text"):
                content_blocks.append({"type": "text", "text": block.text})
            else:
                content_blocks.append({"type": getattr(block, "type", "unknown")})
        return {
            "result": {
                "type": "succeeded",
                "message": {"content": content_blocks},
            }
        }

    # errored / canceled / expired
    error = getattr(r, "error", None)
    error_msg = getattr(error, "message", str(r)) if error else str(result_type)
    return {
        "result": {
            "type": result_type or "error",
            "error": {"message": error_msg},
        }
    }
