"""Shadow comparisons cannot qualify a replacement provider or write state."""

import unittest

from radar.sql_connectome_shadow import compare_identity_shadow


class ShadowIdentityTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

    def reader(self, rows):
        def read(sql, parameters):
            self.calls.append((sql, parameters))
            return rows

        return read

    def test_matching_readback_is_still_unqualified(self):
        report = compare_identity_shadow(
            [{"identity_id": "Radar", "display_name": "Radar"}],
            self.reader([{"identity_id": "radar", "display_name": "Radar"}]),
            reference_source_commit="9" * 40,
            candidate_source_commit="a" * 40,
        )
        self.assertEqual(report.status, "MATCH_UNQUALIFIED")
        self.assertFalse(report.canonical)
        self.assertFalse(report.cutover_authorized)
        self.assertIn("identity_only", report.unverified_capabilities)
        self.assertEqual(len(self.calls), 1)
        sql, args = self.calls[0]
        self.assertTrue(sql.lstrip().lower().startswith("select "))
        self.assertNotIn("insert ", sql.lower())
        self.assertNotIn("update ", sql.lower())
        self.assertNotIn("delete ", sql.lower())
        self.assertEqual(args, (2,))

    def test_missing_and_extra_candidate_identity_deny_match(self):
        expected = [{"identity_id": "a", "display_name": "A"}]
        for rows in ([], [{"identity_id": "b", "display_name": "B"}], [
            {"identity_id": "a", "display_name": "A"},
            {"identity_id": "b", "display_name": "B"},
        ]):
            with self.subTest(rows=rows):
                result = compare_identity_shadow(
                    expected, self.reader(rows),
                    reference_source_commit="9" * 40,
                    candidate_source_commit="b" * 40,
                )
                self.assertEqual(result.status, "MISMATCH")
                self.assertFalse(result.cutover_authorized)

    def test_duplicate_or_malformed_candidate_is_unknown(self):
        expected = [{"identity_id": "a", "display_name": "A"}]
        bad = (
            [{"identity_id": "a", "display_name": "A"}, {"identity_id": "A", "display_name": "A"}],
            [{"identity_id": "a", "display_name": None}],
            "not-a-row-list",
        )
        for rows in bad:
            with self.subTest(rows=rows):
                result = compare_identity_shadow(
                    expected, self.reader(rows),
                    reference_source_commit="9" * 40,
                    candidate_source_commit="c" * 40,
                )
                self.assertEqual(result.status, "UNKNOWN")

    def test_reader_failure_is_unknown_not_match(self):
        def unavailable(_sql, _params):
            raise PermissionError("read role revoked")

        result = compare_identity_shadow(
            [{"identity_id": "a", "display_name": "A"}],
            unavailable,
            reference_source_commit="9" * 40,
            candidate_source_commit="d" * 40,
        )
        self.assertEqual(result.status, "UNKNOWN")

    def test_invalid_source_pin_and_expected_shape_fail_before_reader(self):
        for expected, pin in (
            ([{"identity_id": "a", "display_name": "A"}], "main"),
            ([
                {"identity_id": "a", "display_name": "A"},
                {"identity_id": "A", "display_name": "A"},
            ], "a" * 40),
            ([{"identity_id": "a", "display_name": ""}], "a" * 40),
        ):
            with self.subTest(expected=expected, pin=pin):
                report = compare_identity_shadow(
                    expected, self.reader([]),
                    reference_source_commit="9" * 40,
                    candidate_source_commit=pin,
                )
                self.assertEqual(report.status, "UNKNOWN")
                self.assertFalse(report.cutover_authorized)
        self.assertEqual(self.calls, [])

        result = compare_identity_shadow(
            [{"identity_id": "a", "display_name": "A"}],
            self.reader([]),
            reference_source_commit="main",
            candidate_source_commit="a" * 40,
        )
        self.assertEqual(result.reason, "INVALID_SOURCE_PIN")
        self.assertEqual(self.calls, [])

    def test_expected_identity_content_never_reaches_sql_text(self):
        hostile = "radar'); delete from radar.identities; --"
        report = compare_identity_shadow(
            [{"identity_id": hostile, "display_name": "Hostile"}],
            self.reader([{"identity_id": hostile, "display_name": "Hostile"}]),
            reference_source_commit="9" * 40,
            candidate_source_commit="a" * 40,
        )
        self.assertEqual(report.status, "MATCH_UNQUALIFIED")
        self.assertNotIn(hostile, self.calls[0][0])
        self.assertEqual(self.calls[0][1], (2,))

    def test_candidate_claims_cannot_approve_cutover(self):
        report = compare_identity_shadow(
            [{"identity_id": "a", "display_name": "A"}],
            self.reader([{
                "identity_id": "a", "display_name": "A",
                "canonical": True, "cutover_authorized": True,
            }]),
            reference_source_commit="9" * 40,
            candidate_source_commit="f" * 40,
        )
        self.assertEqual(report.status, "MATCH_UNQUALIFIED")
        self.assertFalse(report.canonical)
        self.assertFalse(report.cutover_authorized)


if __name__ == "__main__":
    unittest.main()
