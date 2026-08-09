"""FinLLM service entrypoint."""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from pathlib import Path
from types import FrameType

from service.config import ServiceConfig, StartupValidationError
from service.http_server import FinLLMHTTPServer
from service.runtime import ServiceRuntime


LOGGER = logging.getLogger("finllm")


def configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("FINLLM_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


class Application:
    def __init__(self, runtime: ServiceRuntime) -> None:
        self.runtime = runtime
        self.server = FinLLMHTTPServer(
            (runtime.config.host, runtime.config.port), runtime
        )
        self._shutdown_lock = threading.Lock()
        self._shutdown_started = False
        self._drain_result: bool | None = None

    def request_shutdown(self, reason: str) -> None:
        with self._shutdown_lock:
            if self._shutdown_started:
                return
            self._shutdown_started = True
        LOGGER.info("shutdown requested: %s", reason)
        self.runtime.begin_shutdown()
        # Keep the listener alive while accepted requests drain so /health and
        # /ready remain observable and new POSTs receive an explicit 503.
        self._drain_result = self.runtime.wait_for_drain()
        self.server.shutdown()

    def signal_handler(self, signum: int, frame: FrameType | None) -> None:
        # HTTPServer.shutdown() must run from a thread other than serve_forever().
        threading.Thread(
            target=self.request_shutdown,
            args=(signal.Signals(signum).name,),
            name="finllm-shutdown",
            daemon=True,
        ).start()

    def run(self) -> int:
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGINT, self.signal_handler)
        LOGGER.info("FinLLM API listening on %s:%s", *self.server.server_address)
        try:
            self.server.serve_forever(poll_interval=0.2)
        finally:
            if not self._shutdown_started:
                self.runtime.begin_shutdown()
            drained = (
                self._drain_result
                if self._drain_result is not None
                else self.runtime.wait_for_drain()
            )
            if not drained:
                LOGGER.warning(
                    "shutdown drain timeout reached after %.3fs; "
                    "remaining request threads will not block process exit",
                    self.runtime.config.shutdown_timeout_seconds,
                )
            self.server.server_close()
            self.runtime.close()
        return 0


def main() -> int:
    configure_logging()
    config_path = Path(
        os.environ.get("FINLLM_CONFIG_PATH", "deploy/config/service.json")
    )
    runtime: ServiceRuntime | None = None
    try:
        config = ServiceConfig.load(config_path)
        runtime = ServiceRuntime(config)
        runtime.initialize()
    except StartupValidationError as exc:
        LOGGER.error("startup validation failed: %s", exc)
        if runtime is not None:
            runtime.close()
        return 2
    return Application(runtime).run()


if __name__ == "__main__":
    raise SystemExit(main())
