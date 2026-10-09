"""Build evaluation dataset v2 (flow 3 damage) from Humans in the Loop's car damage set.

    python scripts/build_damage_dataset.py ~/Downloads/archive.zip --out /tmp/eval-v2 [--upload]

Reads the archive directly; nothing in it is extracted or run. In the archive the two folders are
named the other way round: "Car parts dataset" holds the damage labels and "Car damages dataset"
the part labels. 441 photos are in both (same bytes), so each labelled damage can be placed on a
part and the part on a gate zone. Only those photos are used.

Every photo in the set is of a damaged car, so clean cases are crops of parts that no damage
touches, from the same photos. Damaged cases are balanced across kinds and lean towards the
smaller damage a finishing line sees; the set is mostly crash damage, which is far easier.

Output: JPEGs and dataset JSON under --out; with --upload, the JPEGs go to eval/v2/damage/ in
the artifacts bucket and the JSON (pointing at them) to eval/datasets/v2-damage.json.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import random
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from marshal_ai.eval.damage import DAMAGE_KIND, PART_ZONES  # noqa: E402

DAMAGE_DIR = "Car parts dataset/File1"       # sic: see the module docstring
PARTS_DIR = "Car damages dataset/File1"
MAX_SIDE = 1280
SCALE = 4                                    # rasterise at 1/4 size to measure overlaps
# Damaged cases per kind; half of each from the smallest damage.
PER_KIND = {"scratch": 8, "dent": 7, "paint": 5, "missing_part": 5, "broken_part": 5}
CLEAN_CROPS = 16
CLEAN_PARTS = ("Front-door", "Back-door", "Hood", "Trunk", "Front-bumper", "Back-bumper")
# Crops checked by eye and rejected as clean cases, though no labelled damage touches them.
NOT_CLEAN = {
    "Car damages 1287.png": "rear bumper has holes, possibly missing parking sensors",
    "Car damages 1280.jpg": "front bumper looks deformed at its edge",
    "Car damages 154.png": "headlights badly clouded, which a gate could fairly call a defect",
    "Car damages 296.png": "unlabelled scratch across the tailgate",
}


def _mask(size, polygons) -> Image.Image:
    w, h = size
    m = Image.new("1", (max(1, w // SCALE), max(1, h // SCALE)))
    draw = ImageDraw.Draw(m)
    for poly in polygons:
        if len(poly) >= 3:
            draw.polygon([(x / SCALE, y / SCALE) for x, y in poly], fill=1)
    return m


def _count(m: Image.Image) -> int:
    return m.histogram()[-1]


def _and(a: Image.Image, b: Image.Image) -> Image.Image:
    return ImageChops.logical_and(a, b)


def _objects(z: zipfile.ZipFile, folder: str, name: str) -> tuple[tuple[int, int], list[tuple[str, list]]]:
    ann = json.loads(z.read(f"{folder}/ann/{name}.json"))
    size = (ann["size"]["width"], ann["size"]["height"])
    return size, [(o["classTitle"], o["points"]["exterior"]) for o in ann["objects"]
                  if o.get("geometryType", "polygon") == "polygon"]


def _jpeg(img: Image.Image) -> bytes:
    img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def analyse(z: zipfile.ZipFile, name: str) -> dict:
    size, damages = _objects(z, DAMAGE_DIR, name)
    _, parts = _objects(z, PARTS_DIR, name)
    part_masks = defaultdict(list)
    for title, poly in parts:
        part_masks[title].append(poly)
    part_masks = {t: _mask(size, polys) for t, polys in part_masks.items()}
    area = size[0] * size[1] / SCALE ** 2
    found, damage_union = [], _mask(size, [])
    for title, poly in damages:
        dm = _mask(size, [poly])
        damage_union = ImageChops.logical_or(damage_union, dm)
        overlaps = {t: _count(_and(dm, pm)) for t, pm in part_masks.items()}
        part = max(overlaps, key=overlaps.get) if overlaps and max(overlaps.values()) > 0 else None
        found.append({"class": title, "kind": DAMAGE_KIND.get(title), "part": part,
                      "area": _count(dm) / area})
    return {"name": name, "size": size, "damages": found, "parts": parts,
            "part_masks": part_masks, "damage_union": damage_union,
            "damage_area": _count(damage_union) / area}


def pick_damaged(infos: list[dict], rng: random.Random) -> list[dict]:
    by_kind = defaultdict(list)
    for info in infos:
        kinds = defaultdict(float)
        for d in info["damages"]:
            if d["kind"]:
                kinds[d["kind"]] += d["area"]
        if kinds:
            info["main_kind"] = max(kinds, key=kinds.get)
            by_kind[info["main_kind"]].append(info)
    chosen = []
    for kind, n in PER_KIND.items():
        pool = sorted(by_kind[kind], key=lambda i: i["damage_area"])
        small, rest = pool[: len(pool) // 2], pool[len(pool) // 2:]
        chosen += rng.sample(small, min(len(small), n - n // 2)) + rng.sample(rest, min(len(rest), n // 2))
    return chosen


def clean_crops(infos: list[dict], rng: random.Random) -> list[tuple[dict, str, tuple]]:
    """Parts no damage touches, cropped with a margin that itself holds no damage."""
    candidates = []
    for info in infos:
        for title, poly in info["parts"]:
            if title not in CLEAN_PARTS:
                continue
            xs, ys = [p[0] for p in poly], [p[1] for p in poly]
            w, h = max(xs) - min(xs), max(ys) - min(ys)
            if w < 220 or h < 160:
                continue
            pad = 0.08
            box = (max(0, min(xs) - pad * w), max(0, min(ys) - pad * h),
                   min(info["size"][0], max(xs) + pad * w), min(info["size"][1], max(ys) + pad * h))
            region = Image.new("1", info["damage_union"].size)
            ImageDraw.Draw(region).rectangle([v / SCALE for v in box], fill=1)
            if _count(_and(region, info["damage_union"])) == 0:
                candidates.append((info, title, box))
    rng.shuffle(candidates)
    picked, used = [], set()
    for info, title, box in candidates:          # one crop per photo, spread across parts
        # Skipped after the shuffle, so rejecting one crop does not change the others.
        if info["name"] in used or info["name"] in NOT_CLEAN:
            continue
        picked.append((info, title, box))
        used.add(info["name"])
        if len(picked) == CLEAN_CROPS:
            break
    return picked


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("archive", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--bucket", default=os.getenv("MARSHAL_BUCKET")
                    or f"marshal-artifacts-{os.getenv('GOOGLE_CLOUD_PROJECT', '')}")
    args = ap.parse_args(argv)
    rng = random.Random(args.seed)

    z = zipfile.ZipFile(args.archive)
    names = lambda d: {n[len(f"{d}/ann/"):-len(".json")] for n in z.namelist()
                       if n.startswith(f"{d}/ann/") and n.endswith(".json")}
    shared = sorted(names(DAMAGE_DIR) & names(PARTS_DIR))
    infos = [analyse(z, n) for n in shared]
    print(f"{len(shared)} photos with both damage and part labels", file=sys.stderr)

    images = args.out / "images"
    images.mkdir(parents=True, exist_ok=True)
    prefix = f"gs://{args.bucket}/eval/v2/damage" if args.upload else "images"
    cases = []
    for info in pick_damaged(infos, rng):
        stem = Path(info["name"]).stem.replace(" ", "-").lower()
        (images / f"{stem}.jpg").write_bytes(
            _jpeg(Image.open(io.BytesIO(z.read(f"{DAMAGE_DIR}/img/{info['name']}")))))
        kinds = sorted({d["kind"] for d in info["damages"] if d["kind"]})
        zones = sorted({zone for d in info["damages"] for zone in PART_ZONES.get(d["part"], [])})
        cases.append({"id": stem, "image": f"{prefix}/{stem}.jpg", "expect_damaged": True,
                      "expect_kinds": kinds, "expect_zones": zones,
                      "note": f"{info['main_kind']}; damage covers {info['damage_area']:.1%} of the photo; "
                              + ", ".join(sorted({f"{d['class']} on {d['part'] or 'no part'}" for d in info["damages"]}))})
    for info, title, box in clean_crops(infos, rng):
        stem = f"clean-{title.lower()}-{Path(info['name']).stem.replace(' ', '-').lower()}"
        crop = Image.open(io.BytesIO(z.read(f"{DAMAGE_DIR}/img/{info['name']}"))).crop(box)
        (images / f"{stem}.jpg").write_bytes(_jpeg(crop))
        cases.append({"id": stem, "image": f"{prefix}/{stem}.jpg", "expect_damaged": False,
                      "note": f"{title} with no labelled damage, cropped from {info['name']}"})

    dataset = {
        "name": "v2-damage",
        "description": ("Flow 3 on real photos of damaged cars: Humans in the Loop, 'Car parts and car "
                        "damages' (CC0 1.0), the 441 photos labelled with both parts and damage. Damaged "
                        "cases balanced across kinds, half from the smallest damage; clean cases are crops "
                        f"of undamaged parts. Built by scripts/build_damage_dataset.py, seed {args.seed}."),
        "damage": cases,
    }
    (args.out / "v2-damage.json").write_text(json.dumps(dataset, indent=2))
    print(f"{sum(c['expect_damaged'] for c in cases)} damaged, "
          f"{sum(not c['expect_damaged'] for c in cases)} clean -> {args.out}", file=sys.stderr)

    if args.upload:
        from google.cloud import storage
        bucket = storage.Client().bucket(args.bucket)
        for f in sorted(images.glob("*.jpg")):
            bucket.blob(f"eval/v2/damage/{f.name}").upload_from_filename(str(f), content_type="image/jpeg")
        print(f"uploaded {len(list(images.glob('*.jpg')))} images to gs://{args.bucket}/eval/v2/damage/",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
