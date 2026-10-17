"""Project plant state into the seven dashboard flows."""

STAGE_BY_STATE = {
    "line_end": 1,
    "inspection_gate": 3,
    "review": 3,
    "test_track": 4,
    "report": 5,
    "rework": 5,
    "yard": 5,
    "shipped": 5,
}


def stage_for(state: str, has_open_case: bool = False) -> int:
    return 2 if has_open_case else STAGE_BY_STATE.get(state, 1)
