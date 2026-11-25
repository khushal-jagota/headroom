"""Exercise the publisher's merge-tree check without granting this test release credentials."""
import io
import json
import unittest
from unittest.mock import patch
import zipfile

import check_promotion


class PromotionTest(unittest.TestCase):
    def prove(self, conclusion="success", tree="tree", attempt=2, expired=False, number=9, candidate_tree="tree", missing_package=False, replace_app=False):
        pull = {"number": 9, "merged_at": "now", "merge_commit_sha": "merge",
                "base": {"repo": {"full_name": "test/repo"}, "ref": "main"},
                "head": {"sha": "head", "ref": "feature", "repo": {"full_name": "test/repo"}}}
        run = {"id": 51, "run_attempt": 2, "conclusion": conclusion, "pull_requests": [],
               "head_branch": "feature", "head_repository": {"full_name": "test/repo"},
               "path": ".github/workflows/publish-typescript-release.yml"}
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("qualified.json", json.dumps({"source_sha":"a"*40, "tree":tree, "head":"head", "pr":number, "run":"51","attempt":str(attempt),
                "artifacts":{k:i+20 for i,k in enumerate(check_promotion.ARTIFACTS)}}))
        results = [
            [pull], {"workflow_runs": [run, {**run, "id": 50, "conclusion": "success"}]},
            {"artifacts": [{"name": "qualified-merge-tree-2", "id": 12, "expired": expired}] +
             [{"name": name, "id": i + 20 + (1 if replace_app and name == "panels-app" else 0), "expired": missing_package}
              for i, name in enumerate(check_promotion.ARTIFACTS.values())]},
            archive.getvalue(), {"tree": {"sha": "tree"}}, {"tree": {"sha": candidate_tree}},
        ]
        with patch.dict("os.environ", {"GITHUB_REPOSITORY": "test/repo", "GITHUB_SHA": "merge"}), \
             patch.object(check_promotion, "read_api", side_effect=results):
            return check_promotion.main()

    def test_matching_tree_can_publish(self):
        self.assertEqual(self.prove()["artifacts"]["app"], 20)

    def test_red_canceled_pending_stale_and_expired_cannot_publish(self):
        for case in [{"conclusion": "failure"}, {"conclusion": "cancelled"}, {"conclusion": None},
                     {"tree": "different"}, {"attempt": 1}, {"expired": True}, {"number": 8},
                     {"candidate_tree":"other"}, {"missing_package":True},{"replace_app":True}]:
            with self.subTest(case=case), self.assertRaises(ValueError):
                self.prove(**case)


if __name__ == "__main__":
    unittest.main()
