from ahsoka.models import PersonalizedVerdict, UserConfig


def matches_user(verdict: PersonalizedVerdict, config: UserConfig) -> bool:
    """Thin fan-out validator: ban check + paused check + threshold + model's matched flag.

    Stack/seniority/remote/location/salary reasoning has moved into the
    personalized scoring prompt (scorer.py). This function is the single
    authoritative fan-out decision point.
    """
    # Snapshot defence: get_all_active_configs filters WHERE is_banned=0 at enqueue time,
    # but recovery paths may replay stale snapshots where is_banned is still False.
    if config.is_banned:
        return False
    if config.paused:
        return False
    if verdict.score < config.threshold:
        return False
    if not verdict.matched:
        return False
    return True
