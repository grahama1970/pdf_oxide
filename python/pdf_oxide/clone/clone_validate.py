"""Structural validation against TruthManifest.

Goes beyond QID presence to validate:
- Ordering correctness (QIDs appear in expected sequence)
- Grid recovery (table structure matches)
- Contamination detection (QID appearing in wrong block)
- Section hierarchy preservation
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from pdf_oxide.clone.clone_types import TruthManifest, TruthObject, BlockType


# QID pattern: [QID_XXXXXXXXXXXXXXXX] (16 hex chars)
QID_PATTERN = re.compile(r"\[QID_([A-F0-9]{16})\]")


@dataclass
class QidRecovery:
    """Result of QID presence validation."""
    expected: int
    found: int
    missing: List[str] = field(default_factory=list)
    unexpected: List[str] = field(default_factory=list)

    @property
    def recovery_rate(self) -> float:
        return self.found / max(self.expected, 1)

    @property
    def is_perfect(self) -> bool:
        return self.found == self.expected and not self.unexpected


@dataclass
class OrderingResult:
    """Result of ordering validation."""
    total_pairs: int
    correct_pairs: int
    inversions: List[Tuple[str, str]] = field(default_factory=list)  # (qid_a, qid_b) where a should precede b

    @property
    def ordering_score(self) -> float:
        return self.correct_pairs / max(self.total_pairs, 1)


@dataclass
class GridRecovery:
    """Result of table grid validation."""
    table_id: str
    expected_rows: int
    expected_cols: int
    found_rows: int
    found_cols: int
    cell_recovery: float  # 0.0-1.0
    missing_cells: List[Tuple[int, int]] = field(default_factory=list)  # (row, col)

    @property
    def structure_match(self) -> bool:
        return self.expected_rows == self.found_rows and self.expected_cols == self.found_cols


@dataclass
class ContaminationResult:
    """Result of contamination detection."""
    total_qids: int
    contaminated: int
    contamination_details: List[Dict[str, Any]] = field(default_factory=list)
    # Each detail: {qid, expected_block_type, found_in_block_type, expected_table_id, found_table_id}

    @property
    def contamination_rate(self) -> float:
        return self.contaminated / max(self.total_qids, 1)

    @property
    def is_clean(self) -> bool:
        return self.contaminated == 0


@dataclass
class ValidationResult:
    """Complete validation result against TruthManifest."""
    manifest_path: str
    extraction_source: str

    # Component results
    qid_recovery: QidRecovery
    ordering: OrderingResult
    grid_recoveries: List[GridRecovery] = field(default_factory=list)
    contamination: ContaminationResult = field(default_factory=lambda: ContaminationResult(0, 0))

    # Overall scores
    @property
    def overall_score(self) -> float:
        """Weighted overall score (0.0-1.0)."""
        weights = {
            "recovery": 0.4,
            "ordering": 0.3,
            "grid": 0.2,
            "contamination": 0.1,
        }
        recovery = self.qid_recovery.recovery_rate
        ordering = self.ordering.ordering_score
        grid = (
            sum(g.cell_recovery for g in self.grid_recoveries) / max(len(self.grid_recoveries), 1)
            if self.grid_recoveries else 1.0
        )
        contamination = 1.0 - self.contamination.contamination_rate

        return (
            weights["recovery"] * recovery +
            weights["ordering"] * ordering +
            weights["grid"] * grid +
            weights["contamination"] * contamination
        )

    @property
    def passed(self) -> bool:
        """Did validation pass? (threshold: 0.9)"""
        return self.overall_score >= 0.9

    def to_dict(self) -> Dict[str, Any]:
        return {
            "manifest_path": self.manifest_path,
            "extraction_source": self.extraction_source,
            "overall_score": self.overall_score,
            "passed": self.passed,
            "qid_recovery": {
                "expected": self.qid_recovery.expected,
                "found": self.qid_recovery.found,
                "recovery_rate": self.qid_recovery.recovery_rate,
                "missing_count": len(self.qid_recovery.missing),
            },
            "ordering": {
                "total_pairs": self.ordering.total_pairs,
                "correct_pairs": self.ordering.correct_pairs,
                "ordering_score": self.ordering.ordering_score,
                "inversion_count": len(self.ordering.inversions),
            },
            "grid_recovery": {
                "table_count": len(self.grid_recoveries),
                "avg_cell_recovery": (
                    sum(g.cell_recovery for g in self.grid_recoveries) / max(len(self.grid_recoveries), 1)
                    if self.grid_recoveries else 1.0
                ),
                "structure_matches": sum(1 for g in self.grid_recoveries if g.structure_match),
            },
            "contamination": {
                "total_qids": self.contamination.total_qids,
                "contaminated": self.contamination.contaminated,
                "contamination_rate": self.contamination.contamination_rate,
            },
        }

    def summary(self) -> str:
        """Human-readable summary."""
        status = "PASS" if self.passed else "FAIL"
        return (
            f"Validation {status} (score: {self.overall_score:.2%})\n"
            f"  QID Recovery: {self.qid_recovery.found}/{self.qid_recovery.expected} "
            f"({self.qid_recovery.recovery_rate:.1%})\n"
            f"  Ordering: {self.ordering.correct_pairs}/{self.ordering.total_pairs} pairs "
            f"({self.ordering.ordering_score:.1%})\n"
            f"  Grid Recovery: {len(self.grid_recoveries)} tables, "
            f"avg cell recovery {sum(g.cell_recovery for g in self.grid_recoveries) / max(len(self.grid_recoveries), 1):.1%}\n"
            f"  Contamination: {self.contamination.contaminated}/{self.contamination.total_qids} "
            f"({self.contamination.contamination_rate:.1%})"
        )


def extract_qids_from_text(text: str) -> List[str]:
    """Extract all QIDs from text in order of appearance."""
    return [f"QID_{m.group(1)}" for m in QID_PATTERN.finditer(text)]


def validate_qid_recovery(
    manifest: TruthManifest,
    extracted_qids: Set[str],
) -> QidRecovery:
    """Validate QID presence."""
    expected_qids = set(manifest.qid_to_object.keys())

    found = expected_qids & extracted_qids
    missing = expected_qids - extracted_qids
    unexpected = extracted_qids - expected_qids

    return QidRecovery(
        expected=len(expected_qids),
        found=len(found),
        missing=sorted(missing),
        unexpected=sorted(unexpected),
    )


def validate_ordering(
    manifest: TruthManifest,
    extracted_qids_ordered: List[str],
) -> OrderingResult:
    """Validate that QIDs appear in expected order.

    Uses page-level ordering from manifest.page_qid_order.
    Checks that for any two QIDs from the same page, if A precedes B
    in the manifest, A should precede B in extracted text.
    """
    # Build position map for extracted QIDs
    extracted_pos = {qid: i for i, qid in enumerate(extracted_qids_ordered)}

    total_pairs = 0
    correct_pairs = 0
    inversions = []

    # Check ordering within each page
    for page_num, expected_order in manifest.page_qid_order.items():
        # Filter to QIDs that were actually extracted
        page_qids = [qid for qid in expected_order if qid in extracted_pos]

        # Check all pairs
        for i in range(len(page_qids)):
            for j in range(i + 1, len(page_qids)):
                qid_a = page_qids[i]
                qid_b = page_qids[j]
                total_pairs += 1

                if extracted_pos[qid_a] < extracted_pos[qid_b]:
                    correct_pairs += 1
                else:
                    inversions.append((qid_a, qid_b))

    return OrderingResult(
        total_pairs=total_pairs,
        correct_pairs=correct_pairs,
        inversions=inversions,
    )


def validate_grid_recovery(
    manifest: TruthManifest,
    extracted_tables: List[Dict[str, Any]],
) -> List[GridRecovery]:
    """Validate table grid structure recovery.

    Args:
        manifest: Truth manifest with table structures
        extracted_tables: List of extracted tables, each with:
            - table_id (optional, for matching)
            - rows: int
            - cols: int
            - cells: List[List[str]] (cell contents including QIDs)

    Returns:
        GridRecovery for each table in manifest
    """
    results = []

    for table_id, structure in manifest.table_structures.items():
        expected_rows = structure["rows"]
        expected_cols = structure["cols"]
        expected_cells = structure["cells"]  # [[qid, ...], ...]

        # Try to find matching extracted table
        # Match by QID overlap
        expected_qids = {qid for row in expected_cells for qid in row if qid}
        best_match = None
        best_overlap = 0

        for ext_table in extracted_tables:
            ext_cells = ext_table.get("cells", [])
            ext_qids = set()
            for row in ext_cells:
                for cell in row:
                    ext_qids.update(extract_qids_from_text(str(cell)))

            overlap = len(expected_qids & ext_qids)
            if overlap > best_overlap:
                best_overlap = overlap
                best_match = ext_table

        if best_match is None:
            # Table not found at all
            results.append(GridRecovery(
                table_id=table_id,
                expected_rows=expected_rows,
                expected_cols=expected_cols,
                found_rows=0,
                found_cols=0,
                cell_recovery=0.0,
                missing_cells=[(r, c) for r in range(expected_rows) for c in range(expected_cols)],
            ))
            continue

        # Compare structure
        found_rows = best_match.get("rows", len(best_match.get("cells", [])))
        found_cols = best_match.get("cols", 0)
        if best_match.get("cells"):
            found_cols = max(len(row) for row in best_match["cells"]) if best_match["cells"] else 0

        # Count recovered cells by QID
        ext_cells = best_match.get("cells", [])
        ext_qids = set()
        for row in ext_cells:
            for cell in row:
                ext_qids.update(extract_qids_from_text(str(cell)))

        recovered_count = len(expected_qids & ext_qids)
        total_cells = len(expected_qids)

        # Find missing cells
        missing_cells = []
        for r_idx, row in enumerate(expected_cells):
            for c_idx, qid in enumerate(row):
                if qid and qid not in ext_qids:
                    missing_cells.append((r_idx, c_idx))

        results.append(GridRecovery(
            table_id=table_id,
            expected_rows=expected_rows,
            expected_cols=expected_cols,
            found_rows=found_rows,
            found_cols=found_cols,
            cell_recovery=recovered_count / max(total_cells, 1),
            missing_cells=missing_cells,
        ))

    return results


def validate_contamination(
    manifest: TruthManifest,
    extraction_result: Dict[str, Any],
) -> ContaminationResult:
    """Detect QIDs appearing in wrong context.

    Contamination occurs when:
    - A table cell QID appears outside tables
    - A heading QID appears inside a table
    - A QID from table A appears in table B

    Args:
        manifest: Truth manifest
        extraction_result: Extraction output with structure:
            - blocks: List[{type, text, table_id?, ...}]
            - tables: List[{table_id, cells, ...}]

    Returns:
        ContaminationResult
    """
    details = []
    total = 0
    contaminated = 0

    # Build extracted location map
    extracted_locations: Dict[str, Dict[str, Any]] = {}

    # From blocks
    for block in extraction_result.get("blocks", []):
        block_type = block.get("type", "unknown")
        block_text = block.get("text", "")
        table_id = block.get("table_id")

        for qid in extract_qids_from_text(block_text):
            extracted_locations[qid] = {
                "block_type": block_type,
                "table_id": table_id,
            }

    # From tables
    for table in extraction_result.get("tables", []):
        table_id = table.get("table_id", "unknown")
        for row in table.get("cells", []):
            for cell in row:
                for qid in extract_qids_from_text(str(cell)):
                    extracted_locations[qid] = {
                        "block_type": "table_cell",
                        "table_id": table_id,
                    }

    # Check each expected QID
    for qid, truth_obj in manifest.qid_to_object.items():
        total += 1

        if qid not in extracted_locations:
            continue  # Missing QID, handled by recovery check

        extracted = extracted_locations[qid]

        # Check block type mismatch
        expected_is_table = truth_obj.block_type in (
            BlockType.TABLE, BlockType.TABLE_HEADER, BlockType.TABLE_CELL
        )
        found_is_table = extracted["block_type"] in ("table", "table_cell", "table_header")

        if expected_is_table != found_is_table:
            contaminated += 1
            details.append({
                "qid": qid,
                "expected_block_type": truth_obj.block_type.value,
                "found_in_block_type": extracted["block_type"],
                "issue": "table_boundary_cross",
            })
            continue

        # Check table ID mismatch (QID from table A found in table B)
        if expected_is_table and found_is_table:
            expected_table = truth_obj.table_id
            found_table = extracted.get("table_id")

            if expected_table and found_table and expected_table != found_table:
                contaminated += 1
                details.append({
                    "qid": qid,
                    "expected_table_id": expected_table,
                    "found_table_id": found_table,
                    "issue": "table_id_mismatch",
                })

    return ContaminationResult(
        total_qids=total,
        contaminated=contaminated,
        contamination_details=details,
    )


def validate_extraction(
    manifest: TruthManifest,
    extraction_result: Dict[str, Any],
    manifest_path: str = "",
    extraction_source: str = "",
) -> ValidationResult:
    """Complete validation of extraction against truth manifest.

    Args:
        manifest: TruthManifest from clone builder
        extraction_result: Extraction output with structure:
            - text: str (full extracted text)
            - blocks: List[{type, text, ...}]
            - tables: List[{table_id, rows, cols, cells, ...}]
        manifest_path: Path to manifest file (for reporting)
        extraction_source: Path to extracted PDF (for reporting)

    Returns:
        ValidationResult with all component scores
    """
    # Extract all QIDs from full text
    full_text = extraction_result.get("text", "")
    if not full_text:
        # Build from blocks
        full_text = "\n".join(
            block.get("text", "") for block in extraction_result.get("blocks", [])
        )

    extracted_qids_ordered = extract_qids_from_text(full_text)
    extracted_qids_set = set(extracted_qids_ordered)

    # Run component validations
    qid_recovery = validate_qid_recovery(manifest, extracted_qids_set)
    ordering = validate_ordering(manifest, extracted_qids_ordered)
    grid_recoveries = validate_grid_recovery(manifest, extraction_result.get("tables", []))
    contamination = validate_contamination(manifest, extraction_result)

    return ValidationResult(
        manifest_path=manifest_path,
        extraction_source=extraction_source,
        qid_recovery=qid_recovery,
        ordering=ordering,
        grid_recoveries=grid_recoveries,
        contamination=contamination,
    )


def validate_from_text(
    manifest: TruthManifest,
    extracted_text: str,
    manifest_path: str = "",
    extraction_source: str = "",
) -> ValidationResult:
    """Simplified validation from plain text only.

    Use this when you only have extracted text, not structured extraction.
    Grid recovery and contamination detection will be limited.

    Args:
        manifest: TruthManifest from clone builder
        extracted_text: Full extracted text
        manifest_path: Path to manifest file
        extraction_source: Path to extracted PDF

    Returns:
        ValidationResult (grid and contamination will be empty)
    """
    extracted_qids_ordered = extract_qids_from_text(extracted_text)
    extracted_qids_set = set(extracted_qids_ordered)

    qid_recovery = validate_qid_recovery(manifest, extracted_qids_set)
    ordering = validate_ordering(manifest, extracted_qids_ordered)

    return ValidationResult(
        manifest_path=manifest_path,
        extraction_source=extraction_source,
        qid_recovery=qid_recovery,
        ordering=ordering,
        grid_recoveries=[],
        contamination=ContaminationResult(total_qids=len(manifest.objects), contaminated=0),
    )
