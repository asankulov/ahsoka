import asyncio
import json
import logging

from anthropic import AsyncAnthropic, RateLimitError

from ahsoka.models import Post, Score

logger = logging.getLogger(__name__)

_semaphore = asyncio.Semaphore(5)

_VALID_SENIORITY = {"junior", "mid", "senior", "lead", "any"}
_VALID_REMOTE = {"remote", "onsite", "hybrid", "unknown"}

_PROMPT = """\
You are a job posting analyzer. Extract structured fields from the following job posting.

Return ONLY valid JSON with these exact keys:
{{
  "score": <int 0-10>,
  "reason": "<one sentence>",
  "apply": "<email, Telegram handle, apply link, or empty string>",
  "stack": [<lowercase tech/tool/language tags>],
  "seniority": "<junior | mid | senior | lead | any>",
  "remote": "<remote | onsite | hybrid | unknown>",
  "red_flags": [<short strings, e.g. "vague compensation">]
}}

Scoring rubric:
- 9-10: Clear role, named company, specific stack, salary range, easy apply
- 7-8: Good detail but missing salary or vague on one dimension
- 5-6: Passable but multiple unclear aspects
- 3-4: Vague role or recruiter spam with no specifics
- 0-2: Not a job posting, or severe red flags

Rules:
- "stack": ALL technologies, languages, frameworks, tools mentioned. Lowercase, no duplicates.
- "seniority": "senior" for 3+ years; "lead" for lead/principal/staff; "junior" for entry-level/intern; "mid" for mid-level/2+ years; "any" if unclear.
- "remote": use what the posting says. Office without remote option = "onsite". If unclear = "unknown".
- "red_flags": vague compensation, no company name, unpaid trial, unrealistic requirements, MLM/scam signals. Empty list if none.

Job posting:
{content}"""


def _parse_score(data: dict) -> Score:
    stack_raw = data.get("stack", [])
    if isinstance(stack_raw, str):
        stack_raw = [stack_raw]
    stack = [s.lower().strip() for s in stack_raw if isinstance(s, str) and s.strip()]

    seniority = data.get("seniority", "any")
    if seniority not in _VALID_SENIORITY:
        seniority = "any"

    remote = data.get("remote", "unknown")
    if remote not in _VALID_REMOTE:
        remote = "unknown"

    flags_raw = data.get("red_flags", [])
    if isinstance(flags_raw, str):
        flags_raw = [flags_raw]
    red_flags = [f for f in flags_raw if isinstance(f, str) and f.strip()]

    return Score(
        score=int(data["score"]),
        reason=data.get("reason", ""),
        apply=data.get("apply", ""),
        stack=stack,
        seniority=seniority,
        remote=remote,
        red_flags=red_flags,
    )


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
                    max_tokens=512,
                    messages=[
                        {"role": "user", "content": prompt},
                        {"role": "assistant", "content": "{"},
                    ],
                )
                raw = "{" + response.content[0].text.strip()
                data = json.loads(raw)
                return _parse_score(data)
            except RateLimitError:
                wait = 2**attempt
                logger.warning("Rate limited on attempt %d, retrying in %ds", attempt + 1, wait)
                await asyncio.sleep(wait)
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                logger.warning("Bad scorer response (%s): %r", exc, raw)
                return Score(score=0, reason="parse error", apply="")

    return Score(score=0, reason="rate limit exhausted", apply="")
