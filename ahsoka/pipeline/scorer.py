import asyncio
import json
import logging

from anthropic import AsyncAnthropic, RateLimitError

from ahsoka.models import Post, Score

logger = logging.getLogger(__name__)

_semaphore = asyncio.Semaphore(5)

_PROMPT = """\
You are a job posting analyzer. Score the following job posting's overall \
quality and relevance for a software developer on a scale of 0 to 10. \
Also extract any contact or application info (email, Telegram handle, \
apply link, "DM @x", etc.) into the `apply` field — leave it an empty \
string if none found.

Return ONLY valid JSON:
{{"score": <int>, "reason": "<one sentence>", "apply": "<contact/apply info or empty string>"}}

Job posting:
{content}"""


async def score_post(
    client: AsyncAnthropic,
    post: Post,
    content: str,
    model: str,
) -> Score:
    prompt = _PROMPT.format(content=content[:4000])

    raw = ""
    async with _semaphore:
        logger.debug(
            "Scoring %s/%s — content (%d chars):\n%s",
            post.channel_id, post.message_id, len(content), content[:4000],
        )
        for attempt in range(4):
            try:
                response = await client.messages.create(
                    model=model,
                    max_tokens=256,
                    messages=[
                        {"role": "user", "content": prompt},
                        {"role": "assistant", "content": "{"},
                    ],
                )
                raw = "{" + response.content[0].text.strip()
                data = json.loads(raw)
                return Score(
                    score=int(data["score"]),
                    reason=data.get("reason", ""),
                    apply=data.get("apply", ""),
                )
            except RateLimitError:
                wait = 2**attempt
                logger.warning("Rate limited on attempt %d, retrying in %ds", attempt + 1, wait)
                await asyncio.sleep(wait)
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                logger.warning("Bad scorer response (%s): %r", exc, raw)
                return Score(score=0, reason="parse error", apply="")

    return Score(score=0, reason="rate limit exhausted", apply="")
