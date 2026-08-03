# Architecture

## The constraint everything follows from

> The agent may re-select, re-order, and re-phrase content. It may **never** add a claim.

The interesting question is not whether to state that rule but **where to enforce it**.

An earlier version of this design enforced it at runtime: let an LLM rewrite bullets to
match the JD's vocabulary, then verify the output by string-similarity against the source.
That fails in both directions. Rewriting into JD vocabulary is the *point*, so every
legitimate rewrite lowers similarity — while a fabricated inflation (`1/8th` → `1/20th`,
`75% recall` → `95% recall`) barely moves it. The check is loose exactly where it must be
tight, and you end up tuning a threshold that trades false alarms on honest rewrites
against silent passes on the one failure the system exists to prevent.

A second attempt narrowed rewriting to a curated synonym lexicon. That was safer but
solved the wrong problem. Comparing real CV variants of the same project shows what
actually differs between them:

| | one variant | another |
|---|---|---|
| Title | "Sequential Demand Ranking for Inventory Allocation" | "Decisioning — Inventory Allocation Ranking" |
| Bullet | "**CNN+BiLSTM** ranking model cut review volume to 1/8th…" | "Built a **sequential ranking** model cutting review volume to 1/8th…" |
| Bullet | "**Own the production lifecycle**: … experiment tracking, drift monitoring" | "**Sustained decision quality**: … drift monitoring and output validation" |

The facts are constant. What changes is **which true thing you lead with**. That is not a
synonym swap, and a lexicon covers perhaps a tenth of it.

**So the enforcement moved to authoring time.** Each bullet is written or approved by a
human once, per angle. At runtime the agent only *selects*. Verification collapses to a
dictionary lookup — *is this exact string in the bank?* — with no threshold, no similarity
metric, and no LLM anywhere in the safety path. Strictly stronger, and far less machinery.

## The angle

The organizing unit. An angle is *what kind of value a role hires you to produce* —
orthogonal to seniority, domain, and job title. See the table in the README.

Variants are conventionally cut along role families (ML Engineer / Product DS / Research
DS). Measured against real job descriptions, that cut leaves whole classes of role with no
matching variant, because the market varies along angle instead. A JD is a **blend** of
angles with weights, not a single label.

**Angle resolution.** Mixing bullets from several angles inside one document produces an
incoherent voice, so:

> The dominant angle owns the document — summary, section headings, and skills taxonomy
> all come from it. Per record, phrasing falls back `dominant → strongest secondary →
> neutral`.

Every record carries a `neutral` phrasing (the master's), so no record is ever
unrenderable and the pipeline cannot dead-end on a missing variant.

## Bank schema

Four top-level objects: `identity`, `angles`, `records`, `achievements`.

```yaml
records:
  - id: demand_ranking
    employer: employer_a
    status: {state: live_in_production, since: 2026-01}
    facts: ["1/8th", "75% recall", "4M+ events"]   # numeric allowlist for verification

    variants:
      neutral:                                      # always present; universal fallback
        title: "Sequential Demand Ranking for Inventory Allocation"
        bullets:
          - "CNN+BiLSTM ranking model cut manual review volume to 1/8th while retaining 75% recall on out-of-time data"
          - "Own the production lifecycle: scheduled scoring over 4M+ events, {live_duration} of live drift monitoring"
      decision_science:
        title: "Decisioning — Inventory Allocation Ranking"
        bullets:
          - "Built a sequential ranking model cutting manual review volume to 1/8th while retaining 75% recall"

    short: {neutral: "...", decision_science: "..."}  # 1-line forms for the page budget
    serves: [production_ml, decision_science]         # which angles this record is shown for
```

Two deliberate choices:

**Numbers stay inline.** An earlier design templated every metric into a slot to stop
numbers migrating between projects. But bullets are copied verbatim and are atomic, so a
number *cannot* migrate — the slot machinery solved a problem verbatim copying already
solved, at the cost of an unreadable bank. `facts` is a flat allowlist instead:
verification extracts numeric tokens from each rendered bullet and asserts membership.

**Except derived values.** `{live_duration}` is computed by code from `status.since`.
Hardcoding "6+ months live" makes the claim rot silently as the calendar advances — a CV
that was accurate when written becomes false without anyone editing it.

`serves` governs *selection*; `variants` governs *phrasing*. Different decisions, kept
separate.

## Pipeline

**Stage 0 — apply decision.** Runs first, can terminate everything. Emits
`apply` / `apply_with_custom` / `dont_apply` with a gap list. Thresholds are calibrated
against a hand-scored baseline, not guessed. Declining an application with concrete
reasons is often worth more than a tailored CV.

**Stage 1 — fixed-variant path.** Rank existing variants by coverage, ship the winner with
a diff and the coverage report.

**Stage 2 — assembly.** Select records by **marginal** coverage gain against *uncovered*
requirements, not absolute relevance — otherwise you pick five records that all satisfy
the same requirement. Employers stay chronological; records reorder by relevance within
each employer.

*Page budget by measurement, not prediction.* Page fit depends on line wrapping, not
bullet counts. Render, compile, count pages, and walk a degradation ladder: demote the
lowest-ranked record to `short` → demote the next → drop the lowest-ranked `short` → drop
the next → trim skills → stop and hand to a human with the current best. Every step taken
is reported. A pipeline that can only hard-fail on page count dead-ends in production.

**Stage 3 — verification.** Deterministic code only; no LLM judges anything here.

- **Membership (exact).** Each rendered bullet, with derived slots reverted to
  placeholders, must be byte-identical to a bank string.
- **Numeric allowlist.** Every numeric token ∈ that record's `facts`.
- **Immutables.** Identity, employers, titles, dates byte-identical.
- **Layout.** Compiles clean, exactly one page. Set `\hyphenpenalty=10000` in the preamble
  rather than regexing extracted text for trailing hyphens — prevent, don't detect.
- **Coverage report.** Requirements met with evidence; requirements unmet, listed
  explicitly (this doubles as the interview-prep gap list); fallbacks and ladder steps used.

## Coverage engine

Replaces embedding cosine. Cosine between a JD and a CV is usable for *ranking* but not
for *thresholding* — the band drifts with document length and prose style, and a gate
needs an absolute cut that generalizes across JDs. Coverage is interpretable and
debuggable: when the gate misjudges, you can see *which requirement* it got wrong.
Embeddings survive as a retrieval step (shortlist candidate records per requirement before
adjudication), never as the decision.

Weighting: `kind` (must=3, nice=1) × `category` (core 1.0 · domain 0.8 · tooling 0.6 ·
credential 0.5 · **soft 0, dropped**). Soft requirements appear in every JD, discriminate
nothing, and would only inflate the denominator. An `any_of` group is weighted once as a
group, not per member.

```
score = Σ(weight × status) / Σ(weight)      met=1.0, partial=0.5, unmet=0
```

### The real risk: adjudication stability

`met/partial/unmet` is an LLM judgment and drifts between runs. If one JD scores 0.71 and
0.78 on two passes, the thresholds are decoration. Three mitigations, all required:

1. **Mandatory evidence** — a requirement can only be `met` if it names a bullet id. This
   single rule kills most inflated coverage.
2. **One requirement per call** — batching the full list drifts toward a uniform verdict.
3. **Measure it** — run the eval set three times and report agreement *before* trusting
   any threshold.

## Corpus harvest

Role-specific variants are **not** subsets of the master. Each is hand-rewritten and
carries material the master lacks, so migrating from the master alone silently discards
written work. The bank is built from the union of every document.

LaTeX sources are ground truth. PDF-only variants are parsed with font-weight awareness
because extracted PDF text has no blank line between a bullet and the next project title —
a text-only parser appends the title onto the preceding bullet and corrupts it. See the
README for the full list of cases handled.

Clustering scores IDF-weighted bullet overlap (0.65) above title similarity (0.35),
because every variant renames its projects: two headings for the same work may share a
single token, while unrelated projects share several generic ones. Bullet content — rare
figures, unusual verbs — pins a record regardless of its heading.

A useful side effect: harvesting the union surfaces **contradictions between variants**.
Numeric claims for the same record that disagree across documents are exactly the drift a
single source of truth is meant to eliminate, and they are easiest to find at migration
time.

## Application ledger

Append-only JSONL, written from the first apply decision.

Explicitly **not** an eval dataset. With a realistic application volume and outcomes
dominated by referrals, timing, and headcount, no signal about whether tailoring helped is
recoverable — claiming otherwise would be self-deception. It is kept for operational
memory: which variant a company received, what it claimed, whether you already applied,
and the per-application gap list for interview prep. `outcome` is optional; nothing should
depend on it.
