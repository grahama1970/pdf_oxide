"""Pipeline data types and configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class PipelineConfig:
    """All pipeline settings in one place."""

    # LLM
    scillm_api_base: str = os.getenv("SCILLM_API_BASE", "http://localhost:4010")
    vlm_model: str = os.getenv("CHUTES_VLM_MODEL", "chutes/vlm")
    text_model: str = os.getenv("CHUTES_TEXT_MODEL", "openai/gpt-4o")
    vlm_concurrency: int = int(os.getenv("VLM_CONCURRENCY", "6"))
    text_concurrency: int = int(os.getenv("TEXT_CONCURRENCY", "8"))
    vlm_timeout: int = int(os.getenv("VLM_TIMEOUT_SEC", "45"))

    # ArangoDB
    arango_url: str = os.getenv("ARANGO_URL", "http://127.0.0.1:8529")
    arango_db: str = os.getenv("ARANGO_DB", "memory")
    arango_user: str = os.getenv("ARANGO_USER", "root")
    arango_pass: str = os.getenv("ARANGO_PASS", "")

    # Embedding
    embedding_url: str = os.getenv(
        "EMBEDDING_SERVICE_URL", "http://127.0.0.1:8602"
    )
    embedding_dim: int = 384

    # Features (plugin system — opt-in capabilities)
    features: List[str] = field(default_factory=list)

    # Decryption
    decrypt_password: Optional[str] = None

    # Extraction
    table_flavor: str = "auto"
    line_scale: int = 15
    sync_to_arango: bool = bool(os.getenv("SYNC_TO_ARANGO", "1"))

    # Output
    output_dir: Optional[Path] = None


@dataclass
class PipelineResult:
    """Complete extraction result."""

    source_pdf: str
    page_count: int
    sections: List[Dict[str, Any]] = field(default_factory=list)
    blocks: List[Dict[str, Any]] = field(default_factory=list)
    tables: List[Dict[str, Any]] = field(default_factory=list)
    figures: List[Dict[str, Any]] = field(default_factory=list)
    requirements: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    timings: Dict[str, float] = field(default_factory=dict)
    flattened: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_pdf": self.source_pdf,
            "page_count": self.page_count,
            "sections": self.sections,
            "blocks": self.blocks,
            "tables": self.tables,
            "figures": self.figures,
            "requirements": self.requirements,
            "metadata": self.metadata,
            "timings": self.timings,
        }
