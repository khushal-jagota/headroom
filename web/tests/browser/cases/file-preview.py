# ruff: noqa: E501
"""Independent browser case, extracted without assertion changes."""


def run(browser, urls):

    page = browser.new_page(viewport={"width": 600, "height": 400})
    page.set_default_timeout(5_000)

    reads = []
    page.on(
        "request",
        lambda request: (
            reads.append(request.url) if "/files/tickets/t_probe01/note.md" in request.url else None
        ),
    )
    page.goto([__file__, *urls][1], wait_until="domcontentloaded")

    page.locator("[data-file-preview]").first.wait_for()
    page.wait_for_function("""() => {
      const line = document.querySelector('[data-probe="missing"] .quiet-line');
      return line !== null && line.textContent.trim().length > 0;
    }""")
    assert page.locator('[data-probe="missing"] .quiet-line').inner_text() == "file fetch failed"
    assert page.locator('[data-probe="missing"] img').count() == 0

    # All three draw the note.
    page.wait_for_function("""() => {
      const bodies = document.querySelectorAll('[data-probe="shared"] .file-preview-document-body');
      return bodies.length === 3
        && [...bodies].every((body) => body.textContent.includes("The shared note"));
    }""")
    page.wait_for_timeout(500)
    assert len(reads) == 1, f"three previews of one file made {len(reads)} requests: {reads}"

    pass  # Shared runner owns the browser lifecycle.

    print("file-preview-browser.test.mjs: all assertions passed")
