#!/usr/bin/env python3
"""Build one page showing the evidence behind each verdict.

Everything this project produces on a real server has so far only been visible as JSON in a log over
SSH. The frames are real and in Cloud Storage, and the verdicts are real and in Firestore, but nobody
had actually looked at them together. This pairs them: each photo beside what was decided from it.

    python3 scripts/build_evidence_page.py            # reads the live bucket and Firestore
    open marshal-evidence/index.html

It needs GOOGLE_APPLICATION_CREDENTIALS and GOOGLE_CLOUD_PROJECT, and nothing else - no CARLA, no
instance running. Run it after a demo to have something to show.
"""

from __future__ import annotations

import argparse
import html
import os
from collections import defaultdict
from pathlib import Path

BUCKET = "marshal-artifacts-id-rd-ca-hanif-ali"
PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "id-rd-ca-hanif-ali")

# What each group of frames was for, in plain words.
GROUPS = {
    "gate": ("Flow 3, drive-through visual inspection",
             "Four views of the car at the inspection gate. Gemini checks every body zone for "
             "defects and compares what it sees with the build sheet."),
    "track": ("Flow 4, functional check on the test track",
              "One frame per checklist step, from the camera that can see that lamp. A step passes "
              "only when the car's telemetry and the camera agree."),
    "assist": ("Flow 2, remote assistance",
               "The forward view from the car when it stopped and asked for help."),
}


def fetch(out: Path) -> list[tuple[str, str, int]]:
    """Download every artifact; returns (group, local name, bytes)."""
    from google.cloud import storage

    client = storage.Client(project=PROJECT)
    bucket = client.bucket(BUCKET)
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for blob in client.list_blobs(bucket):
        local = blob.name.replace("/", "__")
        blob.download_to_filename(out / local)
        files.append((blob.name.split("/")[0], local, blob.size or 0))
    return files


def records() -> dict:
    """The verdicts, so a frame can be shown beside what was decided from it."""
    from google.cloud import firestore

    db = firestore.Client(project=PROJECT)
    return {coll: {d.id: d.to_dict() for d in db.collection(coll).stream()}
            for coll in ("cars", "inspection_results", "car_reports")}


def caught_block(store: dict) -> str:
    """What the camera judge contributed that telemetry and rules could not.

    This is the part worth showing. A plant running on rules and the car's own electronics has one
    source: what the car reports about itself. Every row here is a case where that source said the
    car was fine, or said nothing at all, and a second source disagreed or settled it.
    """
    rows = []
    for key, result in sorted(store["inspection_results"].items()):
        vin = result.get("vin", key.split(":")[0])
        for item in result.get("checklist") or []:
            if item.get("telemetry_ok") and item.get("camera_ok") is False:
                rows.append((vin, f"{item['name']}: the car reported this working",
                             item.get("observation") or "the camera disagreed",
                             "would have shipped the fault", item.get("clip_uri")))
        for finding in result.get("findings") or []:
            rows.append((vin, f"{finding['zone']}: nothing in the car's own data",
                         finding.get("observation") or f"{finding['type']}"
                         f" ({finding.get('confidence')})",
                         "no sensor reports paint or body damage", finding.get("image")))
        for mismatch in result.get("build_sheet_mismatches") or []:
            rows.append((vin, "build sheet says otherwise", mismatch,
                         "no sensor compares a car with its paperwork", None))

    if not rows:
        return ("<p>Nothing caught in the runs currently in Firestore &mdash; the cars in it passed. "
                "Run the dark-lamp scene (<code>scripts/run_carla_day4.sh</code>) to populate this.</p>")

    body = "".join(f"""
        <tr>
          <td><strong>{html.escape(vin)}</strong></td>
          <td>{html.escape(one_source)}</td>
          <td>{html.escape(str(saw))}</td>
          <td><small>{html.escape(why)}</small></td>
        </tr>""" for vin, one_source, saw, why, _uri in rows)
    return f"""
    <table>
      <tr><th>VIN</th><th>What the car's own data said</th><th>What the camera judge saw</th>
          <th>Why one source is not enough</th></tr>
      {body}
    </table>"""


def verdict_block(store: dict) -> str:
    rows = []
    for vin, car in sorted(store["cars"].items()):
        visual = store["inspection_results"].get(f"{vin}:visual", {})
        functional = store["inspection_results"].get(f"{vin}:functional", {})
        report = store["car_reports"].get(vin, {})
        checks = []
        for item in functional.get("checklist") or []:
            camera = item.get("camera_ok")
            seen = {True: "camera confirmed", False: "camera disagreed",
                    None: "no camera evidence"}[camera]
            said = item.get("observation")
            words = f'<br><em>&ldquo;{html.escape(said)}&rdquo;</em>' if said else ""
            checks.append(f"{html.escape(item['name'])} &mdash; {seen}{words}")
        rows.append(f"""
        <tr>
          <td><strong>{html.escape(vin)}</strong></td>
          <td>{html.escape(str(car.get('state', '')))}<br><small>{html.escape(str(car.get('location', '')))}</small></td>
          <td>{html.escape(str(visual.get('verdict', '-')))}
              {''.join(f"<br><small>{html.escape(o)}</small>" for o in visual.get('observations') or [])}</td>
          <td>{html.escape(str(functional.get('verdict', '-')))}<br>
              <small>{'<br>'.join(checks)}</small></td>
          <td>{html.escape(str(report.get('verdict', '-')))}<br>
              <small>{html.escape(str(report.get('summary', '')))}</small></td>
        </tr>""")
    if not rows:
        return "<p>No records in Firestore right now.</p>"
    return f"""
    <table>
      <tr><th>VIN</th><th>Where it is</th><th>Visual</th><th>Functional</th><th>Report</th></tr>
      {''.join(rows)}
    </table>"""


def page(files: list[tuple[str, str, int]], store: dict) -> str:
    by_group: dict[str, list] = defaultdict(list)
    for group, local, size in sorted(files, key=lambda f: f[1]):
        by_group[group].append((local, size))

    sections = []
    for group, items in sorted(by_group.items()):
        title, blurb = GROUPS.get(group, (group, ""))
        cards = "".join(
            f"""
        <figure>
          <img src="{html.escape(local)}" loading="lazy" alt="{html.escape(local)}">
          <figcaption>{html.escape(local.replace('__', ' / '))}<br>
            <small>{size / 1000:.0f} kB</small></figcaption>
        </figure>"""
            for local, size in items)
        sections.append(f"""
      <section>
        <h2>{html.escape(title)}</h2>
        <p class="blurb">{html.escape(blurb)}</p>
        <div class="grid">{cards}</div>
      </section>""")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Marshal &mdash; evidence behind the verdicts</title>
  <style>
    body {{ font: 15px/1.5 -apple-system, system-ui, sans-serif; margin: 0 auto; max-width: 1100px;
            padding: 24px; color: #111; }}
    h1 {{ font-size: 22px; }}
    h2 {{ font-size: 17px; margin-top: 32px; }}
    .blurb {{ color: #444; max-width: 70ch; }}
    .grid {{ display: grid; gap: 14px; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); }}
    figure {{ margin: 0; }}
    img {{ width: 100%; border-radius: 6px; background: #000; display: block; }}
    figcaption {{ font-size: 12px; color: #555; padding-top: 4px; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 10px; }}
    th, td {{ text-align: left; vertical-align: top; padding: 7px 9px; border-bottom: 1px solid #ddd;
             font-size: 13px; }}
    th {{ background: #f6f6f6; }}
    small {{ color: #666; }}
  </style>
</head>
<body>
  <h1>Marshal &mdash; the evidence behind each verdict</h1>
  <p class="blurb">Every image here was taken by a camera in a real CARLA 0.9.16 server, and every
  verdict beside it was made by Gemini through Vertex AI. Images come from Cloud Storage and the
  verdicts from Firestore, so this page shows what the system actually decided and what it saw when
  it decided that.</p>

  <section>
    <h2>What the second source caught</h2>
    <p class="blurb">A plant running on rules and the car's own electronics has one source: what the
    car reports about itself. Each row below is a case where that source said the car was fine, or
    had nothing to say, and looking at the car said otherwise.</p>
    {caught_block(store)}
  </section>

  <section>
    <h2>Cars and verdicts, now</h2>
    {verdict_block(store)}
  </section>
  {''.join(sections)}
  <p style="margin-top:36px"><small>Rebuild with
  <code>python3 scripts/build_evidence_page.py</code>.</small></p>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).resolve().parents[2] / "marshal-evidence")
    args = ap.parse_args()

    files = fetch(args.out)
    store = records()
    index = args.out / "index.html"
    index.write_text(page(files, store))
    print(f"{len(files)} frames and {len(store['cars'])} cars -> {index}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
