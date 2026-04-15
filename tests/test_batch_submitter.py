"""Tests for ahsoka.pipeline.batch_submitter.BatchSubmitter."""
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import aiosqlite

from ahsoka.database import init_db
from ahsoka.models import Post, UserConfig, PersonalizedVerdict
from ahsoka.pipeline.batch_queue import BatchRequest
from ahsoka.pipeline.batch_submitter import BatchSubmitter, _sdk_result_to_dict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_post(channel_id: int = 111, message_id: int = 222) -> Post:
    return Post(
        channel_id=channel_id,
        message_id=message_id,
        channel_name="testchan",
        text="Python backend job",
        timestamp=datetime.now(),
    )


def make_config(user_id: int = 42) -> UserConfig:
    return UserConfig(user_id=user_id, notify_chat_id=user_id, stack="python", threshold=7)


def make_request(
    post: Post | None = None,
    config: UserConfig | None = None,
    content: str = "job content",
) -> BatchRequest:
    p = post or make_post()
    c = config or make_config()
    return BatchRequest(
        custom_id=f"{p.channel_id}_{p.message_id}_{c.user_id}",
        post=p,
        content=content,
        config=c,
    )


@pytest.fixture
async def conn():
    async with aiosqlite.connect(":memory:") as c:
        await init_db(c)
        yield c


def make_submitter(conn, model: str = "claude-3-5-haiku-20241022") -> tuple[BatchSubmitter, AsyncMock]:
    client = AsyncMock()
    submitter = BatchSubmitter(
        client=client,
        conn=conn,
        model=model,
        poll_interval_seconds=0,  # no real sleep in tests
    )
    return submitter, client


# ---------------------------------------------------------------------------
# submit
# ---------------------------------------------------------------------------


async def test_submit_calls_batches_create_with_model_injected(conn):
    submitter, client = make_submitter(conn, model="claude-haiku")
    fake_batch = MagicMock()
    fake_batch.id = "batch_abc123"
    client.messages.batches.create = AsyncMock(return_value=fake_batch)

    reqs = [make_request(make_post(channel_id=111, message_id=222), make_config(42))]
    batch_id = await submitter.submit(reqs)

    assert batch_id == "batch_abc123"
    create_call = client.messages.batches.create.call_args
    api_requests = create_call.kwargs["requests"]
    assert len(api_requests) == 1
    assert api_requests[0]["params"]["model"] == "claude-haiku"
    assert api_requests[0]["custom_id"] == "111_222_42"


async def test_submit_saves_pending_batch_to_db(conn):
    submitter, client = make_submitter(conn)
    fake_batch = MagicMock()
    fake_batch.id = "batch_xyz"
    client.messages.batches.create = AsyncMock(return_value=fake_batch)

    reqs = [make_request()]
    await submitter.submit(reqs)

    # Verify the batch was persisted
    from ahsoka.database import get_pending_batches
    pending = await get_pending_batches(conn)
    assert any(p["batch_id"] == "batch_xyz" for p in pending)


async def test_submit_returns_batch_id(conn):
    submitter, client = make_submitter(conn)
    fake_batch = MagicMock()
    fake_batch.id = "batch_return_test"
    client.messages.batches.create = AsyncMock(return_value=fake_batch)

    result = await submitter.submit([make_request()])
    assert result == "batch_return_test"


async def test_submit_retries_up_to_4_times_on_api_exception(conn):
    submitter, client = make_submitter(conn)
    client.messages.batches.create = AsyncMock(side_effect=RuntimeError("API down"))

    with patch("ahsoka.pipeline.batch_submitter.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(RuntimeError, match="Batch submission failed after 4 attempts"):
            await submitter.submit([make_request()])

    assert client.messages.batches.create.call_count == 4


async def test_submit_raises_value_error_on_empty_requests(conn):
    submitter, _ = make_submitter(conn)
    with pytest.raises(ValueError):
        await submitter.submit([])


# ---------------------------------------------------------------------------
# poll_and_process
# ---------------------------------------------------------------------------


def _make_sdk_result(custom_id: str, score: int = 7) -> MagicMock:
    """Build a fake SDK MessageBatchIndividualResponse."""
    result = MagicMock()
    result.custom_id = custom_id

    block = MagicMock()
    payload = {"score": score, "reason": "test", "matched": True, "apply": "", "red_flags": []}
    # The prompt prefills "{", so the model's text starts without it
    block.text = json.dumps(payload)[1:]

    msg = MagicMock()
    msg.content = [block]
    msg.usage = None  # explicit None so token-accumulation code skips it

    inner_result = MagicMock()
    inner_result.type = "succeeded"
    inner_result.message = msg

    result.result = inner_result
    return result


async def test_poll_and_process_polls_until_ended(conn):
    submitter, client = make_submitter(conn)

    # First retrieve: processing; second: ended
    batch_processing = MagicMock(processing_status="processing")
    batch_ended = MagicMock(processing_status="ended")
    client.messages.batches.retrieve = AsyncMock(
        side_effect=[batch_processing, batch_ended]
    )

    custom_id = "111_222_42"
    sdk_result = _make_sdk_result(custom_id, score=8)

    async def fake_results(batch_id):
        async def _gen():
            yield sdk_result
        return _gen()

    client.messages.batches.results = fake_results

    # Save the batch first (poll_and_process calls mark_batch_complete)
    from ahsoka.database import save_pending_batch
    await save_pending_batch(conn, "batch_poll_test", {custom_id: [111, 222, 42]})

    reqs = [make_request(make_post(111, 222), make_config(42))]

    with patch("ahsoka.pipeline.batch_submitter.asyncio.sleep", new_callable=AsyncMock):
        results = await submitter.poll_and_process("batch_poll_test", reqs)

    assert len(results) == 1
    post, config, verdict = results[0]
    assert verdict.score == 8
    assert verdict.user_id == 42


async def test_poll_and_process_maps_custom_id_to_correct_post_and_config(conn):
    submitter, client = make_submitter(conn)
    batch_ended = MagicMock(processing_status="ended")
    client.messages.batches.retrieve = AsyncMock(return_value=batch_ended)

    post1 = make_post(channel_id=100, message_id=1)
    post2 = make_post(channel_id=200, message_id=2)
    config1 = make_config(user_id=10)
    config2 = make_config(user_id=20)

    req1 = make_request(post1, config1)
    req2 = make_request(post2, config2)

    sdk_r1 = _make_sdk_result("100_1_10", score=7)
    sdk_r2 = _make_sdk_result("200_2_20", score=9)

    async def fake_results(batch_id):
        async def _gen():
            yield sdk_r1
            yield sdk_r2
        return _gen()

    client.messages.batches.results = fake_results

    from ahsoka.database import save_pending_batch
    await save_pending_batch(conn, "batch_map_test", {
        "100_1_10": [100, 1, 10],
        "200_2_20": [200, 2, 20],
    })

    results = await submitter.poll_and_process("batch_map_test", [req1, req2])

    assert len(results) == 2
    result_map = {r[2].user_id: r for r in results}
    assert result_map[10][0].channel_id == 100
    assert result_map[20][0].channel_id == 200
    assert result_map[10][2].score == 7
    assert result_map[20][2].score == 9


async def test_poll_and_process_marks_complete_on_success(conn):
    submitter, client = make_submitter(conn)
    batch_ended = MagicMock(processing_status="ended")
    client.messages.batches.retrieve = AsyncMock(return_value=batch_ended)

    custom_id = "111_222_42"
    sdk_result = _make_sdk_result(custom_id)

    async def fake_results(batch_id):
        async def _gen():
            yield sdk_result
        return _gen()

    client.messages.batches.results = fake_results

    from ahsoka.database import save_pending_batch, get_pending_batches
    await save_pending_batch(conn, "batch_complete_test", {custom_id: [111, 222, 42]})

    reqs = [make_request(make_post(111, 222), make_config(42))]
    await submitter.poll_and_process("batch_complete_test", reqs)

    pending = await get_pending_batches(conn)
    assert not any(p["batch_id"] == "batch_complete_test" for p in pending)


async def test_poll_and_process_marks_failed_on_timeout(conn):
    """When the batch processing_status never becomes 'ended', poll_and_process
    times out and marks the batch failed.

    We cannot patch time.monotonic globally because asyncio's event loop uses
    it internally. Instead we set submitter._max_wait_seconds = -1, which makes
    elapsed >= _max_wait_seconds on the very first check.
    """
    submitter, client = make_submitter(conn)
    submitter._max_wait_seconds = -1

    batch_processing = MagicMock(processing_status="processing")
    client.messages.batches.retrieve = AsyncMock(return_value=batch_processing)

    from ahsoka.database import save_pending_batch, get_pending_batches
    await save_pending_batch(conn, "batch_timeout_test", {"111:222:42": [111, 222, 42]})

    reqs = [make_request()]

    with patch("ahsoka.pipeline.batch_submitter.asyncio.sleep", new_callable=AsyncMock):
        result = await submitter.poll_and_process("batch_timeout_test", reqs)

    assert result == []
    pending = await get_pending_batches(conn)
    assert not any(p["batch_id"] == "batch_timeout_test" for p in pending)


async def test_poll_and_process_skips_unknown_custom_id(conn):
    """Unknown custom_id in results: log warning and skip gracefully."""
    submitter, client = make_submitter(conn)
    batch_ended = MagicMock(processing_status="ended")
    client.messages.batches.retrieve = AsyncMock(return_value=batch_ended)

    # Result with a custom_id that does NOT exist in the requests list
    unknown_result = _make_sdk_result("999:999:999", score=5)

    async def fake_results(batch_id):
        async def _gen():
            yield unknown_result
        return _gen()

    client.messages.batches.results = fake_results

    from ahsoka.database import save_pending_batch
    await save_pending_batch(conn, "batch_unknown_test", {"999:999:999": [999, 999, 999]})

    # requests list contains 111:222:42, not 999:999:999
    reqs = [make_request(make_post(111, 222), make_config(42))]
    results = await submitter.poll_and_process("batch_unknown_test", reqs)

    # The unknown custom_id is skipped — no result for it
    assert all(r[2].user_id != 999 for r in results)


# ---------------------------------------------------------------------------
# _sdk_result_to_dict
# ---------------------------------------------------------------------------


def test_sdk_result_to_dict_succeeded():
    block = MagicMock()
    block.text = '"score": 7, "reason": "ok"}'  # continuation after "{"

    msg = MagicMock()
    msg.content = [block]

    inner = MagicMock()
    inner.type = "succeeded"
    inner.message = msg

    sdk_result = MagicMock()
    sdk_result.result = inner

    result = _sdk_result_to_dict(sdk_result)
    assert result["result"]["type"] == "succeeded"
    content = result["result"]["message"]["content"]
    assert len(content) == 1
    assert content[0]["type"] == "text"
    assert content[0]["text"] == '"score": 7, "reason": "ok"}'


def test_sdk_result_to_dict_errored():
    error = MagicMock()
    error.message = "internal server error"

    inner = MagicMock()
    inner.type = "errored"
    inner.error = error

    sdk_result = MagicMock()
    sdk_result.result = inner

    result = _sdk_result_to_dict(sdk_result)
    assert result["result"]["type"] == "errored"
    assert result["result"]["error"]["message"] == "internal server error"


def test_sdk_result_to_dict_missing_result_attribute():
    sdk_result = MagicMock(spec=[])  # no .result attribute
    result = _sdk_result_to_dict(sdk_result)
    assert result["result"]["type"] == "error"
    assert "missing result" in result["result"]["error"]["message"]


# ---------------------------------------------------------------------------
# recover
# ---------------------------------------------------------------------------


async def test_recover_timeout_marks_batch_failed(conn):
    """Batch never reaches 'ended' within max_wait_seconds=-1 → mark_batch_complete('failed'), no results."""
    submitter, client = make_submitter(conn)
    submitter._max_wait_seconds = -1

    batch_processing = MagicMock(processing_status="processing")
    client.messages.batches.retrieve = AsyncMock(return_value=batch_processing)

    from ahsoka.database import save_pending_batch, get_pending_batches
    await save_pending_batch(conn, "batch_recover_timeout", {"111:222:42": [111, 222, 42]})

    request_map = {"111:222:42": [111, 222, 42]}

    with patch("ahsoka.pipeline.batch_submitter.asyncio.sleep", new_callable=AsyncMock):
        await submitter.recover("batch_recover_timeout", request_map)

    # Batch should be marked failed and removed from pending
    pending = await get_pending_batches(conn)
    assert not any(p["batch_id"] == "batch_recover_timeout" for p in pending)
    # results should NOT have been called (returned before results loop)
    client.messages.batches.results.assert_not_called()


async def test_recover_success_path_stores_verdict_and_marks_complete(conn):
    """Batch ends immediately → results iterated → store_verdict called → mark_batch_complete('complete')."""
    submitter, client = make_submitter(conn)

    batch_ended = MagicMock(processing_status="ended")
    client.messages.batches.retrieve = AsyncMock(return_value=batch_ended)

    custom_id = "111_222_42"
    sdk_result = _make_sdk_result(custom_id, score=8)

    async def fake_results(batch_id):
        async def _gen():
            yield sdk_result
        return _gen()

    client.messages.batches.results = fake_results

    from ahsoka.database import save_pending_batch, get_pending_batches
    await save_pending_batch(conn, "batch_recover_ok", {custom_id: [111, 222, 42]})

    request_map = {custom_id: [111, 222, 42]}

    with patch("ahsoka.pipeline.batch_submitter.db.store_verdict", new_callable=AsyncMock) as mock_store, \
         patch("ahsoka.pipeline.batch_submitter.db.mark_batch_complete", new_callable=AsyncMock) as mock_complete:
        await submitter.recover("batch_recover_ok", request_map)

    mock_store.assert_called_once()
    # First positional arg to store_verdict is conn, second is verdict, then channel_id/message_id
    _conn, verdict_arg, channel_id_arg, message_id_arg = mock_store.call_args[0]
    assert verdict_arg.user_id == 42
    assert channel_id_arg == 111
    assert message_id_arg == 222
    mock_complete.assert_called_once_with(submitter._conn, "batch_recover_ok", status="complete")


async def test_recover_exception_during_results_marks_batch_failed(conn):
    """results() raises → mark_batch_complete('failed') called, store_verdict NOT called."""
    submitter, client = make_submitter(conn)

    batch_ended = MagicMock(processing_status="ended")
    client.messages.batches.retrieve = AsyncMock(return_value=batch_ended)
    client.messages.batches.results = AsyncMock(side_effect=RuntimeError("network error"))

    from ahsoka.database import save_pending_batch, get_pending_batches
    await save_pending_batch(conn, "batch_recover_fail", {"111:222:42": [111, 222, 42]})

    request_map = {"111:222:42": [111, 222, 42]}

    with patch("ahsoka.pipeline.batch_submitter.db.store_verdict", new_callable=AsyncMock) as mock_store, \
         patch("ahsoka.pipeline.batch_submitter.db.mark_batch_complete", new_callable=AsyncMock) as mock_complete:
        await submitter.recover("batch_recover_fail", request_map)

    mock_store.assert_not_called()
    mock_complete.assert_called_once_with(submitter._conn, "batch_recover_fail", status="failed")


# ---------------------------------------------------------------------------
# _sdk_result_to_dict (continued)
# ---------------------------------------------------------------------------


def test_sdk_result_to_dict_succeeded_missing_message():
    inner = MagicMock()
    inner.type = "succeeded"
    inner.message = None

    sdk_result = MagicMock()
    sdk_result.result = inner

    result = _sdk_result_to_dict(sdk_result)
    assert result["result"]["type"] == "error"
    assert "missing message" in result["result"]["error"]["message"]


def test_sdk_result_to_dict_block_without_text_attribute():
    """Content block without a .text attribute is converted to {type: <type>} dict."""
    block = MagicMock(spec=[])  # spec=[] means no attributes at all
    # Give it a type attribute but no text attribute
    type(block).type = property(lambda self: "image")

    msg = MagicMock()
    msg.content = [block]

    inner = MagicMock()
    inner.type = "succeeded"
    inner.message = msg

    sdk_result = MagicMock()
    sdk_result.result = inner

    result = _sdk_result_to_dict(sdk_result)
    assert result["result"]["type"] == "succeeded"
    content = result["result"]["message"]["content"]
    assert len(content) == 1
    assert content[0]["type"] == "image"
    assert "text" not in content[0]


# ---------------------------------------------------------------------------
# poll_and_process — retrieve exception and results fetch exception branches
# ---------------------------------------------------------------------------


async def test_poll_and_process_retries_on_retrieve_exception(conn):
    """retrieve() raises once → logs warning, sleeps, retries, succeeds on second call."""
    submitter, client = make_submitter(conn)

    batch_ended = MagicMock(processing_status="ended")
    client.messages.batches.retrieve = AsyncMock(
        side_effect=[RuntimeError("transient"), batch_ended]
    )

    custom_id = "111_222_42"
    sdk_result = _make_sdk_result(custom_id, score=7)

    async def fake_results(batch_id):
        async def _gen():
            yield sdk_result
        return _gen()

    client.messages.batches.results = fake_results

    from ahsoka.database import save_pending_batch
    await save_pending_batch(conn, "batch_retrieve_err", {custom_id: [111, 222, 42]})

    reqs = [make_request(make_post(111, 222), make_config(42))]

    with patch("ahsoka.pipeline.batch_submitter.asyncio.sleep", new_callable=AsyncMock):
        results = await submitter.poll_and_process("batch_retrieve_err", reqs)

    # Eventually completes after retry
    assert len(results) == 1
    assert results[0][2].score == 7


async def test_poll_and_process_results_fetch_exception_marks_failed(conn):
    """results() raises → logs error, marks batch failed, returns partial results."""
    submitter, client = make_submitter(conn)

    batch_ended = MagicMock(processing_status="ended")
    client.messages.batches.retrieve = AsyncMock(return_value=batch_ended)
    client.messages.batches.results = AsyncMock(side_effect=RuntimeError("network error"))

    from ahsoka.database import save_pending_batch, get_pending_batches
    custom_id = "111_222_42"
    await save_pending_batch(conn, "batch_results_err", {custom_id: [111, 222, 42]})

    reqs = [make_request(make_post(111, 222), make_config(42))]

    with patch("ahsoka.pipeline.batch_submitter.asyncio.sleep", new_callable=AsyncMock):
        results = await submitter.poll_and_process("batch_results_err", reqs)

    # Returns empty (or partial) results without raising
    assert results == []

    # Batch should be marked failed and removed from pending
    pending = await get_pending_batches(conn)
    assert not any(p["batch_id"] == "batch_results_err" for p in pending)


# ---------------------------------------------------------------------------
# recover — retrieve exception and sleep/continue branch, unknown custom_id
# ---------------------------------------------------------------------------


async def test_recover_retries_on_retrieve_exception(conn):
    """recover(): retrieve() raises → logs warning, sleeps, retries, eventually ends."""
    submitter, client = make_submitter(conn)

    batch_ended = MagicMock(processing_status="ended")
    client.messages.batches.retrieve = AsyncMock(
        side_effect=[RuntimeError("transient"), batch_ended]
    )

    custom_id = "111_222_42"
    sdk_result = _make_sdk_result(custom_id, score=8)

    async def fake_results(batch_id):
        async def _gen():
            yield sdk_result
        return _gen()

    client.messages.batches.results = fake_results

    from ahsoka.database import save_pending_batch
    await save_pending_batch(conn, "batch_rec_retry", {custom_id: [111, 222, 42]})

    request_map = {custom_id: [111, 222, 42]}

    with patch("ahsoka.pipeline.batch_submitter.asyncio.sleep", new_callable=AsyncMock), \
         patch("ahsoka.pipeline.batch_submitter.db.store_verdict", new_callable=AsyncMock) as mock_store, \
         patch("ahsoka.pipeline.batch_submitter.db.mark_batch_complete", new_callable=AsyncMock):
        await submitter.recover("batch_rec_retry", request_map)

    # After retry, verdict should have been stored
    mock_store.assert_called_once()


async def test_recover_sleeps_and_polls_again_when_processing(conn):
    """recover(): batch is processing → sleep → poll again → ended → store verdict."""
    submitter, client = make_submitter(conn)

    batch_processing = MagicMock(processing_status="processing")
    batch_ended = MagicMock(processing_status="ended")
    client.messages.batches.retrieve = AsyncMock(
        side_effect=[batch_processing, batch_ended]
    )

    custom_id = "111_222_42"
    sdk_result = _make_sdk_result(custom_id, score=6)

    async def fake_results(batch_id):
        async def _gen():
            yield sdk_result
        return _gen()

    client.messages.batches.results = fake_results

    from ahsoka.database import save_pending_batch
    await save_pending_batch(conn, "batch_rec_sleep", {custom_id: [111, 222, 42]})

    request_map = {custom_id: [111, 222, 42]}

    with patch("ahsoka.pipeline.batch_submitter.asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
         patch("ahsoka.pipeline.batch_submitter.db.store_verdict", new_callable=AsyncMock) as mock_store, \
         patch("ahsoka.pipeline.batch_submitter.db.mark_batch_complete", new_callable=AsyncMock):
        await submitter.recover("batch_rec_sleep", request_map)

    # asyncio.sleep should have been called at least once (for the processing state)
    assert mock_sleep.call_count >= 1
    mock_store.assert_called_once()


async def test_recover_skips_unknown_custom_id_in_results(conn):
    """recover(): custom_id in results not in request_map → logs warning, skips."""
    submitter, client = make_submitter(conn)

    batch_ended = MagicMock(processing_status="ended")
    client.messages.batches.retrieve = AsyncMock(return_value=batch_ended)

    # Result has a custom_id not in request_map
    unknown_result = _make_sdk_result("999_999_999", score=5)

    async def fake_results(batch_id):
        async def _gen():
            yield unknown_result
        return _gen()

    client.messages.batches.results = fake_results

    from ahsoka.database import save_pending_batch
    known_id = "111_222_42"
    await save_pending_batch(conn, "batch_rec_unknown", {known_id: [111, 222, 42]})

    request_map = {known_id: [111, 222, 42]}

    with patch("ahsoka.pipeline.batch_submitter.db.store_verdict", new_callable=AsyncMock) as mock_store, \
         patch("ahsoka.pipeline.batch_submitter.db.mark_batch_complete", new_callable=AsyncMock), \
         patch("ahsoka.pipeline.batch_submitter.logger") as mock_logger:
        await submitter.recover("batch_rec_unknown", request_map)

    # Unknown custom_id skipped — store_verdict not called for it
    mock_store.assert_not_called()
    # Warning logged for the unknown custom_id
    assert mock_logger.warning.called


# ---------------------------------------------------------------------------
# Token usage accumulation
# ---------------------------------------------------------------------------


def _make_sdk_result_with_usage(
    custom_id: str,
    score: int = 7,
    input_tokens: int = 100,
    output_tokens: int = 20,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> MagicMock:
    """Like _make_sdk_result but adds a usage object to the message."""
    result = _make_sdk_result(custom_id, score)
    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens
    usage.cache_creation_input_tokens = cache_creation_input_tokens
    usage.cache_read_input_tokens = cache_read_input_tokens
    result.result.message.usage = usage
    return result


async def test_poll_and_process_saves_token_usage(conn):
    """poll_and_process aggregates usage tokens across results and calls save_batch_usage."""
    submitter, client = make_submitter(conn, model="claude-haiku-4-5-20251001")

    batch_ended = MagicMock(processing_status="ended")
    client.messages.batches.retrieve = AsyncMock(return_value=batch_ended)

    r1 = _make_sdk_result_with_usage("111_222_42", input_tokens=500, output_tokens=100)
    r2 = _make_sdk_result_with_usage("111_222_43", input_tokens=300, output_tokens=50,
                                     cache_read_input_tokens=200)

    async def fake_results(batch_id):
        async def _gen():
            yield r1
            yield r2
        return _gen()

    client.messages.batches.results = fake_results

    from ahsoka.database import save_pending_batch, get_total_usage
    await save_pending_batch(conn, "batch_usage_test", {
        "111_222_42": [111, 222, 42],
        "111_222_43": [111, 222, 43],
    })

    req1 = make_request(make_post(111, 222), make_config(42))
    req2 = make_request(make_post(111, 222), make_config(43))

    await submitter.poll_and_process("batch_usage_test", [req1, req2])

    usage = await get_total_usage(conn)
    assert "claude-haiku-4-5-20251001" in usage
    u = usage["claude-haiku-4-5-20251001"]
    assert u["input_tokens"] == 800
    assert u["output_tokens"] == 150
    assert u["cache_read_input_tokens"] == 200
    assert u["succeeded"] == 2
    assert u["batches"] == 1


async def test_recover_saves_token_usage(conn):
    """recover() accumulates tokens from results and calls save_batch_usage."""
    submitter, client = make_submitter(conn, model="claude-haiku-4-5-20251001")

    batch_ended = MagicMock(processing_status="ended")
    client.messages.batches.retrieve = AsyncMock(return_value=batch_ended)

    custom_id = "111_222_42"
    sdk_result = _make_sdk_result_with_usage(custom_id, input_tokens=400, output_tokens=80)

    async def fake_results(batch_id):
        async def _gen():
            yield sdk_result
        return _gen()

    client.messages.batches.results = fake_results

    from ahsoka.database import save_pending_batch, get_total_usage
    await save_pending_batch(conn, "batch_rec_usage", {custom_id: [111, 222, 42]})

    request_map = {custom_id: [111, 222, 42]}

    with patch("ahsoka.pipeline.batch_submitter.db.store_verdict", new_callable=AsyncMock):
        await submitter.recover("batch_rec_usage", request_map)

    usage = await get_total_usage(conn)
    assert "claude-haiku-4-5-20251001" in usage
    u = usage["claude-haiku-4-5-20251001"]
    assert u["input_tokens"] == 400
    assert u["output_tokens"] == 80
    assert u["succeeded"] == 1
