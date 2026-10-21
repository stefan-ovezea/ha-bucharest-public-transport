"""Opt-in integration smoke check against the public, keyless TPBI feeds."""

import concurrent.futures
import json
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.bucharest_transport.const import FEED_URLS, STATIC_URL
from custom_components.bucharest_transport.transport.engine import TransportEngine
from custom_components.bucharest_transport.transport.realtime import decode
from custom_components.bucharest_transport.transport.schedule import Schedule, import_feed
from custom_components.bucharest_transport.transport.search import search


def fetch(item):
    name, url = item
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "BucharestPublicTransport/0.2.1 (+https://github.com/stefan-ovezea/ha-bucharest-public-transport)"
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return name, (response.read(), dict(response.headers))


def main():
    cache = Path(sys.argv[1] if len(sys.argv) > 1 else ".cache/live.sqlite").resolve()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        responses = dict(executor.map(fetch, [("static", STATIC_URL), *FEED_URLS.items()]))
    start = time.monotonic()
    changed = import_feed(*responses["static"][:1], cache, responses["static"][1])
    schedule = Schedule(cache)
    engine = TransportEngine(schedule)
    now = time.time()
    for name in FEED_URLS:
        engine.feeds[name] = decode(responses[name][0], name, schedule, now)
    stations = search(list(schedule.stops.values()), "Pta Uniri")
    examples = []
    seen = set()
    for vehicle in engine.vehicles(now):
        if vehicle["agency"] in seen or not vehicle["stop_id"]:
            continue
        seen.add(vehicle["agency"])
        board = engine.board(vehicle["stop_id"], now)
        examples.append(
            {
                "agency": vehicle["agency"],
                "stop": board["stop"]["name"],
                "status": board["status"],
                "arrivals": len(board["arrivals"]),
                "sources": dict(Counter(r["source"] for r in board["arrivals"])),
            }
        )
    report = {
        "cache_changed": changed,
        "elapsed_seconds": round(time.monotonic() - start, 2),
        "timetable": schedule.meta,
        "routes": len(schedule.routes),
        "stops": len(schedule.stops),
        "search_results": len(stations),
        "feeds": {k: s.health(now) for k, s in engine.feeds.items()},
        "fresh_vehicles_by_agency": dict(Counter(v["agency"] for v in engine.vehicles(now))),
        "boards": examples,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    assert schedule.ready and stations and len(schedule.routes) > 100
    assert all(s.fresh(now) for s in engine.feeds.values())
    assert any(b["sources"].get("live", 0) for b in examples)


if __name__ == "__main__":
    main()
