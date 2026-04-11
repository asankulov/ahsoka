import json
import logging

from ahsoka.models import PersonalizedVerdict, Post, UserConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Personalized prompt builder
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a job posting evaluator. You receive a job posting and a candidate profile.
Score the posting for this specific candidate and determine if it matches their requirements.

Return ONLY valid JSON with these exact keys:
{
  "score": <int 0-10>,
  "reason": "<one sentence explaining the score for this candidate>",
  "matched": <true | false>,
  "apply": "<email, Telegram handle, apply link, or empty string>",
  "red_flags": [<short strings, e.g. "vague compensation">],
  "stack": [<technology names explicitly mentioned in the posting, e.g. "Python", "React">],
  "seniority": "<level stated or implied by the posting itself — one of: junior/middle/senior/lead/any>",
  "remote": "<work arrangement stated in the posting — one of: remote/hybrid/onsite/unknown>"
}

Important: "stack", "seniority", and "remote" are OBJECTIVE FACTS extracted from the posting text.
They describe what the posting says — not what the candidate wants.
- "stack": only list technologies explicitly mentioned; empty list if none.
- "seniority": use "any" if the posting does not specify a seniority level.
- "remote": use "unknown" if the posting does not specify a work arrangement.

Scoring rubric (generic posting quality):
- 9-10: Clear role, named company, specific stack, salary range, easy apply
- 7-8: Good detail but missing salary or vague on one dimension
- 5-6: Passable but multiple unclear aspects
- 3-4: Vague role or recruiter spam with no specifics
- 0-2: Not a job posting, or severe red flags

"matched": set to true if AND ONLY IF all of the following hold:
1. score >= threshold (candidate's minimum quality bar)
2. stack overlap: if the candidate listed technologies, at least one appears in the posting
3. seniority: if the candidate specified a level, the posting targets that level (or is unspecified)
4. remote: if the candidate has a remote preference, the posting matches it (or is unspecified)
5. location: if the candidate specified a location, the posting is in that location or is remote (or unspecified)
6. salary: if the candidate specified salary_min or salary_max, the posting's compensation is within that range (or unspecified/undisclosed — give benefit of the doubt)

Rules:
- "red_flags": vague compensation, no company name, unpaid trial, unrealistic requirements, MLM/scam signals. Empty list if none.
- When a candidate field is empty/unspecified, treat that dimension as "no constraint".\
"""

_USER_TEMPLATE = """\
=== CANDIDATE PROFILE ===
Stack: {stack}
Seniority: {seniority}
Remote preference: {remote}
Location: {location}
Salary range: {salary_min} – {salary_max}
Keywords: {keywords}
Minimum score threshold: {threshold}

=== JOB POSTING ===
{content}\
"""


def build_personalized_prompt(
    post: Post,
    content: str,
    config: UserConfig,
) -> dict:
    """Return an Anthropic Messages API request dict for use in a Batch API request.

    Shape:
        {
            "custom_id": "<channel_id>:<message_id>:<user_id>",
            "params": {
                "model": str,
                "max_tokens": int,
                "system": str,
                "messages": [...],
            }
        }

    The caller (BatchSubmitter) fills in the model from settings and
    wraps this into the batches.create(requests=[...]) payload.
    """
    user_message = _USER_TEMPLATE.format(
        stack=config.stack or "any",
        seniority=config.seniority or "any",
        remote=config.remote or "any",
        location=config.location or "any",
        salary_min=config.salary_min or "unspecified",
        salary_max=config.salary_max or "unspecified",
        keywords=config.keywords or "none",
        threshold=config.threshold,
        content=content[:4000],
    )
    return {
        "custom_id": f"{post.channel_id}:{post.message_id}:{config.user_id}",
        "params": {
            "max_tokens": 512,
            "system": _SYSTEM_PROMPT,
            "messages": [
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": "{"},
            ],
        },
    }


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------


def parse_verdict(response_json: dict, user_id: int) -> PersonalizedVerdict:
    """Parse a single Anthropic batch result into a PersonalizedVerdict.

    Accepts the full per-request result object from batches.results():
        {
            "custom_id": "...",
            "result": {
                "type": "succeeded",
                "message": { "content": [{"type": "text", "text": "..."}] }
            }
        }
    Returns safe defaults on any parse failure.
    """
    raw = ""
    try:
        result = response_json.get("result", {})
        result_type = result.get("type")
        if result_type != "succeeded":
            error = result.get("error") or {}
            reason = error.get("message", result_type or "unknown error")
            logger.warning(
                "Batch request for user %d did not succeed: %s", user_id, reason
            )
            return PersonalizedVerdict(
                user_id=user_id,
                score=0,
                reason=f"batch error: {reason}",
                matched=False,
            )

        message = result.get("message", {})
        content_blocks = message.get("content", [])
        text = ""
        for block in content_blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block["text"].strip()
                break
            elif hasattr(block, "text"):
                # SDK object
                text = block.text.strip()
                break

        # The prompt primes the assistant with "{" so the model continues from there
        raw = "{" + text if not text.startswith("{") else text
        data = json.loads(raw)

        flags_raw = data.get("red_flags", [])
        if isinstance(flags_raw, str):
            flags_raw = [flags_raw]
        red_flags = [f for f in flags_raw if isinstance(f, str) and f.strip()]

        stack_raw = data.get("stack", [])
        if isinstance(stack_raw, str):
            stack_raw = [stack_raw]
        stack = [s for s in stack_raw if isinstance(s, str) and s.strip()]

        _valid_seniority = {"junior", "middle", "senior", "lead", "any"}
        seniority_raw = data.get("seniority", "any")
        seniority = seniority_raw if seniority_raw in _valid_seniority else "any"

        _valid_remote = {"remote", "hybrid", "onsite", "unknown"}
        remote_raw = data.get("remote", "unknown")
        remote = remote_raw if remote_raw in _valid_remote else "unknown"

        return PersonalizedVerdict(
            user_id=user_id,
            score=int(data["score"]),
            reason=data.get("reason", ""),
            matched=bool(data.get("matched", False)),
            apply=data.get("apply", ""),
            red_flags=red_flags,
            stack=stack,
            seniority=seniority,
            remote=remote,
        )
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        logger.warning("parse_verdict failed for user %d (%s): %r", user_id, exc, raw)
        return PersonalizedVerdict(
            user_id=user_id,
            score=0,
            reason="parse error",
            matched=False,
        )
