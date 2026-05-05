import asyncio
import signal

from aiohttp import web

import orders_dashboard


async def _run() -> None:
    app = orders_dashboard.create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="127.0.0.1", port=8088)
    await site.start()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _stop() -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            pass

    try:
        await stop_event.wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(_run())
