"""An old acknowledgment cannot authorize a newly changed native contract."""

import json
from pathlib import Path

import pytest
from scripts import check_ios_compatibility as checks


def test_review_is_bound_to_contract_and_minimum(tmp_path: Path) -> None:
    policy = tmp_path / "src/planner/ios_compatibility/policy.py"
    policy.parent.mkdir(parents=True)
    policy.write_text("MINIMUM_SUPPORTED_BUILD = 1\n")
    server = tmp_path / "src/planner/core/server.py"
    server.parent.mkdir(parents=True)
    server.write_text("# original routes\n")
    review_file = tmp_path / checks.REVIEW
    review_file.parent.mkdir()
    review = {
        "contract_sha256": checks.contract_digest(tmp_path),
        "oldest_supported_build": 1,
        "decision": "compatible",
        "evidence": ("Host fixture checks prove native contracts. "
                     "Signed-device check remains external."),
    }
    review_file.write_text(json.dumps(review))
    checks.check(tmp_path)
    server.write_text("# changed routes\n")
    with pytest.raises(ValueError, match="contracts changed"):
        checks.check(tmp_path)
    review["contract_sha256"] = checks.contract_digest(tmp_path)
    review_file.write_text(json.dumps(review))
    checks.check(tmp_path)
    projection = tmp_path / "src/planner/core/projection.py"
    projection.write_text("# changed serialization outside api.py\n")
    with pytest.raises(ValueError, match="contracts changed"):
        checks.check(tmp_path)
    review["contract_sha256"] = checks.contract_digest(tmp_path)
    review["oldest_supported_build"] = 2
    review_file.write_text(json.dumps(review))
    with pytest.raises(ValueError, match="oldest supported build"):
        checks.check(tmp_path)
