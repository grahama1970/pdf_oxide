"""Taxonomy plugin -- extract bridge tags and taxonomy features from chunks.

Scans chunk text for domain keywords and maps matches to bridge tags.
Updates chunks in ArangoDB with bridge_tags and taxonomy fields.
"""
from __future__ import annotations

from typing import Any, Dict, List, Set, TYPE_CHECKING

from .base import Plugin, registry
from ..pipeline_util import log

if TYPE_CHECKING:
    from ..pipeline_types import PipelineConfig, PipelineResult

# Bridge tag -> keyword list.  Lowercase for case-insensitive matching.
BRIDGE_TAGS: Dict[str, List[str]] = {
    "Vulnerability": [
        "vulnerability", "exploit", "attack surface", "weakness",
        "threat", "cve", "exposure", "susceptible",
    ],
    "Resilience": [
        "resilience", "resilient", "fault tolerance", "fault-tolerant",
        "redundancy", "failover", "recovery", "robustness",
    ],
    "Precision": [
        "precision", "accuracy", "tolerance", "calibration",
        "resolution", "fidelity", "exact", "measurement",
    ],
    "Stealth": [
        "stealth", "covert", "low observable", "signature reduction",
        "countermeasure", "evasion", "concealment", "undetectable",
    ],
    "Corruption": [
        "corruption", "data integrity", "bit flip", "checksum",
        "tamper", "tampered", "integrity violation", "malformed",
    ],
    "Fragility": [
        "fragility", "fragile", "brittle", "single point of failure",
        "spof", "degradation", "breakdown", "catastrophic failure",
    ],
    "Autonomy": [
        "autonomy", "autonomous", "self-governing", "unmanned",
        "automated decision", "machine decision", "ai-driven",
    ],
    "Interoperability": [
        "interoperability", "interoperable", "cross-platform",
        "interface", "integration", "compatibility", "protocol",
    ],
    "Compliance": [
        "compliance", "regulation", "regulatory", "certification",
        "standard", "guideline", "audit", "accreditation",
    ],
    "Sustainability": [
        "sustainability", "sustainable", "lifecycle", "life cycle",
        "environmental", "power consumption", "efficiency",
    ],
    "Safety": [
        "safety", "hazard", "risk mitigation", "fail-safe",
        "human safety", "mishap", "safeguard", "protective",
    ],
    "Security": [
        "security", "encryption", "authentication", "authorization",
        "access control", "cyber", "classified", "infosec",
    ],
}


def extract_bridge_tags(text: str) -> List[str]:
    """Return matching bridge tags for the given text via keyword scan."""
    text_lower = text.lower()
    matched: List[str] = []
    for tag, keywords in BRIDGE_TAGS.items():
        for kw in keywords:
            if kw in text_lower:
                matched.append(tag)
                break
    return sorted(matched)


class TaxonomyPlugin(Plugin):
    name = "taxonomy"
    depends_on = ("arango",)
    asset_types = ("Text", "Table", "Figure")
    description = "Extract bridge tags and taxonomy features"

    async def run_batch(self, result: "PipelineResult", config: "PipelineConfig") -> Dict[str, Any]:
        """Scan chunks in ArangoDB and apply bridge tags."""
        try:
            from arango import ArangoClient
        except ImportError:
            log("python-arango not installed -- skipping taxonomy")
            return {"tagged": 0, "skipped": 0, "reason": "python-arango not installed"}

        client = ArangoClient(hosts=config.arango_url)
        db = client.db(
            config.arango_db,
            username=config.arango_user,
            password=config.arango_pass,
        )

        if not db.has_collection("datalake_chunks"):
            log("datalake_chunks collection not found -- skipping taxonomy")
            return {"tagged": 0, "skipped": 0, "reason": "no chunks collection"}

        # Query chunks that are missing bridge_tags
        cursor = db.aql.execute(
            """
            FOR c IN datalake_chunks
                FILTER c.asset_type IN @types
                FILTER !HAS(c, "bridge_tags")
                RETURN {_key: c._key, text: c.text, asset_type: c.asset_type}
            """,
            bind_vars={"types": self.asset_types},
        )

        tagged = 0
        skipped = 0
        chunk_coll = db.collection("datalake_chunks")

        for doc in cursor:
            text = doc.get("text") or ""
            if not text.strip():
                skipped += 1
                continue

            tags = extract_bridge_tags(text)
            if not tags:
                # Still mark as processed so we don't re-scan
                chunk_coll.update(
                    {"_key": doc["_key"], "bridge_tags": [], "taxonomy": {}},
                )
                skipped += 1
                continue

            chunk_coll.update({
                "_key": doc["_key"],
                "bridge_tags": tags,
                "taxonomy": {
                    "domain": "space_systems",
                    "tags": tags,
                },
            })
            tagged += 1

        log(f"Taxonomy: tagged={tagged}, skipped={skipped}")
        return {"tagged": tagged, "skipped": skipped}


registry.register(TaxonomyPlugin())
