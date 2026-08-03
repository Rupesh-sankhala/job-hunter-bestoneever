"""Clustering tests.

The fixture variant renames every project and rewrites every bullet, so these
tests check the thing that actually matters: that renamed headings still map
back to the same canonical record.
"""

from __future__ import annotations

import pytest

from cvagent.cluster import (
    cluster,
    idf_table,
    is_legacy,
    slugify,
    tokens,
    weighted_overlap,
)
from cvagent.harvest import harvest


@pytest.fixture(scope="module")
def clustered() -> dict:
    return cluster(harvest())


def test_every_group_is_assigned(clustered):
    assert clustered["unmatched"] == [], (
        "unmatched project groups: "
        f"{[(u['variant'], u['title'], u['score']) for u in clustered['unmatched']]}"
    )


def test_records_are_seeded_from_master(clustered):
    assert len(clustered["records"]) >= 8
    assert all(r["canonical_title"] for r in clustered["records"])


def test_renamed_projects_cluster_together(clustered):
    """The variant renames every project; they must not become new records."""
    multi = [r for r in clustered["records"] if r["n_variants"] > 1]
    assert multi, "no record was matched across more than one variant"
    for rec in multi:
        assert len(rec["titles"]) > 1, f"{rec['id']} matched but kept only one title"


def test_assignments_are_confident(clustered):
    """A correct clustering should not rest on near-ties."""
    weak = [
        (r["id"], a["variant"], a["title"], a["margin"])
        for r in clustered["records"]
        for a in r["assignments"]
        if a["margin"] < 0.02
    ]
    assert not weak, f"low-margin assignments: {weak}"


def test_bullet_variants_are_tracked(clustered):
    """Each distinct phrasing records which variants use it -- the input to angle
    tagging."""
    for rec in clustered["records"]:
        for bullet in rec["bullets"]:
            assert bullet["variants"], bullet["text"]
            assert bullet["text"].strip()


def test_legacy_variants_are_flagged():
    assert is_legacy("legacy@h1")
    assert not is_legacy("product@h2")
    assert not is_legacy("master")


# --------------------------------------------------------------------------
# scoring primitives
# --------------------------------------------------------------------------


def test_slugify_is_stable_and_unique():
    taken: set[str] = set()
    a = slugify("Sequential Demand Ranking for Inventory Allocation", taken)
    taken.add(a)
    b = slugify("Sequential Demand Ranking for Inventory Allocation", taken)
    assert a != b and b.startswith(a)


def test_slugify_drops_stopwords():
    assert slugify("Anomaly Detection for Warehouse Telemetry", set()) == (
        "anomaly_detection_warehouse"
    )


def test_weighted_overlap_rewards_rare_tokens():
    """Rare tokens must dominate: two projects sharing only generic words should
    score below two sharing a distinctive one."""
    docs = [tokens(t) for t in [
        "built a model for risk", "built a pipeline for risk", "built a report for risk",
        "circumventing declined authorizations",
    ]]
    idf = idf_table(docs)
    generic = weighted_overlap(tokens("built a model for risk"),
                               tokens("built a report for risk"), idf)
    distinctive = weighted_overlap(tokens("merchants circumventing declined authorizations"),
                                   tokens("detecting circumventing declined authorizations"), idf)
    assert distinctive > generic


def test_weighted_overlap_handles_empty():
    assert weighted_overlap(set(), {"a"}, {}) == 0.0
