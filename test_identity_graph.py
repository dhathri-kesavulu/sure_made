import hashlib
import unittest
from datetime import datetime, timezone

from ghost.identity_graph import Confidence, IdentityGraph, Kind, node, parse_ts


def T(y, m=1, d=1):
    return datetime(y, m, d, tzinfo=timezone.utc)


class EmailChange(unittest.TestCase):
    def setUp(self):
        g = self.g = IdentityGraph()
        g.add_ownership("u1", Kind.EMAIL, "old@x.com", "ids.csv:row 2", valid_from=T(2020))
        g.change_email("u1", "old@x.com", "new@x.com", T(2023), "audit.csv:row 9")

    def test_both_emails_found_from_user(self):
        r = self.g.traverse(node(Kind.USER_ID, "u1"))
        ids = r.identifiers_for("u1", [Kind.EMAIL])
        self.assertIn(node(Kind.EMAIL, "old@x.com"), ids)
        self.assertIn(node(Kind.EMAIL, "new@x.com"), ids)

    def test_intervals_are_split_at_change(self):
        owners_old = self.g.resolve_owners(Kind.EMAIL, "old@x.com")
        self.assertEqual(owners_old["u1"], [(T(2020), T(2023))])
        self.assertEqual(self.g.resolve_owners(Kind.EMAIL, "new@x.com")["u1"], [(T(2023), None)])

    def test_old_email_reaches_user_and_new_email(self):  # multi-hop via the user anchor
        r = self.g.traverse(node(Kind.EMAIL, "OLD@x.com "))   # normalization
        self.assertIn(node(Kind.EMAIL, "new@x.com"), r.identifiers_for("u1"))

    def test_point_in_time_excludes_other_period(self):
        r = self.g.traverse(node(Kind.USER_ID, "u1"), at=T(2021))
        ids = r.identifiers_for("u1", [Kind.EMAIL])
        self.assertIn(node(Kind.EMAIL, "old@x.com"), ids)
        self.assertNotIn(node(Kind.EMAIL, "new@x.com"), ids)

    def test_change_without_prior_row_still_closes_interval(self):
        g = IdentityGraph()
        g.change_email("u9", "a@x.com", "b@x.com", T(2022), "audit.csv:row 3")
        self.assertEqual(g.resolve_owners(Kind.EMAIL, "a@x.com")["u9"], [(None, T(2022))])


class IdentifierReuse(unittest.TestCase):
    def setUp(self):
        g = self.g = IdentityGraph()
        g.add_ownership("alice", Kind.EMAIL, "shared@x.com", "ids:2", T(2019), T(2021))
        g.add_ownership("bob", Kind.EMAIL, "shared@x.com", "ids:3", T(2022), None)
        g.add_ownership("alice", Kind.PHONE, "+1 555 0001", "ids:2")
        g.add_ownership("bob", Kind.PHONE, "+1 555 0002", "ids:3")

    def test_no_conflict_when_periods_disjoint(self):
        self.assertEqual(self.g.conflicts, [])

    def test_time_bound_query_does_not_merge_users(self):
        r = self.g.traverse(node(Kind.EMAIL, "shared@x.com"), at=T(2020))
        self.assertEqual(r.users, {"alice"})
        self.assertNotIn(node(Kind.PHONE, "+15550002"), r.identifiers_for("bob"))

    def test_later_period_resolves_to_other_user(self):
        r = self.g.traverse(node(Kind.EMAIL, "shared@x.com"), at=T(2023))
        self.assertEqual(r.users, {"bob"})

    def test_unbounded_seed_keeps_users_separate(self):
        r = self.g.traverse(node(Kind.EMAIL, "shared@x.com"))
        self.assertEqual(r.users, {"alice", "bob"})
        self.assertNotIn(node(Kind.PHONE, "+15550002"), r.identifiers_for("alice"))
        self.assertNotIn(node(Kind.PHONE, "+15550001"), r.identifiers_for("bob"))

    def test_gap_between_owners_resolves_to_nobody(self):
        self.assertEqual(self.g.resolve_owners(Kind.EMAIL, "shared@x.com", at=datetime(2021, 6, 1, tzinfo=timezone.utc)), {})


class Conflicts(unittest.TestCase):
    def setUp(self):
        g = self.g = IdentityGraph()
        g.add_ownership("u1", Kind.EMAIL, "dup@x.com", "ids:2", T(2020), None)
        g.add_ownership("u2", Kind.EMAIL, "dup@x.com", "ids:3", T(2021), None)
        g.add_ownership("u1", Kind.PHONE, "111", "ids:2")

    def test_overlap_detected_and_reported(self):
        c = self.g.conflicts
        self.assertEqual(len(c), 1)
        self.assertEqual(set(c[0].users), {"u1", "u2"})
        self.assertEqual(c[0].overlap, (T(2021), None))

    def test_conflicting_edges_not_traversed(self):
        r = self.g.traverse(node(Kind.EMAIL, "dup@x.com"))
        self.assertEqual(r.users, set())
        self.assertTrue(r.is_ambiguous)
        self.assertIn("QUARANTINED", {reason for _, reason in r.skipped})

    def test_non_conflicting_identifiers_of_same_user_still_resolve(self):
        r = self.g.traverse(node(Kind.USER_ID, "u1"))
        self.assertIn(node(Kind.PHONE, "111"), r.identifiers_for("u1"))
        self.assertNotIn(node(Kind.EMAIL, "dup@x.com"), r.identifiers_for("u1"))

    def test_same_user_duplicate_rows_are_not_a_conflict(self):
        g = IdentityGraph()
        g.add_ownership("u1", Kind.EMAIL, "a@x.com", "ids:2", T(2020))
        g.add_ownership("u1", Kind.EMAIL, "a@x.com", "audit:5", T(2020))
        self.assertEqual(g.conflicts, [])


class MultiHopAndProvenance(unittest.TestCase):
    def test_hash_to_historical_email_to_user_to_current_email(self):
        g = IdentityGraph()
        g.add_ownership("u1", Kind.EMAIL, "first@x.com", "ids:2", T(2018))
        g.change_email("u1", "first@x.com", "second@x.com", T(2020), "audit:4")
        g.change_email("u1", "second@x.com", "third@x.com", T(2022), "audit:8")
        h = hashlib.sha256(b"first@x.com").hexdigest()
        r = g.traverse(node(Kind.EMAIL_HASH, h))
        ids = r.identifiers_for("u1", [Kind.EMAIL])
        self.assertEqual({v for _, v in ids}, {"first@x.com", "second@x.com", "third@x.com"})

    def test_provenance_preserved_on_every_hop(self):
        g = IdentityGraph()
        g.add_ownership("u1", Kind.EMAIL, "a@x.com", "ids:2")
        g.change_email("u1", "a@x.com", "b@x.com", T(2021), "audit:7")
        r = g.traverse(node(Kind.EMAIL, "a@x.com"))
        d = next(d for d in r.discoveries if d.node == node(Kind.EMAIL, "b@x.com"))
        prov = g.provenance(d)
        self.assertTrue(len(prov) >= 2)
        self.assertTrue(all(srcs for _, srcs in prov))

    def test_edge_without_source_rejected(self):
        with self.assertRaises(ValueError):
            IdentityGraph().add_ownership("u1", Kind.EMAIL, "a@x.com", "")

    def test_alias_chain_two_users(self):
        g = IdentityGraph()
        g.add_ownership("u1", Kind.EMAIL, "a@x.com", "ids:2")
        g.add_ownership("u2", Kind.EMAIL, "b@x.com", "ids:3")
        g.add_alias("u1", "u2", "audit:11", valid_from=T(2021))
        r = g.traverse(node(Kind.USER_ID, "u1"))
        self.assertEqual(r.users, {"u1", "u2"})


class NoSimilarityMerging(unittest.TestCase):
    def test_unverified_edge_is_a_lead_not_a_link(self):
        g = IdentityGraph()
        g.add_ownership("u1", Kind.EMAIL, "a@x.com", "ids:2")
        g.add_ownership("u2", Kind.EMAIL, "a@x.com", "fuzzy:1", confidence=Confidence.UNVERIFIED)
        r = g.traverse(node(Kind.EMAIL, "a@x.com"))
        self.assertEqual(r.users, {"u1"})
        self.assertIn("UNVERIFIED", {reason for _, reason in r.skipped})

    def test_plus_tags_and_dots_are_not_collapsed(self):
        g = IdentityGraph()
        g.add_ownership("u1", Kind.EMAIL, "jane.doe@x.com", "ids:2")
        g.add_ownership("u2", Kind.EMAIL, "janedoe@x.com", "ids:3")
        self.assertEqual(g.traverse(node(Kind.EMAIL, "janedoe@x.com")).users, {"u2"})


class Timestamps(unittest.TestCase):
    def test_naive_rejected_by_default(self):
        with self.assertRaises(ValueError):
            parse_ts("2024-01-01T00:00:00")

    def test_naive_accepted_when_declared_utc(self):
        self.assertEqual(parse_ts("2024-01-01T00:00:00", assume_utc=True), T(2024))

    def test_offsets_normalized_to_utc(self):
        self.assertEqual(parse_ts("2024-01-01T05:30:00+05:30"), T(2024))


if __name__ == "__main__":
    unittest.main()
