"""The values and the handle the e2e fixtures and the e2e tests both build on.

Split out of ``conftest.py`` so that a test needing one of these imports an ordinary
module. pytest loads ``conftest.py`` under the bare name ``conftest``, so a test saying
``from conftest import ...`` depends on pytest having put this directory on ``sys.path``
— which reads as a missing module to every other tool, and which would load a second copy
of the module under a second name if it were imported the normal way. A plain module has
one name and one copy, from anywhere.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import Browser, Page
from tests.support.server import (
    BOOT_BUDGET_S as BOOT_BUDGET_S,
)
from tests.support.server import (
    FAKE_NOW as FAKE_NOW,
)
from tests.support.server import (
    PLAN_BIN as PLAN_BIN,
)
from tests.support.server import (
    REPO_ROOT as REPO_ROOT,
)
from tests.support.server import (
    WAIT_MS as WAIT_MS,
)
from tests.support.server import (
    ApiHelper as ApiHelper,
)
from tests.support.server import (
    JsonObject as JsonObject,
)
from tests.support.server import (
    ServerHandle as ServerHandle,
)
from tests.support.server import (
    _append_rows as _append_rows,
)


def open_status_group(page: Page, key: str) -> None:
    """Open the Workspace status group named by ``key``.

    Which groups arrive open is a product choice, and no test here owns it. A test that
    must read a row inside a group opens that group itself, and says so by calling this.
    """
    group = f'[data-bucket-key="{key}"]'
    page.wait_for_selector(group, timeout=WAIT_MS)
    if page.get_attribute(group, "open") is None:
        page.click(f"{group} > summary")
    page.wait_for_function(
        "selector => document.querySelector(selector)?.open === true",
        arg=group,
        timeout=WAIT_MS,
    )


def capture_success(page: Page, path: str | Path) -> None:
    """Capture a review image only when explicitly requested."""
    if output := os.environ.get("PANELS_UI_PROOF_DIR"):
        target = Path(output) / Path(path).name
        target.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(target), full_page=True, animations="disabled")


@contextmanager
def browser_failure_evidence(browser: Browser, label: str) -> Iterator[None]:
    """Retain evidence before a standalone frontend browser exits on failure."""
    try:
        yield
    except Exception:
        folder = (
            Path(os.environ.get("PANELS_E2E_FAILURE_DIR", "data/verification/browser-failure"))
            / label
        )
        folder.mkdir(parents=True, exist_ok=True)
        for context_index, context in enumerate(browser.contexts):
            for page_index, page in enumerate(context.pages):
                stem = folder / f"context-{context_index}-page-{page_index}"
                try:
                    page.screenshot(
                        path=str(stem.with_suffix(".png")), full_page=True, timeout=5000
                    )
                    stem.with_suffix(".html").write_text(page.content())
                except Exception as error:
                    stem.with_suffix(".capture-error.txt").write_text(str(error))
            try:
                context.tracing.stop(path=str(folder / f"context-{context_index}-trace.zip"))
            except Exception:
                pass
        raise
