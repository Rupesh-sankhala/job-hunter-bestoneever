"""Bank v0 tests.

The bank is the safety boundary: at runtime the agent only selects from it, so
anything wrong here is wrong in every CV that follows. These tests assert the
two properties the whole design rests on -- nothing in the bank was invented,
and every figure a bullet states is in that record's allowlist.
"""

from __future__ import annotations

import pytest

from cvagent.bank import NUMERIC_RE, build_angles, build_records, extract_identity, facts_of, load_config
from cvagent.cluster import cluster
from cvagent.harvest import harvest, norm_key


@pytest.fixture(scope="module")
def cfg() -> dict:
    return load_config()


@pytest.fixture(scope="module")
def inv() -> dict:
    return harvest()


@pytest.fixture(scope="module")
def records(inv, cfg) -> list[dict]:
    recs, _ = build_records(cluster(inv), cfg["variants"])
    return recs


# --------------------------------------------------------------------------
# the core invariant: nothing is invented
# --------------------------------------------------------------------------


def test_every_bullet_traces_to_the_corpus(records, inv):
    """No bullet may exist in the bank that was not authored in some CV."""
    corpus = {norm_key(i["text"]) for d in inv["documents"] for i in d["items"]}
    for r in records:
        for cell, v in r["variants"].items():
            for bullet in v["bullets"]:
                assert norm_key(bullet) in corpus, f"{r['id']}/{cell}: {bullet[:70]!r}"


def test_every_title_traces_to_the_corpus(records, inv):
    corpus = {norm_key(p["title"]) for d in inv["documents"] for p in d["projects"]}
    for r in records:
        for cell, v in r["variants"].items():
            if v["title"]:
                assert norm_key(v["title"]) in corpus, f"{r['id']}/{cell}: {v['title']!r}"


def test_every_record_has_neutral_fallback(records):
    """Resolution falls back to `neutral`, so it must always exist -- otherwise a
    record becomes unrenderable for some angle."""
    for r in records:
        assert "neutral" in r["variants"], f"{r['id']} has no neutral phrasing"
        assert r["variants"]["neutral"]["bullets"]


# --------------------------------------------------------------------------
# numeric allowlist
# --------------------------------------------------------------------------


def test_every_figure_in_a_bullet_is_in_the_records_facts(records):
    """Stage 3 checks rendered numbers against `facts`. If a figure a bullet
    actually states is missing from the allowlist, verification would reject the
    bank's own content."""
    for r in records:
        allowed = set(r["facts"])
        for cell, v in r["variants"].items():
            for bullet in v["bullets"]:
                for fig in NUMERIC_RE.findall(bullet):
                    if fig.strip():
                        assert fig.strip() in allowed, (
                            f"{r['id']}/{cell}: {fig!r} not in facts {sorted(allowed)}"
                        )


def test_fractions_are_not_shredded():
    """'1/10th' must survive whole; splitting it into '1' and '10' would let a
    corrupted ratio pass verification."""
    assert facts_of(["cut review volume to 1/8th while retaining 75% recall"]) == [
        "1/8th",
        "75%",
    ]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("scoring 200M+ accounts monthly", ["200M+"]),
        ("generating $4M+ annual revenue", ["$4M+"]),
        ("engagement 140% and capture 3×", ["140%", "3×"]),
        ("9+ months of live drift monitoring", ["9+ months"]),
        ("~5.2k LOC", ["5.2k"]),
        ("for 40+ partners across 20+ countries", ["40+", "20+"]),
        ("no figures at all here", []),
    ],
)
def test_numeric_extraction(text, expected):
    assert sorted(facts_of([text])) == sorted(expected)


# --------------------------------------------------------------------------
# identity
# --------------------------------------------------------------------------


def test_identity_is_clean(inv):
    ident = extract_identity(inv)
    assert ident["name"] and "\\" not in ident["name"], ident["name"]
    assert "[2pt]" not in ident["name"], "LaTeX spacing leaked into the name"
    assert len(ident["name"].split()) <= 6
    assert ident["employers"] and ident["education"]
    for entry in ident["employers"] + ident["education"]:
        assert entry["label"] and entry["dates"]
        assert "\\" not in entry["label"]


def test_identity_contact_is_split(inv):
    contact = extract_identity(inv)["contact"]
    assert len(contact) >= 3
    assert any("@" in c for c in contact), "expected an email"
    assert not any(c.startswith("|") or "$" in c for c in contact)


# --------------------------------------------------------------------------
# angles
# --------------------------------------------------------------------------


def test_authored_angles_have_a_voice(inv, cfg):
    """An angle backed by a real variant must carry a summary and skills, since
    the dominant angle supplies the whole document's voice."""
    angles = build_angles(inv, cfg)
    for key, a in angles.items():
        if a["source_variant"] is None:
            continue
        assert a["summary"], f"{key} has no summary"
        assert a["skills"], f"{key} has no skills taxonomy"


def test_unauthored_angles_are_explicit(inv, cfg):
    """The gaps must be visible, not silently absent."""
    angles = build_angles(inv, cfg)
    for name in cfg["unauthored"]:
        assert name in angles
        assert angles[name]["summary"] is None
        assert angles[name].get("_todo")


def test_domain_qualified_cells_do_not_collapse(records, cfg):
    """Two variants may share an angle and differ only in domain. If domain
    collapsed into angle, one variant's phrasing would overwrite the other's."""
    by_angle: dict[str, set[str]] = {}
    for v in cfg["variants"].values():
        key = v["angle"] if not v.get("domain") else f"{v['angle']}/{v['domain']}"
        by_angle.setdefault(v["angle"], set()).add(key)
    shared = {a: keys for a, keys in by_angle.items() if len(keys) > 1}
    if not shared:
        pytest.skip("corpus has no angle served by more than one domain")
    for angle, keys in shared.items():
        for r in records:
            present = [k for k in keys if k in r["variants"]]
            if len(present) > 1:
                texts = [tuple(r["variants"][k]["bullets"]) for k in present]
                assert len(set(texts)) == len(texts), f"{r['id']}: {angle} domains collapsed"


def test_serves_lists_only_angles_with_content(records):
    for r in records:
        for angle in r["serves"]:
            assert any(c.split("/")[0] == angle for c in r["variants"]), (
                f"{r['id']} claims to serve {angle} with no bullets"
            )
