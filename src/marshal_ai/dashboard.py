"""Marshal dashboard: one page showing the plant camera next to what the agents decided.

The CARLA viewer (`marshal_ai.carla_viewer`) only shows pixels, so flows 5 and 6 are
invisible in it and the verdicts that make flows 3 and 4 meaningful are text. This adds
the missing half: a live feed of the seven flows' decisions beside the camera.

    flow 1 yard traffic      vehicle-status, mission.go / hold / resume
    flow 2 assistance desk   assist-requests, the ranked card, the human's choice
    flow 3 visual inspector  gate-images -> per-zone findings and the verdict
    flow 4 functional check  track-steps -> checklist items
    flow 5 report and rework inspection-results -> report, rework bay
    flow 6 scenario generator test-requests -> validated spec
    flow 7 adversarial tester test-results -> failing variant, regression

Bound to loopback by default; reach it over the same SSH tunnel as the viewer. The
camera pane is proxied from the viewer so one forwarded port is enough.

    python -m marshal_ai.dashboard --http-port 8090
"""

from __future__ import annotations

import argparse
import json
import logging
import threading
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterable

from marshal_ai.events import Topic

log = logging.getLogger("marshal.dashboard")

MAX_EVENTS = 400

FLOWS: dict[Topic, tuple[int, str]] = {
    Topic.VEHICLE_STATUS: (1, "Yard traffic"),
    Topic.ASSIST_REQUESTS: (2, "Assistance desk"),
    Topic.GATE_IMAGES: (3, "Visual inspector"),
    Topic.TRACK_STEPS: (4, "Functional check"),
    Topic.INSPECTION_RESULTS: (5, "Report and rework"),
    Topic.TEST_REQUESTS: (6, "Scenario generator"),
    Topic.TEST_RESULTS: (7, "Adversarial tester"),
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


@dataclass
class Event:
    seq: int
    at: str
    flow: int
    source: str
    title: str
    detail: str
    level: str = "info"          # info | good | warn | bad

    def as_dict(self) -> dict:
        return {"seq": self.seq, "at": self.at, "flow": self.flow, "source": self.source,
                "title": self.title, "detail": self.detail, "level": self.level}


class EventLog:
    """Bounded, thread-safe log that readers can wait on, like the viewer's FrameBuffer."""

    def __init__(self, maxlen: int = MAX_EVENTS):
        self._events: deque[Event] = deque(maxlen=maxlen)
        self._seq = 0
        self._condition = threading.Condition()

    def publish(self, flow: int, source: str, title: str, detail: str = "",
                level: str = "info") -> Event:
        with self._condition:
            self._seq += 1
            event = Event(self._seq, _now(), flow, source, title, detail, level)
            self._events.append(event)
            self._condition.notify_all()
            return event

    def since(self, seq: int) -> list[Event]:
        with self._condition:
            return [e for e in self._events if e.seq > seq]

    def wait_after(self, seq: int, timeout: float = 20.0) -> list[Event]:
        with self._condition:
            if not any(e.seq > seq for e in self._events):
                self._condition.wait_for(lambda: self._seq > seq, timeout=timeout)
            return [e for e in self._events if e.seq > seq]

    @property
    def latest_seq(self) -> int:
        with self._condition:
            return self._seq


# --------------------------------------------------------------------------- #
# Turning topic payloads into lines a person can read
# --------------------------------------------------------------------------- #

def _fmt(value: Any, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def describe(topic: Topic, data: dict) -> tuple[str, str, str]:
    """(title, detail, level) for one bus message."""
    kind = data.get("type", "")

    if kind == "route.choice":
        probs = data.get("probs") or {}
        detail = ", ".join(f"{k} {p:.2f}" for k, p in probs.items())
        return (f"{data.get('vin', '?')} → {data.get('choice', '?')}",
                f"by {data.get('by', '?')}" + (f" · {detail}" if detail else "")
                + (f" · {data.get('seconds')} s" if data.get("seconds") is not None else ""),
                "info")

    if kind == "parking.step":
        choice = str(data.get("choice", "?"))
        probs = data.get("probs") or {}
        detail = ", ".join(f"{k} {p:.2f}" for k, p in probs.items())
        return (f"{data.get('vin', '?')} parking {data.get('slot', '?')}: {choice}",
                detail or f"decision {data.get('decision', '?')}",
                "good" if choice.startswith("path_") or choice == "parked" else "warn")

    if topic is Topic.VEHICLE_STATUS:
        vin = data.get("vin", "?")
        if kind == "mission.done":
            return f"{vin} arrived at {data.get('location', '?')}", "", "good"
        return (f"{vin} at {data.get('location', '?')}",
                f"{data.get('speed_kmh', 0):.1f} km/h, progress {data.get('progress', 0):.0%}", "info")

    if topic is Topic.ASSIST_REQUESTS:
        options = data.get("options") or []
        names = ", ".join(o.get("id", o) if isinstance(o, dict) else str(o) for o in options)
        return (f"{data.get('vin', '?')} stopped and asked for help",
                f"{data.get('reason', '')} · options: {names or 'none'}", "warn")

    if topic is Topic.GATE_IMAGES:
        views = data.get("views") or []
        return (f"{data.get('vin', '?')} entered the inspection gate",
                f"{len(views)} camera views, variant check {'on' if data.get('check_variant', True) else 'off'}",
                "info")

    if topic is Topic.TRACK_STEPS:
        ok = data.get("telemetry_ok")
        return (f"{data.get('vin', '?')} track step {data.get('step', '?')}: {data.get('name', '')}",
                _fmt(data.get("telemetry", {})), "good" if ok else "warn")

    if topic is Topic.INSPECTION_RESULTS:
        verdict = str(data.get("verdict", "")).upper()
        findings = data.get("findings") or data.get("zone_findings") or []
        level = {"PASS": "good", "REVIEW": "warn", "FAIL": "bad"}.get(verdict, "info")
        return (f"{data.get('vin', '?')} inspection verdict {verdict or '?'}",
                _fmt(findings) if findings else "no findings", level)

    if topic is Topic.TEST_REQUESTS:
        return ("New test requested",
                _fmt(data.get("text", data)), "info")

    if topic is Topic.TEST_RESULTS:
        passed = data.get("passed")
        return (f"{data.get('variant', data.get('scenario_id', 'run'))} "
                f"{'passed' if passed else 'FAILED'}",
                _fmt(data.get("failed_criteria") or data.get("metrics") or {}),
                "good" if passed else "bad")

    return (kind or topic.value, _fmt(data), "info")


def _status_key(data: dict) -> str | None:
    """Vehicle status arrives every tick; only a real change is worth a line."""
    if data.get("type") in ("route.choice", "parking.step"):
        return None                     # a decision is always worth a line, even twice
    if data.get("type") == "mission.done":
        return None
    return (f"{data.get('vin')}|{data.get('location')}|"
            f"{'moving' if (data.get('speed_kmh') or 0) > 0.5 else 'stopped'}|"
            f"{round((data.get('progress') or 0) * 4)}")


def attach(app: Any, events: EventLog) -> None:
    """Subscribe the log to every topic so decisions appear as they happen."""
    last_status: dict[str, str] = {}
    lock = threading.Lock()

    for topic, (flow, source) in FLOWS.items():
        def handler(data: dict, _t: Topic = topic, _f: int = flow, _s: str = source) -> None:
            if _t is Topic.VEHICLE_STATUS:
                key = _status_key(data)
                if key is not None:
                    vin = str(data.get("vin"))
                    with lock:
                        if last_status.get(vin) == key:
                            return
                        last_status[vin] = key
            try:
                title, detail, level = describe(_t, data)
            except Exception as exc:                      # a bad payload must not kill the bus
                title, detail, level = f"{_t.value} (undescribable)", str(exc)[:120], "warn"
            events.publish(_f, _s, title, detail, level)

        app.rt.bus.subscribe(topic, handler)


# --------------------------------------------------------------------------- #
# State snapshot: the records behind the feed
# --------------------------------------------------------------------------- #

def snapshot(app: Any) -> dict:
    from marshal_ai.records import AssistanceCase, Car, CarReport, Scenario, TestRun

    rt = app.rt

    def rows(model, fields: Iterable[str], limit: int = 12) -> list[dict]:
        try:
            items = list(rt.store.list(model))[-limit:]
        except Exception:
            return []
        out = []
        for item in items:
            row = {}
            for f in fields:
                value = getattr(item, f, None)
                row[f] = value.value if hasattr(value, "value") else value
            out.append(row)
        return out

    try:
        kpis = app.kpis()
    except Exception as exc:
        kpis = {"error": str(exc)[:120]}

    return {
        "at": _now(),
        "cars": rows(Car, ("vin", "state", "location")),
        "cases": rows(AssistanceCase, ("case_id", "vin", "chosen", "resolved_by")),
        "reports": rows(CarReport, ("vin", "verdict", "rework_bay")),
        "scenarios": rows(Scenario, ("id", "status", "source")),
        "runs": rows(TestRun, ("run_id", "scenario_id", "variant", "passed")),
        "pending": [{"id": p.proposal_id, "kind": p.kind, "summary": p.summary}
                    for p in rt.pending()],
        "kpis": kpis,
    }


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Marshal dashboard</title><style>
:root{color-scheme:dark}
html,body{margin:0;background:#101215;color:#e6e8ea;font:14px/1.45 system-ui,sans-serif}
header{padding:10px 16px;border-bottom:1px solid #23262b;display:flex;gap:16px;align-items:baseline}
header b{font-size:15px}header span{color:#8b9199;font-size:12px}
main{display:grid;grid-template-columns:minmax(360px,1fr) minmax(320px,1fr);gap:14px;padding:14px}
@media(max-width:900px){main{grid-template-columns:1fr}}
section{background:#161a1f;border:1px solid #23262b;border-radius:8px;overflow:hidden}
h2{margin:0;padding:8px 12px;font-size:12px;text-transform:uppercase;letter-spacing:.08em;
   color:#8b9199;border-bottom:1px solid #23262b}
img{display:block;width:100%;height:auto;background:#000}
#feed{max-height:52vh;overflow:auto;margin:0;padding:0;list-style:none}
#feed li{padding:7px 12px;border-bottom:1px solid #1d2126;display:grid;
         grid-template-columns:52px 1fr;gap:8px}
#feed time{color:#6b7280;font-variant-numeric:tabular-nums;font-size:12px}
.flow{display:inline-block;min-width:16px;padding:0 5px;margin-right:6px;border-radius:4px;
      background:#23262b;color:#9aa3ad;font-size:11px}
.t{font-weight:600}.d{color:#8b9199;font-size:12px;word-break:break-word}
.good .t{color:#5ac27a}.warn .t{color:#e0b341}.bad .t{color:#f2645a}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{text-align:left;padding:5px 12px;border-bottom:1px solid #1d2126}
th{color:#6b7280;font-weight:500}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:0}
.empty{padding:10px 12px;color:#6b7280;font-size:12px}
</style></head><body>
<header><b>Marshal</b><span id="meta">connecting…</span></header>
<main>
  <section><h2>Plant camera</h2><img id="cam" src="/camera.mjpg" alt="plant camera"></section>
  <section><h2>Flow activity</h2><ul id="feed"></ul></section>
  <section><h2>Cars</h2><div id="cars"></div></section>
  <section><h2>Test lab</h2><div id="lab"></div></section>
  <section><h2>Reports and rework</h2><div id="reports"></div></section>
  <section><h2>Waiting for a human</h2><div id="pending"></div></section>
</main>
<script>
const feed = document.getElementById('feed');
function esc(s){const d=document.createElement('div');d.textContent=s==null?'':String(s);return d.innerHTML}
function addEvents(list){
  for(const e of list){
    const li=document.createElement('li'); li.className=e.level;
    li.innerHTML='<time>'+esc(e.at)+'</time><div><span class="flow">'+e.flow+'</span>'+
      '<span class="t">'+esc(e.title)+'</span>'+(e.detail?'<div class="d">'+esc(e.detail)+'</div>':'')+'</div>';
    feed.prepend(li);
  }
  while(feed.children.length>300) feed.removeChild(feed.lastChild);
}
function table(rows,cols){
  if(!rows||!rows.length) return '<div class="empty">nothing yet</div>';
  let h='<table><tr>'+cols.map(c=>'<th>'+esc(c)+'</th>').join('')+'</tr>';
  for(const r of rows) h+='<tr>'+cols.map(c=>'<td>'+esc(r[c])+'</td>').join('')+'</tr>';
  return h+'</table>';
}
const es=new EventSource('/events');
es.onmessage=ev=>addEvents(JSON.parse(ev.data));
es.onerror=()=>{document.getElementById('meta').textContent='event stream lost — reload'};
async function poll(){
  try{
    const s=await (await fetch('/state')).json();
    document.getElementById('meta').textContent=
      'updated '+s.at+' · '+(s.cars||[]).length+' cars · '+(s.runs||[]).length+' test runs';
    document.getElementById('cars').innerHTML=table(s.cars,['vin','state','location']);
    document.getElementById('lab').innerHTML=
      '<div class="grid2"><div>'+table(s.scenarios,['id','status'])+'</div>'+
      '<div>'+table(s.runs,['variant','passed'])+'</div></div>';
    document.getElementById('reports').innerHTML=table(s.reports,['vin','verdict','rework_bay']);
    document.getElementById('pending').innerHTML=table(s.pending,['kind','summary']);
  }catch(e){}
  setTimeout(poll,2000);
}
poll();
</script></body></html>"""


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, app, events: EventLog, camera_url: str):
        super().__init__(address, handler)
        self.app = app
        self.events = events
        self.camera_url = camera_url


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "marshal-dashboard"

    def log_message(self, fmt, *args):           # keep journald quiet
        log.debug(fmt, *args)

    def do_GET(self) -> None:                    # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._bytes(PAGE.encode(), "text/html; charset=utf-8")
        elif path == "/healthz":
            self._bytes(json.dumps({"status": "ok", "events": self.server.events.latest_seq},
                                   sort_keys=True).encode(), "application/json")
        elif path == "/state":
            try:
                body = json.dumps(snapshot(self.server.app), sort_keys=True, default=str).encode()
            except Exception as exc:
                body = json.dumps({"error": str(exc)[:200]}).encode()
            self._bytes(body, "application/json")
        elif path == "/events":
            self._events()
        elif path in ("/camera.mjpg", "/snapshot.jpg"):
            self._camera(path)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def _bytes(self, body: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _events(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        events = self.server.events
        seq = max(0, events.latest_seq - 40)      # a little history on connect
        try:
            while True:
                batch = events.wait_after(seq, timeout=15.0)
                if batch:
                    seq = batch[-1].seq
                    payload = json.dumps([e.as_dict() for e in batch], default=str)
                    self.wfile.write(f"data: {payload}\n\n".encode())
                else:
                    self.wfile.write(b": keep-alive\n\n")   # keeps proxies from closing
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

    def _camera(self, path: str) -> None:
        """Proxy the viewer so the dashboard needs only one forwarded port."""
        url = self.server.camera_url if path == "/camera.mjpg" else \
            self.server.camera_url.replace("/stream.mjpg", "/snapshot.jpg")
        try:
            upstream = urllib.request.urlopen(url, timeout=10)
        except (urllib.error.URLError, OSError) as exc:
            body = f"camera unavailable at {url}: {exc}".encode()
            self.send_response(HTTPStatus.SERVICE_UNAVAILABLE)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", upstream.headers.get("Content-Type", "image/jpeg"))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            while True:
                chunk = upstream.read(8192)
                if not chunk:
                    return
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            upstream.close()


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bind", default="127.0.0.1",
                    help="loopback by default; reach it over the SSH tunnel")
    ap.add_argument("--http-port", type=int, default=8090)
    ap.add_argument("--camera-url", default="http://127.0.0.1:8080/stream.mjpg",
                    help="the CARLA viewer stream to proxy into the camera pane")
    ap.add_argument("--seed-demo", action="store_true",
                    help="seed the fake plant so the panels are populated immediately")
    ap.add_argument("--run-plant", action="store_true",
                    help="also run the plant in this process. Needed with MARSHAL_BUS=memory, "
                         "where only publishers in the same process reach the feed; with "
                         "MARSHAL_BUS=gcp the flows can run anywhere.")
    ap.add_argument("--plant-speed", type=float, default=1.0,
                    help="simulated seconds per real second when --run-plant is set")
    ap.add_argument("--demo", action="store_true",
                    help="also drive the beats that need a trigger (stuck car, scenario "
                         "generation, adversarial search, release gate) so all seven flows "
                         "appear. Repeats on a loop.")
    return ap


async def _demo_round(app: Any, events: EventLog) -> None:
    """The scripted beats from scripts/demo.py, so flows 2, 6 and 7 also show up."""
    import asyncio

    from marshal_ai.app import RELEASES
    from marshal_ai.demo import BOX_SCENE, seed_demo
    from marshal_ai.driving import MissionGo
    from marshal_ai.lab.runner import SimRunner
    from marshal_ai.records import AssistanceCase

    rt = app.rt
    supervisor, engineer = "supervisor-ana", "engineer-kim"

    # flow 2: a car stops and asks; the desk ranks options and a human decides
    case_id = rt.next_id("AC")
    vin = rt.next_id("VIN")
    seed_demo(rt, [vin])
    await rt.missions.send(MissionGo(mission_id=rt.next_id("M"), vin=vin, waypoints=["yard.Y2"]))
    await app.run_for(8)
    if rt.world is not None:
        rt.world.scenes[case_id] = BOX_SCENE
    await rt.driving.raise_assist(vin, case_id, "unknown object in lane",
                                  ["wait_for_removal", "pass_left_3kmh", "reroute_lane_c"],
                                  clip_uri=f"gs://marshal/assist/{case_id}.mp4")
    case = rt.store.get(AssistanceCase, case_id)
    if case is not None:
        events.publish(2, "Assistance desk", f"Card for {vin}: {case.situation}",
                       f"recommended {case.recommended} ({case.confidence:.0%})", "warn")
        await app.assistance.decide(case_id, case.recommended, supervisor)
        events.publish(2, "Assistance desk", f"{supervisor} chose {case.recommended}",
                       "case sent to the test lab", "good")

    # flow 6: plain language becomes a validated scenario
    request = ("A worker steps out from behind a parked forklift at dusk while a car turns "
               "into the finishing lane.")
    gen = await asyncio.to_thread(app.generator.generate, request)
    if not gen.ok:
        events.publish(6, "Scenario generator", "Generation needs an engineer",
                       _fmt(gen.errors), "bad")
        return
    spec = gen.scenario.spec
    events.publish(6, "Scenario generator", f"{gen.scenario.id} generated and dry-run OK",
                   f"{spec['time_of_day']}, {[a['type'] for a in spec['actors']]}, "
                   f"min gap {gen.dry_run_metrics.get('min_gap_to_person_m')} m", "good")
    app.generator.review(gen.scenario.id, True, engineer)
    events.publish(6, "Scenario generator", f"{engineer} approved {gen.scenario.id} into the library")

    # flow 7: search for the smallest change that breaks it
    result = await asyncio.to_thread(app.adversarial.search, gen.scenario.id)
    if result.minimal is not None:
        events.publish(7, "Adversarial tester",
                       f"{result.minimal.spec.id} fails after {len(result.runs)} runs",
                       f"changes {result.minimal.changes} · "
                       f"{'; '.join(result.minimal.run.failed_criteria)}", "bad")
    else:
        events.publish(7, "Adversarial tester",
                       f"robust across {len(result.runs)} runs", result.report(), "good")

    # flow 7 closing: the release gate is the thing a human acts on
    for release in ("behavior-agent-1.0", "behavior-agent-1.1"):
        report = app.release_gate.evaluate(release, SimRunner(rt.map, RELEASES[release]))
        blocked = getattr(report, "blocked", None)
        events.publish(7, "Release gate", report.summary(), "",
                       "bad" if (blocked if blocked is not None else "blocked" in report.summary())
                       else "good")


def _start_demo(app: Any, events: EventLog, period_s: float = 120.0) -> None:
    import asyncio

    def run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def pump() -> None:
            while True:
                try:
                    await _demo_round(app, events)
                except Exception as exc:
                    events.publish(0, "dashboard", "Demo round failed", str(exc)[:200], "bad")
                await asyncio.sleep(period_s)

        try:
            loop.run_until_complete(pump())
        finally:
            loop.close()

    threading.Thread(target=run, name="marshal-demo", daemon=True).start()


def _start_plant(app: Any, events: EventLog, speed: float) -> None:
    """Drive the fake plant on a background loop so flow 1 keeps producing decisions."""
    import asyncio

    def run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def pump() -> None:
            while True:
                try:
                    await app.run_for(0.5 * speed)
                except Exception as exc:
                    events.publish(0, "dashboard", "Plant loop error", str(exc)[:160], "bad")
                    await asyncio.sleep(2.0)
                else:
                    await asyncio.sleep(0.5)

        try:
            loop.run_until_complete(pump())
        finally:
            loop.close()

    threading.Thread(target=run, name="marshal-plant", daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from marshal_ai.app import get_app
    app = get_app()
    if args.seed_demo:
        from marshal_ai.demo import seed_demo
        seed_demo(app.rt)

    events = EventLog()
    attach(app, events)
    events.publish(0, "dashboard", "Dashboard started",
                   f"llm={app.rt.settings.llm} driving={app.rt.settings.driving} "
                   f"store={app.rt.settings.store} bus={app.rt.settings.bus}")

    if args.run_plant:
        if app.rt.settings.driving != "fake":
            events.publish(0, "dashboard", "Plant loop skipped",
                           f"--run-plant drives the fake plant only, "
                           f"driving={app.rt.settings.driving}", "warn")
        else:
            _start_plant(app, events, args.plant_speed)
            events.publish(0, "dashboard", "Plant running",
                           f"{args.plant_speed}x simulated time")
    elif app.rt.settings.bus == "memory":
        events.publish(0, "dashboard", "Feed will stay quiet",
                       "MARSHAL_BUS=memory only sees this process. Add --run-plant, or set "
                       "MARSHAL_BUS=gcp to receive flows from the CARLA host.", "warn")

    if args.demo:
        if app.rt.settings.driving != "fake":
            events.publish(0, "dashboard", "Demo skipped",
                           "--demo drives the fake plant only", "warn")
        else:
            _start_demo(app, events)
            events.publish(0, "dashboard", "Demo loop running",
                           "stuck car, scenario generation, adversarial search, release gate")

    server = DashboardServer((args.bind, args.http_port), Handler, app, events, args.camera_url)
    log.info("dashboard on http://%s:%d (camera proxied from %s)",
             args.bind, args.http_port, args.camera_url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
