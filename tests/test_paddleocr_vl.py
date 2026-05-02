from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from wordbyword.source_parsing.paddleocr_vl import (
    PaddleOCRVLInterruptedError,
    PaddleOCRVLSettings,
    run_paddleocr_vl,
)


class PaddleOCRVLWorkerSupervisionTest(unittest.TestCase):
    def test_keyboard_interrupt_terminates_worker_process(self) -> None:
        context = FakeMultiprocessingContext()

        with patch("wordbyword.source_parsing.paddleocr_vl.multiprocessing.get_context", return_value=context):
            with self.assertRaises(PaddleOCRVLInterruptedError):
                run_paddleocr_vl(
                    page_image_paths=[Path("page-0001.png")],
                    assets_dir=Path("assets"),
                    settings=PaddleOCRVLSettings(),
                )

        assert context.process is not None
        assert context.queue is not None
        self.assertTrue(context.process.started)
        self.assertTrue(context.process.terminated)
        self.assertFalse(context.process.killed)
        self.assertTrue(context.queue.closed)
        self.assertTrue(context.queue.joined)


class FakeMultiprocessingContext:
    def __init__(self) -> None:
        self.queue: FakeQueue | None = None
        self.process: FakeProcess | None = None

    def Queue(self) -> "FakeQueue":
        self.queue = FakeQueue()
        return self.queue

    def Process(self, **kwargs: object) -> "FakeProcess":
        self.process = FakeProcess(**kwargs)
        return self.process


class FakeQueue:
    def __init__(self) -> None:
        self.closed = False
        self.joined = False

    def get(self, *, timeout: float) -> dict[str, object]:
        raise KeyboardInterrupt

    def close(self) -> None:
        self.closed = True

    def join_thread(self) -> None:
        self.joined = True


class FakeProcess:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.exitcode: int | None = None
        self.started = False
        self.terminated = False
        self.killed = False
        self._alive = True

    def start(self) -> None:
        self.started = True

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout: float | None = None) -> None:
        return None

    def terminate(self) -> None:
        self.terminated = True
        self._alive = False

    def kill(self) -> None:
        self.killed = True
        self._alive = False
