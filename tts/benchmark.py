"""Offline image smoke and HTTP benchmark. Run: docker run --network none ...

Reports buffered time-to-playable, not an invented streaming first-chunk metric.
Uses the actual application lifespan/readiness, HTTP handler and all encoders.
"""

import importlib
from io import BytesIO
import json
import platform
import resource
import threading
import time
import urllib.error
import urllib.request

from tts.app import create_app
from tts.runtime import KokoroRuntime

TEXTS = {
    "short": "Hello! I’m Daemon. Your speech now runs on our own server, without an external API key.",
    "moderate": (
        "Here is the plan for tomorrow. Start with a short review of the project, "
        "then choose the most important task and work on it without distractions. "
        "Take a break after the first hour, and check whether the result matches "
        "the original request. Keep a record of the decisions, tests, and remaining "
        "questions so another engineer can pick up the work. "
        "In the afternoon, review the implementation with a colleague and make "
        "the smallest changes needed to address real problems. "
        "Finish by saving the result, sharing a concise summary, and naming the "
        "next concrete step. Good engineering is a combination of clear boundaries, "
        "useful evidence, and honest communication."
    ),
}


def main() -> None:
    import uvicorn

    runtime = KokoroRuntime()
    started = time.monotonic()
    config = uvicorn.Config(create_app(runtime), host="127.0.0.1", port=8080, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    ready = None
    while time.monotonic() - started < 120:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8080/ready", timeout=1) as response:
                ready = json.load(response)
                break
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.05)
    if ready is None or not ready["ready"]:
        raise RuntimeError("Real model did not become ready")
    result = {
        "environment": platform.platform(),
        "startup_to_ready_seconds": time.monotonic() - started,
        "ready": ready,
        "cases": [],
    }
    av = importlib.import_module("av")
    try:
        for name, text in TEXTS.items():
            for format in ("mp3", "wav", "opus"):
                request = urllib.request.Request(
                    "http://127.0.0.1:8080/synthesize",
                    data=json.dumps({"text": text, "format": format}).encode(),
                    headers={"Content-Type": "application/json"},
                )
                begin = time.monotonic()
                with urllib.request.urlopen(request, timeout=125) as response:
                    first = time.monotonic() - begin
                    content = response.read()
                    duration = float(response.headers["x-audio-duration"])
                    synthesis = float(response.headers["x-synthesis-seconds"])
                playable = time.monotonic() - begin
                with av.open(BytesIO(content)) as decoded:
                    frames = list(decoded.decode(audio=0))
                    decoded_seconds = sum(f.samples / f.sample_rate for f in frames)
                    assert decoded_seconds > 0
                    assert any(abs(f.to_ndarray()).max() > 0 for f in frames)
                result["cases"].append(
                    {
                        "name": name,
                        "format": format,
                        "characters": len(text),
                        "first_response_seconds": first,
                        "time_to_playable_seconds": playable,
                        "synthesis_seconds": synthesis,
                        "audio_seconds": duration,
                        "decoded_seconds": decoded_seconds,
                        "realtime_factor": synthesis / duration,
                        "bytes": len(content),
                    }
                )
        result["peak_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        print(json.dumps(result, indent=2))
    finally:
        server.should_exit = True
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
