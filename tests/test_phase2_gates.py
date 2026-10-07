import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from phase2.extraction import flag_fields, ready_to_draft
from phase2.checkpoint import require_approval, ApprovalRequired


class Gates(unittest.TestCase):
    def test_low_confidence_and_missing_are_flagged(self):
        fs = flag_fields([{"name": "tag", "value": "V-210", "confidence": 0.99},
                          {"name": "thk", "value": "4.8", "confidence": 0.5},
                          {"name": "date", "value": "", "confidence": 0.99},
                          {"name": "insp", "value": "AB", "confidence": None}])
        self.assertEqual([f["needs_review"] for f in fs], [False, True, True, True])

    def test_draft_blocked_until_confirmed(self):
        fs = flag_fields([{"name": "thk", "value": "4.8", "confidence": 0.5}])
        self.assertFalse(ready_to_draft(fs))
        fs[0]["confirmed"] = True
        self.assertTrue(ready_to_draft(fs))

    def test_export_refused_without_approval(self):
        with self.assertRaises(ApprovalRequired):
            require_approval({})
        require_approval({"approved_by": "j.doe"})


if __name__ == "__main__":
    unittest.main()
