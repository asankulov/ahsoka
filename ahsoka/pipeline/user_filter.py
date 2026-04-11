from ahsoka.models import PersonalizedVerdict, UserConfig


def matches_user(verdict: PersonalizedVerdict, config: UserConfig) -> bool:
    """Thin fan-out validator: paused check + threshold + model's matched flag.

    Stack/seniority/remote/location/salary reasoning has moved into the
    personalized scoring prompt (scorer.py). This function is the single
    authoritative fan-out decision point.
    """
    if config.paused:
        return False
    if verdict.score < config.threshold:
        return False
    if not verdict.matched:
        return False
    return True
