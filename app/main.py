"""FastAPI application entry point: /health, /webex/webhook, keepalive loop."""
from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

import requests
import uvicorn
from fastapi import FastAPI, Request, Response

from app.config import Config
from app.integrations.webex import WebexAdapter
from app.services.bot_service import TonyService
from app.storage.excel import ExcelStore, StoreError, WorkbookLocked
from app.storage.state import State

log = logging.getLogger("tony.main")


def build_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or Config()
    logging.basicConfig(level=cfg.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    store = ExcelStore(cfg)
    state = State(cfg)
    adapter = WebexAdapter(cfg)
    service = TonyService(cfg, store, state, adapter)
    pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="tony-evt")
    started = time.time()

    def keepalive():
        """Webhook self-heal every 5 min, dead-man ping, and periodic alive log."""
        last_alive = 0.0
        while True:
            try:
                adapter.ensure_webhook()
                if cfg.ping_url:
                    requests.get(cfg.ping_url, timeout=10)
                if time.time() - last_alive > 86400:
                    log.info(
                        "Tony is alive | uptime=%.1fh cases=%d queue=%d webhook=%s",
                        (time.time() - started) / 3600,
                        len(store.all_cases()),
                        service.wq.depth,
                        adapter.webhook_status,
                    )
                    last_alive = time.time()
            except Exception:
                log.exception("keepalive tick failed")
            time.sleep(300)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup: connect to Webex and start keepalive
        for attempt in range(10):
            try:
                adapter.start()
                break
            except Exception as e:
                log.error("Webex start failed (%s); retry in 15s", e)
                time.sleep(15)
        threading.Thread(target=keepalive, name="tony-keepalive", daemon=True).start()
        yield
        # Shutdown: cleanup pool
        pool.shutdown(wait=False)

    app = FastAPI(title="Tony QA Bot", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.post("/webex/webhook")
    async def webhook(request: Request):
        body = await request.body()
        if not adapter.verify_signature(body, request.headers.get("X-Spark-Signature")):
            return Response(status_code=401)
        payload = json.loads(body or b"{}")

        def work():
            try:
                msg = adapter.parse_event(payload)
                if msg:
                    service.handle(msg)
            except Exception:
                log.exception("webhook processing failed")

        pool.submit(work)  # Answer Webex immediately, process in background
        return {"ok": True}

    @app.get("/health")
    def health(response: Response):
        wb = "ok"
        try:
            n = len(store.all_cases())
        except (WorkbookLocked, StoreError, Exception) as e:
            wb, n = f"error: {e}", -1
        if store.is_locked():
            wb = "locked (writes queued)" if wb == "ok" else wb
        ok = not wb.startswith("error")
        response.status_code = 200 if ok else 503
        return {
            "status": "ok" if ok else "degraded",
            "uptime_s": int(time.time() - started),
            "workbook": wb,
            "cases": n,
            "write_queue": service.wq.depth,
            "webhook": adapter.webhook_status,
            "last_event_age_s": int(time.time() - service.last_event_ts) if service.last_event_ts else None,
        }

    return app


def main():
    cfg = Config()
    uvicorn.run(build_app(cfg), host="0.0.0.0", port=cfg.port, log_level=cfg.log_level.lower())


if __name__ == "__main__":
    main()

