"""Cluster harvested bullets into canonical records (build step 2, part 1).

Every variant renames its projects, so the same work appears under several
headings. Titles alone therefore cannot identify a record -- two headings for the
same project may share only one token, while two unrelated projects may share
several generic ones.

Bullet *content* is far more distinctive: rare tokens (specific figures, unusual
verbs) pin a record regardless of what its heading says. We score on IDF-weighted
overlap of bullets, with the title as a weaker signal, seeding the canonical set
from the master document.

Emits a review report; the assignment is human-checked before the bank is built.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

from cvagent.harvest import ROOT, norm_key

HARVEST = ROOT / "data" / "harvest"

# The canonical record set is derived from whichever document is the master, so
# no project name is baked into the code. Nicer ids can be supplied privately in
# config/record_ids.json as {"<project title>": "<id>"}; unmapped titles get a
# slug. That file is gitignored -- project titles are personal content.
ID_OVERRIDES = ROOT / "config" / "record_ids.json"
MASTER_VARIANT = os.environ.get("CVAGENT_MASTER", "master")

SLUG_STOP = set("a an the and or of for at to in on with using via as & -".split())


def slugify(title: str, taken: set[str]) -> str:
    words = [w for w in norm_key(title).split() if w not in SLUG_STOP]
    slug = "_".join(words[:3]) or "record"
    if slug not in taken:
        return slug
    for n in range(2, 100):
        if (candidate := f"{slug}_{n}") not in taken:
            return candidate
    raise ValueError(f"cannot allocate id for {title!r}")


def load_id_overrides() -> dict[str, str]:
    if not ID_OVERRIDES.exists():
        return {}
    raw = json.loads(ID_OVERRIDES.read_text(encoding="utf-8"))
    return {norm_key(k): v for k, v in raw.items()}

STOP = set(
    "a an the and or of for to in on with at by from using via as is are was were "
    "using use used built build building designed design led own owns".split()
)

TITLE_WEIGHT = 0.35
BULLET_WEIGHT = 0.65
MATCH_THRESHOLD = 0.12


def tokens(text: str) -> set[str]:
    return {t for t in norm_key(text).split() if t not in STOP and len(t) > 1}


def idf_table(docs: list[set[str]]) -> dict[str, float]:
    n = len(docs)
    df = Counter(t for d in docs for t in d)
    return {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}


def weighted_overlap(a: set[str], b: set[str], idf: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    shared = sum(idf.get(t, 1.0) for t in a & b)
    total = sum(idf.get(t, 1.0) for t in a | b)
    return shared / total if total else 0.0


def load() -> dict:
    return json.loads((HARVEST / "inventory.json").read_text(encoding="utf-8"))


def project_groups(data: dict) -> list[dict]:
    """One group per (variant, project) pair, carrying its bullets."""
    groups: dict[tuple[str, str], dict] = {}
    for doc in data["documents"]:
        for item in doc["items"]:
            if not item["project"]:
                continue
            key = (doc["variant"], item["project"])
            g = groups.setdefault(key, {
                "variant": doc["variant"], "source": doc["source"], "title": item["project"],
                "section": item["section"], "employer": item["employer"], "bullets": [],
            })
            g["bullets"].append(item["text"])
    return list(groups.values())


def cluster(data: dict) -> dict:
    groups = project_groups(data)

    overrides = load_id_overrides()
    seeds: dict[str, dict] = {}
    for g in groups:
        if g["variant"] != MASTER_VARIANT:
            continue
        rid = overrides.get(norm_key(g["title"])) or slugify(g["title"], set(seeds))
        seeds[rid] = {
            "id": rid, "canonical_title": g["title"], "section": g["section"],
            "employer": g["employer"],
            "title_tokens": tokens(g["title"]),
            "bullet_tokens": set().union(*(tokens(b) for b in g["bullets"])) if g["bullets"] else set(),
        }

    if not seeds:
        raise SystemExit(
            f"no master document found (variant {MASTER_VARIANT!r}); "
            f"saw: {sorted({g['variant'] for g in groups})}. "
            "Set CVAGENT_MASTER to the variant that contains every record."
        )

    idf = idf_table([tokens(b) for g in groups for b in g["bullets"]])

    records: dict[str, dict] = {
        rid: {"id": rid, "canonical_title": s["canonical_title"], "section": s["section"],
              "employer": s["employer"], "titles": {}, "bullets": defaultdict(list),
              "assignments": []}
        for rid, s in seeds.items()
    }
    unmatched: list[dict] = []

    for g in groups:
        gt, gb = tokens(g["title"]), (set().union(*(tokens(b) for b in g["bullets"])) if g["bullets"] else set())
        scored = [
            (TITLE_WEIGHT * weighted_overlap(gt, s["title_tokens"], idf)
             + BULLET_WEIGHT * weighted_overlap(gb, s["bullet_tokens"], idf), rid)
            for rid, s in seeds.items()
        ]
        score, rid = max(scored)
        runner_up = sorted((s for s, _ in scored), reverse=True)[1] if len(scored) > 1 else 0.0

        if score < MATCH_THRESHOLD:
            unmatched.append({**g, "best_guess": rid, "score": round(score, 3)})
            continue

        rec = records[rid]
        rec["titles"].setdefault(g["title"], []).append(g["variant"])
        for b in g["bullets"]:
            rec["bullets"][norm_key(b)].append({"text": b, "variant": g["variant"]})
        rec["assignments"].append({
            "variant": g["variant"], "title": g["title"], "score": round(score, 3),
            "margin": round(score - runner_up, 3),
        })

    out = []
    for rec in records.values():
        bullets = [{"text": v[0]["text"], "variants": sorted({x["variant"] for x in v})}
                   for v in rec["bullets"].values()]
        bullets.sort(key=lambda b: (-len(b["variants"]), b["text"]))
        out.append({**rec, "bullets": bullets,
                    "titles": [{"title": t, "variants": v} for t, v in rec["titles"].items()],
                    "n_variants": len({a["variant"] for a in rec["assignments"]})})

    # Bullets attached to no project (achievements, summary-level lines).
    loose = [{"text": it["text"], "section": it["section"], "variant": doc["variant"]}
             for doc in data["documents"] for it in doc["items"] if not it["project"]]

    return {"records": out, "unmatched": unmatched, "loose": loose}


# --------------------------------------------------------------------------
# review report
# --------------------------------------------------------------------------

# Variants harvested from an older corpus generation, tagged by variant_name().
LEGACY_RE = re.compile(r"@h1$")


def is_legacy(variant: str) -> bool:
    return bool(LEGACY_RE.search(variant))


def report(clustered: dict) -> str:
    L: list[str] = [
        "# Bank Migration — Review Report",
        "",
        "Auto-clustered from the union of all 11 CV documents. **Review before the bank is built.**",
        "",
        "- Bullets are grouped by canonical record; each shows which variants use it.",
        "- Legacy variants (older corpus generation) may contain typos and stale "
        "figures. They are harvested for coverage but must not be promoted verbatim.",
        "- A bullet used by only one variant is candidate angle-specific phrasing.",
        "",
        "---",
        "",
    ]
    for rec in clustered["records"]:
        current = [b for b in rec["bullets"] if not all(is_legacy(v) for v in b["variants"])]
        L.append(f"## `{rec['id']}` — {rec['canonical_title']}")
        L.append("")
        L.append(f"*section:* `{rec['section']}` · *appears in* **{rec['n_variants']}** variants · "
                 f"*{len(rec['bullets'])} distinct bullets* ({len(current)} non-legacy)")
        L.append("")
        L.append("**Titles used:**")
        L.append("")
        for t in rec["titles"]:
            L.append(f"- `{'`, `'.join(t['variants'])}` → {t['title']}")
        L.append("")
        L.append("**Bullets:**")
        L.append("")
        for b in rec["bullets"]:
            tag = "".join(f"`{v}` " for v in b["variants"])
            L.append(f"- {b['text']}")
            L.append(f"  <br/>↳ {tag}")
        L.append("")
        weak = [a for a in rec["assignments"] if a["margin"] < 0.05]
        if weak:
            L.append("> ⚠️ **low-margin assignments** (verify these belong here):")
            for a in weak:
                L.append(f"> - `{a['variant']}` \"{a['title']}\" "
                         f"(score {a['score']}, margin {a['margin']})")
            L.append("")
        L.append("---")
        L.append("")

    if clustered["unmatched"]:
        L += ["## ⚠️ Unmatched project groups", "",
              "These did not match any canonical record above the threshold:", ""]
        for u in clustered["unmatched"]:
            L.append(f"- `{u['variant']}` **{u['title']}** "
                     f"(best guess `{u['best_guess']}`, score {u['score']})")
            for b in u["bullets"]:
                L.append(f"  - {b}")
        L.append("")

    loose = defaultdict(list)
    for x in clustered["loose"]:
        loose[x["section"]].append(x)
    if loose:
        L += ["## Section-level bullets (no project)", ""]
        for sec, items in sorted(loose.items()):
            L.append(f"### `{sec}`")
            L.append("")
            seen: dict[str, set[str]] = {}
            for it in items:
                seen.setdefault(norm_key(it["text"]), set()).add(it["variant"])
            texts = {norm_key(it["text"]): it["text"] for it in items}
            for k, variants in sorted(seen.items(), key=lambda kv: -len(kv[1])):
                L.append(f"- {texts[k]}")
                L.append(f"  <br/>↳ {''.join(f'`{v}` ' for v in sorted(variants))}")
            L.append("")
    return "\n".join(L)


def main() -> None:
    data = load()
    clustered = cluster(data)
    (HARVEST / "records_draft.json").write_text(
        json.dumps(clustered, indent=2, ensure_ascii=False, default=list), encoding="utf-8")
    (HARVEST / "records_review.md").write_text(report(clustered), encoding="utf-8")

    print(f"{'record':<24}{'variants':>9}{'bullets':>9}   titles")
    for r in clustered["records"]:
        print(f"{r['id']:<24}{r['n_variants']:>9}{len(r['bullets']):>9}   {len(r['titles'])}")
    print(f"\nunmatched groups: {len(clustered['unmatched'])}")
    print(f"loose bullets   : {len(clustered['loose'])}")
    weak = [(r["id"], a) for r in clustered["records"] for a in r["assignments"] if a["margin"] < 0.05]
    print(f"low-margin      : {len(weak)}")
    for rid, a in weak:
        print(f"   {rid:<22} {a['variant']:<14} {a['title'][:52]} (margin {a['margin']})")
    print(f"\nwrote {HARVEST/'records_review.md'}")


if __name__ == "__main__":
    main()
