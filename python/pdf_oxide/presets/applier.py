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
    "table_contained_suppression_rule",
    "table_false_positive_suppression_rule",
    "same_band_merge_rule",
    "adjacent_text_merge_rule",
    "field_split_rule",
    "text_normalization_rule",
    "page_chrome_prefix_strip_rule",
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

    - `font_properties` (sub-object): typographic checks against
      `el.font_size`, `el.font_name`, `el.is_bold` (populated at
      `_extract_page` time from the underlying classify_blocks payload).
      Supported keys: `is_bold` (bool, exact match), `font_size_lt`,
      `font_size_gt`, `font_size_eq` (within ±0.1pt), `font_name`
      (exact), `font_name_contains` (substring). Element with no
      font_size is rejected if any font_size_* check is given.

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
        if k == "font_properties":
            font_size = el.get("font_size")
            font_name = el.get("font_name") or ""
            is_bold = el.get("is_bold")
            for ck, cv in (v or {}).items():
                if ck == "is_bold":
                    if bool(is_bold) is not bool(cv):
                        return False
                elif ck == "font_size_lt":
                    if font_size is None:
                        return False
                    try:
                        if not float(font_size) < float(cv):
                            return False
                    except (TypeError, ValueError):
                        return False
                elif ck == "font_size_gt":
                    if font_size is None:
                        return False
                    try:
                        if not float(font_size) > float(cv):
                            return False
                    except (TypeError, ValueError):
                        return False
                elif ck == "font_size_eq":
                    if font_size is None:
                        return False
                    try:
                        if abs(float(font_size) - float(cv)) > 0.1:
                            return False
                    except (TypeError, ValueError):
                        return False
                elif ck == "font_name":
                    if font_name != cv:
                        return False
                elif ck == "font_name_contains":
                    if str(cv) not in font_name:
                        return False
                else:
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


def _bbox_area(bbox: list[float]) -> float:
    if not bbox or len(bbox) != 4:
        return 0.0
    return max(0.0, float(bbox[2]) - float(bbox[0])) * max(0.0, float(bbox[3]) - float(bbox[1]))


def _bbox_coverage(inner: list[float], outer: list[float]) -> float:
    inner_area = _bbox_area(inner)
    if inner_area <= 0:
        return 0.0
    x0 = max(float(inner[0]), float(outer[0]))
    y0 = max(float(inner[1]), float(outer[1]))
    x1 = min(float(inner[2]), float(outer[2]))
    y1 = min(float(inner[3]), float(outer[3]))
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    return intersection / inner_area


def _bbox_y_center(bbox: list[float]) -> float:
    return (float(bbox[1]) + float(bbox[3])) / 2.0


def _strip_leading_sidebar_tokens(text: str, token_patterns: list[str]) -> str:
    tokens = text.split()
    if not tokens:
        return ""
    compiled = [re.compile(pattern, re.I) for pattern in token_patterns]
    kept: list[str] = []
    original_lower = text.lower()
    for index, token in enumerate(tokens):
        normalized = token.strip(" ,.;:")
        next_normalized = tokens[index + 1].strip(" ,.;:") if index + 1 < len(tokens) else ""
        if normalized.lower() == "of" and next_normalized.lower() == "charge":
            continue
        if normalized.lower() == "from" and "this publication" in original_lower:
            continue
        if any(pattern.search(normalized) for pattern in compiled):
            continue
        kept.append(token)
    return " ".join(kept).strip()


def _strip_leading_prefix_patterns(text: str, prefix_patterns: list[str]) -> str:
    stripped = text.strip()
    for pattern in prefix_patterns:
        stripped = re.sub(pattern, "", stripped, count=1, flags=re.I).strip()
    return stripped


def _apply_page_chrome_prefix_strip_rule(
    elements: list[dict[str, Any]],
    rule: dict[str, Any],
    entry_id: str,
    cfg: ApplierConfig,
) -> list[dict[str, Any]]:
    """Strip NIST left-margin chrome tokens that PyMuPDF interleaves into body lines."""
    applies_when = rule.get("applies_when") or {}
    token_patterns = rule.get("strip_token_patterns") or []
    prefix_patterns = rule.get("strip_prefix_patterns") or []
    body_column_x_min = rule.get("body_column_x_min")
    empty_type = rule.get("empty_type") or "header_footer_noise"
    tiny_fragment_when = rule.get("tiny_fragment_when") or {}
    tiny_fragment_type = rule.get("tiny_fragment_type") or empty_type
    fired = 0

    for el in elements:
        if not _matches_when(el, applies_when):
            continue
        original_text = el.get("text") or ""
        cleaned = _strip_leading_prefix_patterns(original_text, prefix_patterns)
        cleaned = _strip_leading_sidebar_tokens(cleaned, token_patterns)
        cleaned = " ".join(cleaned.split())

        if cleaned != " ".join(original_text.split()):
            if cleaned:
                el["text"] = cleaned
                if body_column_x_min is not None and el.get("bbox") and len(el["bbox"]) == 4:
                    bbox = list(el["bbox"])
                    bbox[0] = max(float(bbox[0]), float(body_column_x_min))
                    el["bbox"] = bbox
            else:
                el["type"] = empty_type
                el["semantic_role"] = "page_chrome"
            fired += 1
            continue

        if tiny_fragment_when and _matches_when(el, tiny_fragment_when):
            el["type"] = tiny_fragment_type
            el["semantic_role"] = "page_chrome"
            fired += 1

    cfg.rule_fired_counts[entry_id] = cfg.rule_fired_counts.get(entry_id, 0) + fired
    return elements


def _apply_same_band_merge_rule(
    elements: list[dict[str, Any]],
    rule: dict[str, Any],
    entry_id: str,
    cfg: ApplierConfig,
) -> list[dict[str, Any]]:
    """Merge same-page chrome fragments that occupy one horizontal band."""
    merge_when = rule.get("merge_when") or {}
    min_count = int(rule.get("min_count") or 2)
    max_y_center_delta = float(rule.get("max_y_center_delta") or 0.02)
    suppress_children = bool(rule.get("suppress_children", True))
    synth_spec = rule.get("synthesize") or {}
    parent_type = synth_spec.get("type") or "running_header"
    parent_source_type = synth_spec.get("source_type") or "synthetic"

    candidates_by_page: dict[Any, list[dict[str, Any]]] = {}
    for el in elements:
        if el.get("bbox") and _matches_when(el, merge_when):
            candidates_by_page.setdefault(el.get("page"), []).append(el)

    groups_by_first_id: dict[str, dict[str, Any]] = {}
    child_ids: set[str] = set()
    fired = 0
    for page, candidates in candidates_by_page.items():
        remaining = sorted(candidates, key=lambda el: (_bbox_y_center(el["bbox"]), float(el["bbox"][0])))
        while remaining:
            seed = remaining.pop(0)
            seed_center = _bbox_y_center(seed["bbox"])
            group = [seed]
            keep: list[dict[str, Any]] = []
            for candidate in remaining:
                if abs(_bbox_y_center(candidate["bbox"]) - seed_center) <= max_y_center_delta:
                    group.append(candidate)
                else:
                    keep.append(candidate)
            remaining = keep
            if len(group) < min_count:
                continue
            group.sort(key=lambda el: float(el["bbox"][0]))
            first = group[0]
            synth = {
                "id": f"actual:p{page}:{parent_type}:merged:{fired}",
                "page": page,
                "type": parent_type,
                "source_type": parent_source_type,
                "bbox": _bbox_union([el["bbox"] for el in group if el.get("bbox")]),
                "text": " ".join((el.get("text") or "").strip() for el in group if (el.get("text") or "").strip()),
                "child_ids": [el.get("id") for el in group],
            }
            if synth_spec.get("semantic_role"):
                synth["semantic_role"] = synth_spec["semantic_role"]
            groups_by_first_id[str(first.get("id"))] = synth
            if suppress_children:
                child_ids.update(str(el.get("id")) for el in group)
            fired += 1

    if not groups_by_first_id:
        return elements

    out: list[dict[str, Any]] = []
    for el in elements:
        el_id = str(el.get("id"))
        if el_id in groups_by_first_id:
            out.append(groups_by_first_id[el_id])
        if el_id in child_ids:
            continue
        out.append(el)

    cfg.rule_fired_counts[entry_id] = cfg.rule_fired_counts.get(entry_id, 0) + fired
    return out


def _apply_adjacent_text_merge_rule(
    elements: list[dict[str, Any]],
    rule: dict[str, Any],
    entry_id: str,
    cfg: ApplierConfig,
) -> list[dict[str, Any]]:
    """Merge a tightly guarded adjacent text continuation into its predecessor."""
    lead_when = rule.get("lead_when") or {}
    tail_when = rule.get("tail_when") or {}
    lead_text_regex = rule.get("lead_text_regex")
    tail_text_regex = rule.get("tail_text_regex")
    max_y_gap = float(rule.get("max_y_gap", 0.02))
    max_x_delta = float(rule.get("max_x_delta", 0.03))
    join_style = rule.get("join_style") or "space"
    merged_fields = rule.get("merged_fields") or {}
    child_ids_field = rule.get("child_ids_field") or "child_ids"

    lead_pattern = re.compile(lead_text_regex) if lead_text_regex else None
    tail_pattern = re.compile(tail_text_regex) if tail_text_regex else None

    out: list[dict[str, Any]] = []
    fired = 0
    i = 0
    while i < len(elements):
        lead = dict(elements[i])
        if i + 1 >= len(elements):
            out.append(lead)
            i += 1
            continue
        tail = elements[i + 1]

        if not (_matches_when(lead, lead_when) and _matches_when(tail, tail_when)):
            out.append(lead)
            i += 1
            continue
        if lead.get("page") != tail.get("page"):
            out.append(lead)
            i += 1
            continue
        lead_text = str(lead.get("text") or "").strip()
        tail_text = str(tail.get("text") or "").strip()
        if lead_pattern and not lead_pattern.search(lead_text):
            out.append(lead)
            i += 1
            continue
        if tail_pattern and not tail_pattern.search(tail_text):
            out.append(lead)
            i += 1
            continue

        lead_bbox = lead.get("bbox") or []
        tail_bbox = tail.get("bbox") or []
        if len(lead_bbox) != 4 or len(tail_bbox) != 4:
            out.append(lead)
            i += 1
            continue
        y_gap = float(tail_bbox[1]) - float(lead_bbox[3])
        x_delta = abs(float(tail_bbox[0]) - float(lead_bbox[0]))
        if y_gap < -max_y_gap or y_gap > max_y_gap or x_delta > max_x_delta:
            out.append(lead)
            i += 1
            continue

        if join_style == "hyphen_continuation" and lead_text.endswith("-"):
            merged_text = f"{lead_text}{tail_text}"
        else:
            merged_text = " ".join(part for part in [lead_text, tail_text] if part)
        lead["text"] = merged_text
        lead["bbox"] = _bbox_union([lead_bbox, tail_bbox])
        lead[child_ids_field] = [lead.get("id"), tail.get("id")]
        for field, value in merged_fields.items():
            lead[field] = value
        out.append(lead)
        fired += 1
        i += 2

    cfg.rule_fired_counts[entry_id] = cfg.rule_fired_counts.get(entry_id, 0) + fired
    return out


def _collapse_url_internal_whitespace(text: str) -> str:
    def replace_url(match: re.Match[str]) -> str:
        return re.sub(r"\s+", "", match.group(0))

    return re.sub(r"https?://.*?(?=\s+\[|$)", replace_url, text)


def _apply_text_normalization_rule(
    elements: list[dict[str, Any]],
    rule: dict[str, Any],
    entry_id: str,
    cfg: ApplierConfig,
) -> None:
    applies_when = rule.get("applies_when") or {}
    transforms = rule.get("transforms") or []
    fired = 0
    for el in elements:
        if not _matches_when(el, applies_when):
            continue
        original = str(el.get("text") or "")
        text = original
        for transform in transforms:
            if transform == "remove_space_after_standard_citation_hyphen":
                text = re.sub(
                    r"(\[(?:SP|NIST\s+SP|FIPS|IR)\s+[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*-)\s+([A-Za-z0-9])",
                    r"\1\2",
                    text,
                )
            elif transform == "collapse_url_internal_whitespace":
                text = _collapse_url_internal_whitespace(text)
            else:
                raise LedgerSchemaError(f"{entry_id}: unknown text normalization transform {transform!r}")
        if text != original:
            el["text"] = text
            fired += 1
    cfg.rule_fired_counts[entry_id] = cfg.rule_fired_counts.get(entry_id, 0) + fired


def _slice_bbox(bbox: list[Any], index: int, count: int) -> list[Any]:
    if len(bbox) != 4 or count <= 1:
        return bbox
    try:
        x0, y0, x1, y1 = (float(c) for c in bbox)
    except (TypeError, ValueError):
        return bbox
    height = y1 - y0
    if height <= 0:
        return bbox
    seg_y0 = y0 + height * (index / count)
    seg_y1 = y0 + height * ((index + 1) / count)
    return [x0, seg_y0, x1, seg_y1]


def _slice_bbox_by_weight(
    bbox: list[Any],
    start_weight: float,
    end_weight: float,
    total_weight: float,
) -> list[Any]:
    if len(bbox) != 4 or total_weight <= 0:
        return bbox
    try:
        x0, y0, x1, y1 = (float(c) for c in bbox)
    except (TypeError, ValueError):
        return bbox
    height = y1 - y0
    if height <= 0:
        return bbox
    return [
        x0,
        y0 + height * (start_weight / total_weight),
        x1,
        y0 + height * (end_weight / total_weight),
    ]


def _apply_field_split_rule(
    elements: list[dict[str, Any]],
    rule: dict[str, Any],
    entry_id: str,
    cfg: ApplierConfig,
) -> list[dict[str, Any]]:
    applies_when = rule.get("applies_when") or {}
    split_regex = rule.get("split_regex")
    segments = rule.get("segments") or []
    if not split_regex or not segments:
        raise LedgerSchemaError(f"{entry_id}: field_split_rule missing split_regex/segments")
    pattern = re.compile(split_regex)
    bbox_strategy = rule.get("bbox_strategy") or "slice_even"
    out: list[dict[str, Any]] = []
    fired = 0
    for el in elements:
        if not _matches_when(el, applies_when):
            out.append(el)
            continue
        text = str(el.get("text") or "").strip()
        match = pattern.match(text)
        if not match:
            out.append(el)
            continue
        group_texts = [
            (match.groupdict().get(segment.get("group") or "") or "").strip()
            for segment in segments
        ]
        weights = [max(1, (len(text) + 79) // 80) for text in group_texts]
        total_weight = float(sum(weights))
        cumulative_weight = 0.0
        child_ids: list[Any] = []
        split_parts: list[dict[str, Any]] = []
        for idx, segment in enumerate(segments):
            group_name = segment.get("group")
            if not group_name:
                raise LedgerSchemaError(f"{entry_id}: field_split_rule segment missing group")
            segment_text = (match.groupdict().get(group_name) or "").strip()
            if not segment_text:
                continue
            part = dict(el)
            suffix = segment.get("id_suffix")
            if suffix:
                part["id"] = f"{el.get('id')}#{suffix}"
            part["text"] = segment_text
            if bbox_strategy == "retain_parent":
                part["bbox"] = el.get("bbox") or []
            elif bbox_strategy == "slice_even":
                part["bbox"] = _slice_bbox(el.get("bbox") or [], idx, len(segments))
            elif bbox_strategy == "text_length_weighted":
                start_weight = cumulative_weight
                cumulative_weight += float(weights[idx])
                part["bbox"] = _slice_bbox_by_weight(
                    el.get("bbox") or [],
                    start_weight,
                    cumulative_weight,
                    total_weight,
                )
            else:
                raise LedgerSchemaError(f"{entry_id}: unknown field_split_rule bbox_strategy {bbox_strategy!r}")
            for field, value in (segment.get("fields") or {}).items():
                part[field] = value
            split_parts.append(part)
            child_ids.append(part.get("id"))
        if len(split_parts) <= 1:
            out.append(el)
            continue
        for part in split_parts:
            part["parent_id"] = el.get("id")
        split_parts[0]["child_ids"] = child_ids
        out.extend(split_parts)
        fired += 1
    cfg.rule_fired_counts[entry_id] = cfg.rule_fired_counts.get(entry_id, 0) + fired
    return out


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


def _apply_table_contained_suppression_rule(
    elements: list[dict[str, Any]],
    rule: dict[str, Any],
    entry_id: str,
    cfg: ApplierConfig,
) -> list[dict[str, Any]]:
    """Remove standalone content elements already represented by table regions."""
    table_when = rule.get("table_when") or {"type": "table"}
    suppress_when = rule.get("suppress_when") or {}
    min_coverage = float(rule.get("min_coverage") or 0.90)

    tables = [
        el for el in elements
        if el.get("bbox") and _matches_when(el, table_when)
    ]
    if not tables:
        return elements

    out: list[dict[str, Any]] = []
    fired = 0
    for el in elements:
        bbox = el.get("bbox")
        if not bbox or not _matches_when(el, suppress_when):
            out.append(el)
            continue
        page = el.get("page")
        covered = any(
            table.get("page") == page
            and _bbox_coverage(bbox, table.get("bbox") or []) >= min_coverage
            for table in tables
        )
        if covered:
            fired += 1
            if cfg.trace:
                cfg.warnings.append(
                    f"table_contained_suppression: {entry_id} removed {el.get('id')}"
                )
            continue
        out.append(el)

    cfg.rule_fired_counts[entry_id] = cfg.rule_fired_counts.get(entry_id, 0) + fired
    return out


def _iter_raw_table_rows(el: dict[str, Any]) -> Iterable[list[str]]:
    raw = el.get("raw") or {}
    rows = raw.get("rows") or []
    if not isinstance(rows, list):
        return
    for row in rows:
        cells = row.get("cells") if isinstance(row, dict) else row
        if not isinstance(cells, list):
            continue
        values: list[str] = []
        for cell in cells:
            if isinstance(cell, dict):
                text = cell.get("text")
            else:
                text = cell
            if text is None:
                continue
            cleaned = " ".join(str(text).split())
            if cleaned:
                values.append(cleaned)
        yield values


def _raw_table_matches_false_positive_rule(el: dict[str, Any], rule: dict[str, Any]) -> bool:
    if not _matches_when(el, rule.get("table_when") or {"type": "table"}):
        return False

    text = " ".join(str(el.get("text") or "").split())
    if rule.get("require_empty_text", True) and text:
        return False

    bbox = el.get("bbox") or []
    if len(bbox) != 4:
        return False
    min_height = float(rule.get("min_bbox_height") or 0.0)
    if min_height and (float(bbox[3]) - float(bbox[1])) < min_height:
        return False

    raw = el.get("raw") or {}
    min_rows = int(rule.get("min_row_count") or 0)
    min_columns = int(rule.get("min_column_count") or 0)
    if min_rows and int(raw.get("row_count") or 0) < min_rows:
        return False
    raw_column_count = raw.get("column_count") or raw.get("col_count")
    if min_columns and int(raw_column_count or 0) < min_columns:
        return False

    ignore_patterns = [re.compile(pattern, re.I) for pattern in rule.get("ignore_row_patterns") or []]
    required_patterns = [re.compile(pattern, re.I) for pattern in rule.get("required_row_patterns") or []]
    max_content_rows = int(rule.get("max_content_rows") or 0)

    content_rows: list[str] = []
    for values in _iter_raw_table_rows(el):
        row_text = " ".join(values)
        if not row_text:
            continue
        if any(pattern.search(row_text) for pattern in ignore_patterns):
            continue
        content_rows.append(row_text)

    if max_content_rows and len(content_rows) > max_content_rows:
        return False
    return all(any(pattern.search(row) for row in content_rows) for pattern in required_patterns)


def _apply_table_false_positive_suppression_rule(
    elements: list[dict[str, Any]],
    rule: dict[str, Any],
    entry_id: str,
    cfg: ApplierConfig,
) -> list[dict[str, Any]]:
    """Remove table blocks that are parser artifacts, before they suppress body text."""
    out: list[dict[str, Any]] = []
    fired = 0
    for el in elements:
        if _raw_table_matches_false_positive_rule(el, rule):
            fired += 1
            if cfg.trace:
                cfg.warnings.append(
                    f"table_false_positive_suppression: {entry_id} removed {el.get('id')}"
                )
            continue
        out.append(el)
    cfg.rule_fired_counts[entry_id] = cfg.rule_fired_counts.get(entry_id, 0) + fired
    return out


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
            elif applier_kind == "table_contained_suppression_rule":
                out = _apply_table_contained_suppression_rule(out, rule, eid, cfg)
            elif applier_kind == "table_false_positive_suppression_rule":
                out = _apply_table_false_positive_suppression_rule(out, rule, eid, cfg)
            elif applier_kind == "same_band_merge_rule":
                out = _apply_same_band_merge_rule(out, rule, eid, cfg)
            elif applier_kind == "adjacent_text_merge_rule":
                out = _apply_adjacent_text_merge_rule(out, rule, eid, cfg)
            elif applier_kind == "field_split_rule":
                out = _apply_field_split_rule(out, rule, eid, cfg)
            elif applier_kind == "text_normalization_rule":
                _apply_text_normalization_rule(out, rule, eid, cfg)
            elif applier_kind == "page_chrome_prefix_strip_rule":
                out = _apply_page_chrome_prefix_strip_rule(out, rule, eid, cfg)

    return out


__all__ = [
    "apply_ledger",
    "ApplierConfig",
    "LedgerError",
    "LedgerConflictError",
    "LedgerSchemaError",
]
