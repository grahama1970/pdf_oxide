"""Promotion-ledger applier — B.1.1 of plan-3623092 v6.

Reads a promotion ledger (the source of truth) and applies its entries to a
list of extracted elements. The preset JSON is the COMPILED output of this
applier; see compiler.py.

Schema v2 (per WebGPT review 2026-05-11 night):
  - Top-level: {schema_version, document_family, preset_path, entries[]}
  - Entry common fields: entry_id, category, rule_class, fix_target,
    core_component?, status, revision, evidence_and_intent, evidence, guards,
    verification
  - Rule kinds: block_type_map, text_classifier_rule, structural_grouping_rule,
    bbox_refinement_rule

Apply modes:
  - "release" (default) — consumes entries with status in {verified, closed}
  - "staging" — consumes entries with status in {applied, verified, closed}
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


# Status sets per mode (S4 of v6_ledger_schema_v2).
_RELEASE_STATUSES = frozenset({"verified", "closed"})
_STAGING_STATUSES = frozenset({"applied", "verified", "closed"})

# Apply order — hardcoded for v1 (Q7 of v6_ledger_schema_v2).
_APPLY_ORDER = ("type_remap", "semantic_enrichment", "bbox_refinement", "structural_grouping")

# Rule kinds the applier supports.
_RULE_KINDS = frozenset({
    "block_type_map",
    "text_classifier_rule",
    "structural_grouping_rule",
    "bbox_refinement_rule",
})


class LedgerError(Exception):
    """Base for ledger contract violations."""


class LedgerConflictError(LedgerError):
    """Two entries with the same rule kind both match the same input.

    Per S8 / Q8 of v6 schema:
      - block_type_map conflicts (two rules on same source_type) raise at LOAD time
      - text_classifier_rule overlaps are not raised; they emit
        `multiple_rules_matched_warning` traces during apply.
    """


class LedgerSchemaError(LedgerError):
    """Entry violates schema v2 (missing required fields, invalid status, etc.)."""


@dataclass
class ApplierConfig:
    mode: str = "release"   # "release" | "staging"
    trace: bool = False
    warnings: list[str] = field(default_factory=list)
    rule_fired_counts: dict[str, int] = field(default_factory=dict)
    # Overlap tracing (B.2.1): per-write log so reviewers can see which rules
    # wrote which fields on which elements. Key: (element_id, field). Value:
    # list of (entry_id, new_value) tuples in apply order.
    field_writes: dict[tuple[str, str], list[tuple[str, Any]]] = field(default_factory=dict)
    # Specificity tracking for conflict resolution: (element_id, field) → priority
    # of the entry that last successfully wrote. A later, BROADER (higher
    # priority value) entry that conflicts is silently skipped (specificity
    # ordering working as designed). Equal or stricter conflicts still raise.
    field_writer_specificity: dict[tuple[str, str], int] = field(default_factory=dict)
    # Track the entry_id that wrote each (element_id, field) so structured
    # specificity_skip events can identify the prior writer (B.3 post-review).
    field_writer_entry_id: dict[tuple[str, str], str] = field(default_factory=dict)
    # Structured specificity skip events (B.3 post-review per WebGPT): when a
    # broader rule defers to a strictly more-specific prior writer, an event
    # is recorded here for verifier-side allowlist enforcement. Replaces the
    # generic `warnings` channel for these events.
    specificity_skips: list[dict[str, Any]] = field(default_factory=list)


def _entry_active(entry: dict[str, Any], mode: str) -> bool:
    statuses = _RELEASE_STATUSES if mode == "release" else _STAGING_STATUSES
    return entry.get("status") in statuses


def _filter_active_entries(
    ledger: dict[str, Any], mode: str
) -> list[dict[str, Any]]:
    """Return the latest-revision active entry per entry_id, skipping superseded ones."""
    entries: list[dict[str, Any]] = ledger.get("entries") or []

    # Track superseded entry_ids (any entry's `supersedes` list disables those).
    superseded: set[str] = set()
    for e in entries:
        for sid in e.get("supersedes") or []:
            superseded.add(sid)

    # Group by entry_id; keep highest revision per id that is also active.
    by_id: dict[str, dict[str, Any]] = {}
    for e in entries:
        eid = e.get("entry_id")
        if not eid or eid in superseded:
            continue
        if not _entry_active(e, mode):
            continue
        prev = by_id.get(eid)
        if prev is None or int(e.get("revision", 1)) > int(prev.get("revision", 1)):
            by_id[eid] = e

    # Deterministic order: typed-prefix ascending (matches v6 schema §5).
    return sorted(by_id.values(), key=lambda e: e["entry_id"])


# ----- Per-rule-kind apply functions ------------------------------------------


def _apply_block_type_map(
    elements: list[dict[str, Any]],
    rule: dict[str, Any],
    entry_id: str,
    cfg: ApplierConfig,
) -> None:
    """In-place: set element.type when element.source_type matches."""
    source_type = rule.get("source_type")
    target_type = rule.get("target_type")
    extras = rule.get("extras") or {}
    if not source_type or not target_type:
        raise LedgerSchemaError(f"{entry_id}: block_type_map missing source_type/target_type")
    fired = 0
    for el in elements:
        if el.get("source_type") == source_type:
            el["type"] = target_type
            for k, v in extras.items():
                el[k] = v
            fired += 1
    cfg.rule_fired_counts[entry_id] = cfg.rule_fired_counts.get(entry_id, 0) + fired


def _matches_when(el: dict[str, Any], when: dict[str, Any]) -> bool:
    """All keys in `when` must hold against `el`.

    Equality is the default. Two structured extensions (WebGPT 2026-05-13 R4):

    - `bbox_constraints` (sub-object): numeric checks against `el.bbox` (xyxy
      normalized). Supported keys: `x_min_lt`, `x_min_gt`, `x_max_lt`,
      `x_max_gt`, `y_min_lt`, `y_min_gt`, `y_max_lt`, `y_max_gt`,
      `width_lt`, `width_gt`. Element with no bbox or malformed bbox is
      rejected. Unknown sub-keys are treated as failing constraints.

    - List values on a regular key: any-of match. e.g. `{"type": ["paragraph_block", "list"]}`
      passes if `el.type` is in the list.
    """
    for k, v in (when or {}).items():
        if k == "bbox_constraints":
            bbox = el.get("bbox")
            if not bbox or len(bbox) != 4:
                return False
            try:
                x0, y0, x1, y1 = (float(c) for c in bbox)
            except (TypeError, ValueError):
                return False
            width = x1 - x0
            checks = {
                "x_min_lt": lambda lim: x0 < lim,
                "x_min_gt": lambda lim: x0 > lim,
                "x_max_lt": lambda lim: x1 < lim,
                "x_max_gt": lambda lim: x1 > lim,
                "y_min_lt": lambda lim: y0 < lim,
                "y_min_gt": lambda lim: y0 > lim,
                "y_max_lt": lambda lim: y1 < lim,
                "y_max_gt": lambda lim: y1 > lim,
                "width_lt": lambda lim: width < lim,
                "width_gt": lambda lim: width > lim,
            }
            for ck, cv in (v or {}).items():
                if ck not in checks:
                    return False
                try:
                    if not checks[ck](float(cv)):
                        return False
                except (TypeError, ValueError):
                    return False
            continue
        el_v = el.get(k)
        if isinstance(v, list):
            if el_v not in v:
                return False
            continue
        if el_v != v:
            return False
    return True


def _match_spec(text: str, spec: dict[str, Any]) -> dict[str, str | None] | None:
    """Return a groupdict on match, or None on miss.

    Supported matcher kinds (B.2.1 structured matchers replacing brittle regex):
      - equals_ci:        text.strip().lower() == spec["equals_ci"].lower()
      - prefix_ci:        text.lstrip().lower().startswith(spec["prefix_ci"].lower())
      - dot_leader_parser: structured TOC line parse — splits on a run of >=4 dots,
                          captures `label` (left) and `target_page` (right, must be int)
      - regex:            re.match(spec["regex"], text), groupdict() from named groups

    Each matcher returns a `groupdict` consumable by `_expand_template` for
    field population. Matchers fail gracefully (return None) on bad input.
    """
    if "equals_ci" in spec:
        return {} if text.strip().lower() == spec["equals_ci"].strip().lower() else None
    if "prefix_ci" in spec:
        return {} if text.lstrip().lower().startswith(spec["prefix_ci"].lower()) else None
    if spec.get("dot_leader_parser"):
        idx = re.search(r"\.{4,}", text)
        if not idx:
            return None
        label = text[: idx.start()].strip()
        tail = text[idx.end():].strip()
        if not tail.isdigit():
            return None
        if not label:
            return None
        return {"label": label, "target_page": tail}
    if "regex" in spec:
        m = re.match(spec["regex"], text)
        return m.groupdict() if m else None
    return None


# Matcher specificity (B.2.1 per WebGPT 2026-05-11 final spec):
# equals_ci > prefix_ci > dot_leader_parser > regex
# Within a tier, entries are applied in entry_id order.
_MATCHER_SPECIFICITY = {
    "equals_ci": 0,
    "prefix_ci": 1,
    "dot_leader_parser": 2,
    "regex": 3,
}
_SPECIFICITY_NAME = {v: k for k, v in _MATCHER_SPECIFICITY.items()}


# Applier semantic version. Embedded in baseline manifests so a future regen
# can detect drift between recorded baseline and current applier behavior.
__applier_version__ = "0.3.0"


def _entry_specificity(entry: dict[str, Any]) -> int:
    """Lowest priority value of any matcher in this entry's extract specs."""
    rule = entry.get("rule") or {}
    specs = rule.get("extract") or []
    best = 99
    for spec in specs:
        for kind, prio in _MATCHER_SPECIFICITY.items():
            if kind in spec or spec.get(kind):
                best = min(best, prio)
    return best


def _apply_text_classifier_rule(
    elements: list[dict[str, Any]],
    rule: dict[str, Any],
    entry_id: str,
    cfg: ApplierConfig,
    entry: dict[str, Any] | None = None,
) -> None:
    """For each matching element, run each `extract[].matcher` over element.text
    and populate captured fields.

    Overlap semantics (post WebGPT 2026-05-11 review, change C4):
      - staging mode: emit `multiple_rules_matched_warning`; first writer wins.
      - release mode: raise LedgerConflictError UNLESS the entry under apply
        has `release_conflict_waiver: true` (intentional override).

    Matcher specificity (B.2.1): entries within semantic_enrichment are sorted
    by `_entry_specificity` so an `equals_ci` rule on "Table of Contents" fires
    BEFORE a `prefix_ci` rule on "TABLE ". The narrower rule claims the field;
    the broader rule then hits overlap protection and skips.
    """
    applies_when = rule.get("applies_when") or {}
    extract_specs = rule.get("extract") or []
    waiver = bool((entry or {}).get("release_conflict_waiver"))
    self_priority = _entry_specificity(entry or {})
    fired = 0
    for el in elements:
        if not _matches_when(el, applies_when):
            continue
        text = el.get("text") or ""
        for spec in extract_specs:
            group_values = _match_spec(text, spec)
            if group_values is None:
                continue
            for field_name, template in (spec.get("fields") or {}).items():
                new_value = _expand_template(template, group_values)
                existing = el.get(field_name)
                key = (el.get("id"), field_name)
                # Trace every attempted write — even no-op or skipped ones —
                # so reviewers can audit which rules touched each (element, field).
                cfg.field_writes.setdefault(key, []).append((entry_id, new_value))
                if existing is not None and existing != new_value:
                    msg = (
                        f"text_classifier_overlap: {entry_id} would set "
                        f"{el.get('id')}.{field_name}={new_value!r} but it is "
                        f"already {existing!r}"
                    )
                    # Specificity check: if the existing value was written by a
                    # STRICTLY more specific entry (lower priority value), record
                    # a structured specificity_skip event for verifier-side
                    # allowlist enforcement (B.3 post-review per WebGPT).
                    prior_priority = cfg.field_writer_specificity.get(key)
                    if prior_priority is not None and prior_priority < self_priority:
                        cfg.specificity_skips.append({
                            "element_id": el.get("id"),
                            "field": field_name,
                            "prior_entry_id": cfg.field_writer_entry_id.get(key),
                            "skipped_entry_id": entry_id,
                            "prior_specificity": _SPECIFICITY_NAME.get(prior_priority, str(prior_priority)),
                            "skipped_specificity": _SPECIFICITY_NAME.get(self_priority, str(self_priority)),
                            "prior_value": existing,
                            "skipped_value": new_value,
                        })
                        continue
                    # Same-or-less-specific overlap: real conflict.
                    if cfg.mode == "release" and not waiver:
                        raise LedgerConflictError(
                            msg + " — release mode requires no overlap or "
                            "`release_conflict_waiver: true`"
                        )
                    # `release_conflict_waiver: true` means "intentional override
                    # by a more specific text_classifier_rule" — overwrite the
                    # prior value AND record the override event for audit.
                    if waiver:
                        cfg.warnings.append(
                            "release_conflict_waiver_override: " + msg
                            + f"; overwriting per `release_conflict_waiver: true` on {entry_id}"
                        )
                        el[field_name] = new_value
                        cfg.field_writer_specificity[key] = self_priority
                        cfg.field_writer_entry_id[key] = entry_id
                        continue
                    # Staging mode without waiver: prior value wins.
                    cfg.warnings.append(
                        "multiple_rules_matched_warning: " + msg + "; keeping prior value"
                    )
                    continue
                el[field_name] = new_value
                cfg.field_writer_specificity[key] = self_priority
                cfg.field_writer_entry_id[key] = entry_id
            fired += 1
            break  # one matcher per element per rule
    cfg.rule_fired_counts[entry_id] = cfg.rule_fired_counts.get(entry_id, 0) + fired


def _expand_template(template: Any, groups: dict[str, str | None]) -> Any:
    """Resolve `${name}` placeholders against regex groupdict; pass non-string values through.

    Numeric-looking captures are coerced to int when the template is exactly a single
    `${name}` and the value is all digits — keeps target_page as an int.
    """
    if not isinstance(template, str):
        return template
    if template.startswith("${") and template.endswith("}") and template.count("${") == 1:
        name = template[2:-1]
        val = groups.get(name)
        if val is None:
            return None
        if val.isdigit():
            return int(val)
        return val.strip() if val else val
    return re.sub(r"\$\{(\w+)\}", lambda m: (groups.get(m.group(1)) or ""), template)


def _bbox_union(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _apply_bbox_refinement_rule(
    elements: list[dict[str, Any]],
    rule: dict[str, Any],
    entry_id: str,
    cfg: ApplierConfig,
) -> None:
    """Today implements transform='shrink_to_cell_union' against raw.cells.

    Other transforms can be added; unknown transforms raise.
    """
    applies_when = rule.get("applies_when") or {}
    transform = rule.get("transform")
    if transform != "shrink_to_cell_union":
        raise LedgerSchemaError(
            f"{entry_id}: bbox_refinement_rule transform={transform!r} not supported by applier v1"
        )
    fired = 0
    for el in elements:
        if not _matches_when(el, applies_when):
            continue
        raw = el.get("raw") or {}
        # Cells may live under raw.cells (flat) or raw.rows (list-of-lists).
        cells = raw.get("cells")
        bboxes: list[list[float]] = []
        if isinstance(cells, list):
            flat: list[Any] = []
            for c in cells:
                if isinstance(c, list):
                    flat.extend(c)
                else:
                    flat.append(c)
            for c in flat:
                if isinstance(c, dict) and c.get("bbox"):
                    bboxes.append(c["bbox"])
        union = _bbox_union(bboxes)
        if union is not None:
            el["bbox"] = union
            fired += 1
    cfg.rule_fired_counts[entry_id] = cfg.rule_fired_counts.get(entry_id, 0) + fired


def _apply_structural_grouping_rule(
    elements: list[dict[str, Any]],
    rule: dict[str, Any],
    entry_id: str,
    cfg: ApplierConfig,
) -> list[dict[str, Any]]:
    """Whole-list transform. Returns a NEW elements list with synthetic parents
    inserted before each run of matching leaves; sets leaves' parent_id."""
    group_when = rule.get("group_when") or {}
    min_run = int(rule.get("min_run_length") or 2)
    synth_spec = rule.get("synthesize_parent") or {}
    leaf_link = rule.get("leaf_link_field") or "parent_id"

    parent_type = synth_spec.get("type")
    parent_role = synth_spec.get("semantic_role")
    fields_from_children = synth_spec.get("fields_from_children") or []
    heading_lookahead = synth_spec.get("heading_lookahead") or {}
    if not parent_type:
        raise LedgerSchemaError(f"{entry_id}: structural_grouping_rule missing synthesize_parent.type")

    out: list[dict[str, Any]] = []
    i = 0
    counter = 0
    n = len(elements)
    while i < n:
        el = elements[i]
        if not _matches_when(el, group_when):
            out.append(el)
            i += 1
            continue
        # Walk a contiguous run.
        run_start = i
        while i < n and _matches_when(elements[i], group_when):
            i += 1
        run = elements[run_start:i]
        if len(run) < min_run:
            out.extend(run)
            continue

        # Synth parent
        first_page = run[0].get("page")
        last_page = run[-1].get("page")
        parent_id = f"actual:p{first_page}:{parent_type}:{counter}"
        counter += 1

        synth: dict[str, Any] = {
            "id": parent_id,
            "page": first_page,
            "type": parent_type,
            "source_type": synth_spec.get("source_type") or "synthetic",
        }
        if parent_role:
            synth["semantic_role"] = parent_role
        if "page_range" in fields_from_children:
            synth["page_range"] = [first_page, last_page]
        if "entry_count" in fields_from_children:
            synth["entry_count"] = len(run)
        # Bbox union over first-page leaves
        first_page_bboxes = [r["bbox"] for r in run if r.get("page") == first_page and r.get("bbox")]
        if first_page_bboxes:
            synth["bbox"] = _bbox_union(first_page_bboxes)
        # Heading lookahead — find the immediately-preceding element matching text pattern
        heading_pattern = heading_lookahead.get("text_matches")
        if heading_pattern and out:
            prev = out[-1]
            if re.match(heading_pattern, (prev.get("text") or "").strip()):
                synth["heading"] = {"element_id": prev.get("id"), "label": prev.get("text")}
                prev[leaf_link] = parent_id

        # Link leaves
        for leaf in run:
            leaf[leaf_link] = parent_id

        out.append(synth)
        out.extend(run)
        cfg.rule_fired_counts[entry_id] = cfg.rule_fired_counts.get(entry_id, 0) + 1

    return out


# ----- Conflict detection (load time) -----------------------------------------


def _detect_load_time_conflicts(active_entries: Iterable[dict[str, Any]]) -> None:
    """Raise LedgerConflictError when two block_type_map entries share source_type."""
    seen: dict[str, str] = {}
    for entry in active_entries:
        rule = entry.get("rule") or {}
        applier_kind = entry.get("applier_rule_kind")
        if applier_kind != "block_type_map":
            continue
        st = rule.get("source_type")
        if st is None:
            continue
        prior = seen.get(st)
        if prior is not None and prior != entry["entry_id"]:
            raise LedgerConflictError(
                f"block_type_map source_type={st!r} matched by both {prior} and {entry['entry_id']}; "
                f"one must supersede the other"
            )
        seen[st] = entry["entry_id"]


# ----- Main entry point -------------------------------------------------------


def apply_ledger(
    elements: list[dict[str, Any]],
    ledger: dict[str, Any],
    config: ApplierConfig | None = None,
) -> list[dict[str, Any]]:
    """Apply every active ledger entry to `elements` and return the result.

    Pure function — does NOT read from disk or write to disk.
    """
    cfg = config or ApplierConfig()
    if cfg.mode not in {"release", "staging"}:
        raise LedgerSchemaError(f"unknown mode: {cfg.mode!r}")

    active = _filter_active_entries(ledger, cfg.mode)
    _detect_load_time_conflicts(active)

    # Group by category, then within category sort by entry_id ascending.
    by_category: dict[str, list[dict[str, Any]]] = {}
    for entry in active:
        cat = entry.get("category")
        if cat not in _APPLY_ORDER and cat not in {"positive_fixture", "human_decision"}:
            raise LedgerSchemaError(
                f"{entry.get('entry_id')}: unknown category {cat!r}"
            )
        by_category.setdefault(cat, []).append(entry)

    # Mutable copy; structural_grouping reassigns it
    out = [dict(e) for e in elements]

    for category in _APPLY_ORDER:
        category_entries = by_category.get(category, [])
        # Within `semantic_enrichment`, sort by matcher specificity so the
        # narrowest matcher (equals_ci) claims its field before broader
        # matchers (prefix_ci → dot_leader_parser → regex). Avoids negative-
        # lookahead coupling between rules.
        if category == "semantic_enrichment":
            category_entries = sorted(
                category_entries,
                key=lambda e: (_entry_specificity(e), e["entry_id"]),
            )
        for entry in category_entries:
            applier_kind = entry.get("applier_rule_kind")
            if applier_kind is None and category in {"positive_fixture", "human_decision"}:
                continue
            if applier_kind not in _RULE_KINDS:
                raise LedgerSchemaError(
                    f"{entry.get('entry_id')}: applier_rule_kind {applier_kind!r} not supported"
                )
            rule = entry.get("rule") or {}
            eid = entry["entry_id"]
            if applier_kind == "block_type_map":
                _apply_block_type_map(out, rule, eid, cfg)
            elif applier_kind == "text_classifier_rule":
                _apply_text_classifier_rule(out, rule, eid, cfg, entry=entry)
            elif applier_kind == "bbox_refinement_rule":
                _apply_bbox_refinement_rule(out, rule, eid, cfg)
            elif applier_kind == "structural_grouping_rule":
                out = _apply_structural_grouping_rule(out, rule, eid, cfg)

    return out


__all__ = [
    "apply_ledger",
    "ApplierConfig",
    "LedgerError",
    "LedgerConflictError",
    "LedgerSchemaError",
]
