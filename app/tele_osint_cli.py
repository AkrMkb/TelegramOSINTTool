from __future__ import annotations
import argparse
import asyncio
from pathlib import Path
import signal
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from app import create_app


async def _async_main(args):
    app = await create_app(args.config)
    await app.init_runtime(debug=args.debug)

    found = []
    if args.discover:
        print("[discover] keyword search…")
        found = await app.discover(debug=args.debug)
        print(f"[discover] keyword hits: {len(found)}")

    crawl_found = []
    if getattr(app.cfg.discovery, "crawl", None) and app.cfg.discovery.crawl.enabled:
        print("[discover] crawling via links/mentions…")
        seeds = sorted(set((app.cfg.seed_channels or []) + found))
        crawl_found = await app.crawl(seeds=seeds, debug=args.debug)
        print(f"[discover] crawl hits: {len(crawl_found)}")

    targets = sorted(set((app.cfg.seed_channels or []) + found + crawl_found))

    if targets:
        await app.join_targets(targets, debug=args.debug)
        entities = await app.entities_from_refs(targets, debug=args.debug)
    else:
        entities = None

    if args.backfill:
        await app.backfill_targets(targets, new_only=args.new_only, debug=args.debug)

    if args.run:
        await app.start_live(entities=entities, debug=args.debug)
        await app.start_maintenance_background(debug=args.debug)

        stop_evt = asyncio.Event()

        def _handle_stop():
            stop_evt.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _handle_stop)
            except NotImplementedError:
                pass

        print("[main] running. Press Ctrl+C to stop.")
        await stop_evt.wait()
        print("[main] stopping…")
        await app.shutdown()
    else:
        await app.shutdown()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--discover", action="store_true")
    p.add_argument("--backfill", action="store_true")
    p.add_argument("--run", action="store_true")
    p.add_argument("--debug", action="store_true")
    p.add_argument("--new-only", action="store_true")
    args = p.parse_args()
    asyncio.run(_async_main(args))


if __name__ == "__main__":
    main()
