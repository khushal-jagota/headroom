from pathlib import Path

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--compare-list", action="append", default=[])
    parser.addoption("--compare-report-dir", default=None)
    parser.addoption("--compare-right-module", default=None)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "comparison_list" in metafunc.fixturenames:
        names = metafunc.config.getoption("--compare-list")
        paths = (
            [Path(p) for p in names]
            if names
            else sorted((Path(__file__).parent / "lists").glob("*.json"))
        )
        metafunc.parametrize(
            "comparison_list",
            [
                pytest.param(
                    path,
                    marks=pytest.mark.xfail(
                        run=False,
                        reason=(
                            "expected-open P3 (#916): local child custody rows and linked "
                            "occurrence identities await supervision"
                        ),
                    ),
                )
                if path.name == "unit-15.queue.json"
                else path
                for path in paths
            ],
            ids=[p.stem for p in paths],
        )
