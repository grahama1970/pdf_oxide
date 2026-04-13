"""Text synthesis + QID injection."""
from __future__ import annotations
import sys
import hashlib
from dataclasses import dataclass
from .schemas import BlockProposal, LayoutProposal


@dataclass
class SynthesizedBlock:
    """Block with synthetic text and QID."""
    proposal: BlockProposal
    logical_text: str   # synthetic content
    qid: str            # block-level QID
    rendered_text: str  # with QID marker inserted
    table_cells: list[list[str]] | None = None  # For tables: [row][col]
    cell_qids: list[list[str]] | None = None    # For tables: QID per cell


class QidAllocator:
    """Deterministic QID generator.

    Design (per Codex review):
    - SHA-256 with 16 hex chars (64 bits) to avoid birthday collisions
    - Semantic coords only (no counter) for stability across runs
    - Namespace versioning for future compatibility
    """

    VERSION = "v1"

    def __init__(self, doc_id: str, seed: int):
        self.doc_id = doc_id
        self.seed = seed
        self._assigned: dict[str, str] = {}  # semantic_key -> qid

    def allocate(self, page_num: int, block_id: str) -> str:
        """Generate deterministic QID for a block.

        Uses semantic coordinates (page, block_id) rather than counter
        to ensure stability across runs regardless of iteration order.
        """
        # Semantic key - stable across runs
        semantic_key = f"{self.VERSION}|{self.doc_id}|{self.seed}|p{page_num}|{block_id}"

        # Check cache for idempotency
        if semantic_key in self._assigned:
            return self._assigned[semantic_key]

        # SHA-256 with 16 hex chars (64 bits) - collision resistant
        h = hashlib.sha256(semantic_key.encode()).hexdigest()[:16]
        qid = f"QID_{h.upper()}"

        self._assigned[semantic_key] = qid
        return qid


class TextSynthesizer:
    """Generates synthetic text matching block profiles."""

    def __init__(self, seed: int = 42, domain: str = "government"):
        self.seed = seed
        self.domain = domain
        self._corpus: list[str] | None = None
        self._corpus_idx = 0

    def _load_corpus(self) -> list[str]:
        """Load corpus from /create-text skill text banks."""
        if self._corpus is not None:
            return self._corpus

        import json
        from pathlib import Path

        bank_dir = Path("/mnt/storage12tb/text_banks")
        corpus = []

        # Load from domain-specific bank first, then government, then any
        domains_to_try = [self.domain, "government", "nist", "engineering"]
        content_types = ["prose", "heading", "glossary", "table_cell"]

        for domain in domains_to_try:
            bank_file = bank_dir / f"{domain}.json"
            if bank_file.exists():
                try:
                    chunks = json.loads(bank_file.read_text())
                    for ct in content_types:
                        matching = [c["text"].strip() for c in chunks
                                    if c.get("content_type") == ct and c.get("text", "").strip()]
                        corpus.extend(matching[:200])  # Cap per type
                    if len(corpus) >= 500:
                        break
                except Exception:
                    continue

        if not corpus:
            # Last resort fallback
            corpus = [
                "The organization shall implement security controls.",
                "Access to information systems requires authorization.",
                "Security requirements apply to all personnel.",
            ] * 10

        self._corpus = corpus
        return self._corpus

    def _get_corpus_text(self, target_length: int) -> str:
        """Get corpus text approximately matching target length."""
        corpus = self._load_corpus()

        # Build text to approximate target length
        result = []
        current_len = 0
        attempts = 0

        while current_len < target_length and attempts < 50:
            chunk = corpus[self._corpus_idx % len(corpus)]
            self._corpus_idx += 1
            result.append(chunk)
            current_len += len(chunk) + 1
            attempts += 1

        text = " ".join(result)

        # Trim to approximate length
        if len(text) > target_length * 1.2:
            # Find word boundary near target
            cut = target_length
            while cut < len(text) and text[cut] not in " .,;:":
                cut += 1
            text = text[:cut].rstrip(" .,;:")

        return text

    def synthesize_block(self, block: BlockProposal) -> str:
        """Generate synthetic text for a block.

        Matches original LINE COUNT, not character count, to prevent overflow.
        For tables, returns a special marker - actual cell content is in synthesize_table_cells.
        """
        if block.block_type == "table":
            # Tables get cell-level text, not block-level
            return "[TABLE]"

        # Count original lines
        original_line_count = len(block.lines)

        # Estimate chars per line from block width and font size
        avg_char_width = block.dominant_size * 0.5
        chars_per_line = int(block.bbox.width / avg_char_width) if avg_char_width > 0 else 50

        # Target: same number of lines, each ~80% filled
        target_len = int(original_line_count * chars_per_line * 0.8)

        # Generate text matching approximate length
        if block.block_type == "heading":
            return self._get_corpus_text(min(target_len, 80))
        elif block.block_type in ("header", "footer"):
            return self._get_corpus_text(min(target_len, 60))
        else:
            return self._get_corpus_text(min(target_len, 500))

    def synthesize_table_cells(self, block: BlockProposal) -> list[list[str]]:
        """Generate synthetic text for each table cell.

        Returns 2D array [row][col] of cell text.
        """
        if not block.table:
            return []

        rows = block.table.rows
        cols = block.table.cols

        # Generate grid of cell content
        cells = []
        for r in range(rows):
            row_cells = []
            for c in range(cols):
                if r == 0:
                    # Header row - short labels
                    text = self._get_corpus_text(20)[:15]
                else:
                    # Body cells - vary length
                    text = self._get_corpus_text(40)[:30]
                row_cells.append(text)
            cells.append(row_cells)

        return cells


def synthesize_page(
    proposal: LayoutProposal,
    qid_allocator: QidAllocator,
    synthesizer: TextSynthesizer,
) -> list[SynthesizedBlock]:
    """Synthesize text for all blocks on a page."""
    results = []

    for block in proposal.blocks:
        logical_text = synthesizer.synthesize_block(block)
        qid = qid_allocator.allocate(proposal.page_num, block.id)

        # Insert QID at start of text (zero-width marker simulation)
        # In practice, QID could be invisible Unicode or special marker
        rendered_text = f"[{qid}]{logical_text}"

        # Handle tables with cell-level content and QIDs
        table_cells = None
        cell_qids = None
        if block.block_type == "table" and block.table:
            table_cells = synthesizer.synthesize_table_cells(block)
            # Allocate QID per cell
            cell_qids = []
            for r in range(block.table.rows):
                row_qids = []
                for c in range(block.table.cols):
                    cell_qid = qid_allocator.allocate(
                        proposal.page_num, f"{block.id}_r{r}c{c}"
                    )
                    row_qids.append(cell_qid)
                cell_qids.append(row_qids)

        results.append(SynthesizedBlock(
            proposal=block,
            logical_text=logical_text,
            qid=qid,
            rendered_text=rendered_text,
            table_cells=table_cells,
            cell_qids=cell_qids,
        ))

    return results
