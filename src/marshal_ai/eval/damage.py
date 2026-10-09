"""Damage photos for flow 3: the public set's labels, in the gate's terms.

The design names a public car-damage image set as the stand-in for scratches and dents
("What is real and what is stand-in"). Dataset v2 uses Humans in the Loop's "Car parts and car
damages" (CC0). Its labels are translated here: damage classes into the defect kinds the gate
reports, and car parts into the gate's body zones.
"""

from __future__ import annotations

# The set's damage classes, grouped into the kinds a finishing-line gate cares about.
DAMAGE_KIND = {
    "Scratch": "scratch",
    "Dent": "dent",
    "Paint chip": "paint",
    "Flaking": "paint",
    "Corrosion": "paint",
    "Missing part": "missing_part",
    "Broken part": "broken_part",
    "Cracked": "broken_part",
}

# How a finding's free-text type (the model writes "scratch", "dent", "panel_gap", ...) reads
# as one of those kinds. A finding can match more than one: "paint scratch" is both.
KIND_WORDS = {
    "scratch": ("scratch", "scuff", "scrape", "abrasion"),
    "dent": ("dent", "deform", "crumpl", "crush", "buckl", "ding"),
    "paint": ("paint", "chip", "flak", "peel", "corros", "rust", "clear coat", "clearcoat"),
    "missing_part": ("missing", "absent"),
    "broken_part": ("broken", "crack", "shatter", "detach", "hanging", "torn", "damage", "smash"),
}

# The set's car parts that fall in one of the gate's eight zones. Part labels do not say left or
# right, so a door accepts either side. Parts outside every zone (fender, quarter panel, wheels,
# windows, roof) score detection and kind but not zone.
PART_ZONES = {
    "Hood": ["bonnet"],
    "Front-bumper": ["front"],
    "Grille": ["front"],
    "Headlight": ["front"],
    "Back-bumper": ["rear"],
    "Tail-light": ["rear"],
    "Trunk": ["boot"],
    "Front-door": ["front_left_door", "front_right_door"],
    "Back-door": ["rear_left_door", "rear_right_door"],
}


def kinds_of(finding_type: str) -> set[str]:
    text = finding_type.lower().replace("_", " ")
    return {kind for kind, words in KIND_WORDS.items() if any(w in text for w in words)}
