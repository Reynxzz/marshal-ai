"""Low-resource browser viewer for a remote, headless CARLA server."""

from __future__ import annotations

import argparse
import io
import json
import logging
import signal
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

LOG = logging.getLogger("carla-viewer")
BOUNDARY = "carla-frame"


class FrameBuffer:
    """Keep only the newest JPEG so slow browsers cannot grow a queue."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._jpeg: bytes | None = None
        self._sequence = 0
        self._updated_at = 0.0

    def publish(self, jpeg: bytes) -> None:
        with self._condition:
            self._jpeg = jpeg
            self._sequence += 1
            self._updated_at = time.monotonic()
            self._condition.notify_all()

    def wait_after(self, sequence: int, timeout: float = 5.0) -> tuple[int, bytes] | None:
        with self._condition:
            self._condition.wait_for(lambda: self._sequence > sequence, timeout=timeout)
            if self._sequence <= sequence or self._jpeg is None:
                return None
            return self._sequence, self._jpeg

    def status(self) -> dict[str, float | int | bool]:
        with self._condition:
            age = time.monotonic() - self._updated_at if self._updated_at else 0.0
            return {
                "frame_ready": self._jpeg is not None,
                "frame_sequence": self._sequence,
                "frame_age_s": round(age, 2),
                "jpeg_bytes": len(self._jpeg) if self._jpeg else 0,
            }


class ViewerServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, frames: FrameBuffer, metadata: dict[str, Any]):
        self.frames = frames
        self.metadata = metadata
        super().__init__(address, ViewerHandler)


class ViewerHandler(BaseHTTPRequestHandler):
    server: ViewerServer

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlsplit(self.path).path
        if path == "/":
            self._page()
        elif path == "/healthz":
            self._health()
        elif path == "/snapshot.jpg":
            self._snapshot()
        elif path == "/stream.mjpg":
            self._stream()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def _page(self) -> None:
        meta = self.server.metadata
        body = f"""<!doctype html>
<html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width\">
<title>CARLA viewer</title><style>
html,body{{margin:0;background:#111;color:#ddd;font:14px system-ui}}main{{max-width:960px;margin:auto;padding:12px}}
img{{display:block;width:100%;height:auto;background:#222}}small{{display:block;margin-top:8px}}
</style></head><body><main><img src=\"/stream.mjpg\" alt=\"CARLA camera stream\">
<small>{meta['map_name']} · {meta['width']}×{meta['height']} · {meta['fps']:.1f} FPS · spawn {meta['spawn_index']}</small>
</main></body></html>""".encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _health(self) -> None:
        body = json.dumps({**self.server.metadata, **self.server.frames.status()}, sort_keys=True).encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _snapshot(self) -> None:
        frame = self.server.frames.wait_after(-1)
        if frame is None:
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "No CARLA frame available")
            return
        _, jpeg = frame
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(jpeg)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(jpeg)

    def _stream(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        sequence = -1
        try:
            while True:
                frame = self.server.frames.wait_after(sequence)
                if frame is None:
                    continue
                sequence, jpeg = frame
                self.wfile.write(
                    f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n".encode()
                )
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
        except (BrokenPipeError, ConnectionResetError):
            return

    def log_message(self, fmt: str, *args: Any) -> None:
        LOG.debug("%s - %s", self.client_address[0], fmt % args)


def _camera_transform(carla: Any, spawn: Any, height: float) -> Any:
    forward = spawn.get_forward_vector()
    location = spawn.location - forward * 8.0
    location.z += height
    return carla.Transform(
        location,
        carla.Rotation(pitch=-35.0, yaw=spawn.rotation.yaw),
    )


def _encoder(frames: FrameBuffer, quality: int):
    from PIL import Image

    def encode(image: Any) -> None:
        try:
            rgb = Image.frombuffer(
                "RGBA", (image.width, image.height), image.raw_data, "raw", "BGRA", 0, 1
            ).convert("RGB")
            output = io.BytesIO()
            rgb.save(output, format="JPEG", quality=quality, optimize=False)
            frames.publish(output.getvalue())
        except Exception:
            LOG.exception("failed to encode CARLA frame")

    return encode


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--carla-host", default="localhost")
    ap.add_argument("--carla-port", type=int, default=2000)
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--http-port", type=int, default=8080)
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=360)
    ap.add_argument("--fps", type=float, default=2.0)
    ap.add_argument("--jpeg-quality", type=int, default=60)
    ap.add_argument("--spawn-index", type=int, default=0)
    ap.add_argument("--camera-at", default=None, metavar="X,Y,Z,YAW",
                    help="look from an explicit pose instead of a spawn point, e.g. "
                         "'199.64,-134.95,0.5,-110.3'. Town05 has no spawn points along "
                         "some plant routes, so named map points need this.")
    ap.add_argument("--camera-height", type=float, default=8.0)
    return ap


def _pose_from_arg(carla: Any, text: str) -> Any:
    parts = text.split(",")
    if len(parts) != 4:
        raise SystemExit("--camera-at needs exactly X,Y,Z,YAW")
    try:
        x, y, z, yaw = (float(p) for p in parts)
    except ValueError:
        raise SystemExit(f"--camera-at must be four numbers, got {text!r}")
    return carla.Transform(carla.Location(x=x, y=y, z=z), carla.Rotation(yaw=yaw))


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.fps <= 0 or args.width <= 0 or args.height <= 0:
        raise SystemExit("width, height and fps must be positive")
    if not 1 <= args.jpeg_quality <= 95:
        raise SystemExit("jpeg-quality must be between 1 and 95")

    import carla

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    client = carla.Client(args.carla_host, args.carla_port)
    client.set_timeout(20.0)
    world = client.get_world()
    spawns = world.get_map().get_spawn_points()
    if args.camera_at is None:
        if not spawns:
            raise SystemExit("the current CARLA map has no vehicle spawn points")
        if not 0 <= args.spawn_index < len(spawns):
            raise SystemExit(
                f"spawn-index must be between 0 and {len(spawns) - 1}. If this is lower than "
                f"expected the server is probably on the wrong map ({world.get_map().name}); "
                f"load the right one and restart rather than lowering the index.")
        pose = spawns[args.spawn_index]
    else:
        pose = _pose_from_arg(carla, args.camera_at)

    blueprint = world.get_blueprint_library().find("sensor.camera.rgb")
    blueprint.set_attribute("image_size_x", str(args.width))
    blueprint.set_attribute("image_size_y", str(args.height))
    blueprint.set_attribute("sensor_tick", str(1.0 / args.fps))
    blueprint.set_attribute("fov", "90")
    camera = world.spawn_actor(
        blueprint,
        _camera_transform(carla, pose, args.camera_height),
    )

    frames = FrameBuffer()
    camera.listen(_encoder(frames, args.jpeg_quality))
    metadata = {
        "status": "ok",
        "map_name": world.get_map().name,
        "width": args.width,
        "height": args.height,
        "fps": args.fps,
        "spawn_index": args.spawn_index if args.camera_at is None else args.camera_at,
    }
    server = ViewerServer((args.bind, args.http_port), frames, metadata)
    stop = threading.Event()

    def request_stop(*_: Any) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    thread = threading.Thread(target=server.serve_forever, name="http", daemon=True)
    thread.start()
    LOG.info(
        "viewer ready on http://%s:%d (%dx%d at %.1f FPS)",
        args.bind, args.http_port, args.width, args.height, args.fps,
    )
    try:
        while not stop.wait(1.0):
            pass
    finally:
        server.shutdown()
        server.server_close()
        camera.stop()
        camera.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
