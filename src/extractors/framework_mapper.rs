//! Framework control reference extraction (S12 replacement).
//!
//! Extracts control references (NIST, SPARTA, CWE, ATT&CK, D3FEND, etc.)
//! from text using a 2-tier approach:
//!   Tier 1 — Regex: Wide-net pattern matching for control ID candidates
//!   Tier 2 — Fuzzy: Match candidates against a control catalog via strsim
//!
//! The catalog is loaded once (from Python/JSON), then all matching runs in Rust.
//! ArangoDB upserts remain in Python — this module handles the CPU-intensive matching.

use regex::Regex;
use std::collections::{HashMap, HashSet};

/// A candidate control reference found by regex.
#[derive(Debug, Clone)]
pub struct ControlCandidate {
    pub candidate: String,
    pub start: usize,
    pub end: usize,
    pub context_window: String,
}

/// A resolved control match after catalog lookup.
#[derive(Debug, Clone)]
pub struct ControlMatch {
    pub candidate: String,
    pub control_id: String,
    pub control_key: String,
    pub framework: String,
    pub confidence: f32,
    pub method: String, // "exact", "parent_exact", "fuzzy"
    pub context_window: String,
}

/// Result of processing a single chunk of text.
#[derive(Debug, Clone)]
pub struct ChunkMappingResult {
    pub chunk_key: String,
    pub is_requirement: bool,
    pub candidates_found: usize,
    pub matches: Vec<ControlMatch>,
}

/// Statistics from a mapping run.
#[derive(Debug, Clone, Default)]
pub struct MappingStats {
    pub chunks_processed: usize,
    pub chunks_with_candidates: usize,
    pub chunks_with_matches: usize,
    pub requirement_chunks: usize,
    pub total_candidates: usize,
    pub exact_matches: usize,
    pub parent_exact_matches: usize,
    pub fuzzy_matches: usize,
    pub unmatched: usize,
}

// ---------------------------------------------------------------------------
// Control catalog
// ---------------------------------------------------------------------------

/// In-memory control catalog for Tier 2 matching.
/// Loaded from Python (which queries ArangoDB), then used for all matching.
pub struct ControlCatalog {
    /// control_id (case-normalized) → _key
    exact: HashMap<String, String>,
    /// control_id → source_framework
    frameworks: HashMap<String, String>,
    /// All control IDs for fuzzy matching
    ids: Vec<String>,
    /// Uppercase versions for case-insensitive exact match
    upper_map: HashMap<String, String>,
}

impl ControlCatalog {
    pub fn new() -> Self {
        Self {
            exact: HashMap::new(),
            frameworks: HashMap::new(),
            ids: Vec::new(),
            upper_map: HashMap::new(),
        }
    }

    /// Load catalog entries. Called from Python with data from ArangoDB.
    pub fn load(&mut self, entries: Vec<(String, String, String)>) {
        // entries: Vec<(control_id, _key, source_framework)>
        for (cid, key, framework) in entries {
            self.upper_map.insert(cid.to_uppercase(), key.clone());
            self.exact.insert(cid.clone(), key);
            if !framework.is_empty() {
                self.frameworks.insert(cid.clone(), framework);
            }
            self.ids.push(cid);
        }
    }

    pub fn len(&self) -> usize {
        self.ids.len()
    }

    pub fn is_empty(&self) -> bool {
        self.ids.is_empty()
    }

    /// Exact match (case-insensitive).
    fn exact_match(&self, candidate: &str) -> Option<(String, String, f32)> {
        if let Some(key) = self.exact.get(candidate) {
            return Some((candidate.to_string(), key.clone(), 1.0));
        }
        let upper = candidate.to_uppercase();
        if let Some(key) = self.upper_map.get(&upper) {
            // Find the original-case ID
            let cid = self
                .ids
                .iter()
                .find(|id| id.to_uppercase() == upper)
                .cloned()
                .unwrap_or_else(|| upper.clone());
            return Some((cid, key.clone(), 1.0));
        }
        None
    }

    /// Fuzzy match against catalog using normalized Levenshtein.
    fn fuzzy_match(&self, candidate: &str, threshold: f32) -> Option<(String, String, f32)> {
        if self.ids.is_empty() {
            return None;
        }
        let upper = candidate.to_uppercase();
        let mut best_score: f64 = 0.0;
        let mut best_id: Option<&str> = None;

        for id in &self.ids {
            let score = strsim::normalized_levenshtein(&upper, &id.to_uppercase());
            if score > best_score {
                best_score = score;
                best_id = Some(id);
            }
        }

        let score_f32 = best_score as f32 * 100.0; // Convert to 0-100 scale like rapidfuzz
        if score_f32 < threshold {
            return None;
        }

        let matched_id = best_id?;
        let key = self.exact.get(matched_id)?.clone();

        // Confidence scales from 0.70 at threshold to 0.95 at 100
        let confidence = 0.70 + (score_f32 - threshold) / (100.0 - threshold) * 0.25;
        Some((matched_id.to_string(), key, confidence.min(0.95)))
    }

    /// Resolve a candidate: exact → parent_exact → fuzzy.
    pub fn resolve(
        &self,
        candidate: &str,
        fuzz_threshold: f32,
    ) -> Option<(String, String, f32, String)> {
        // Try exact
        if let Some((cid, key, conf)) = self.exact_match(candidate) {
            return Some((cid, key, conf, "exact".to_string()));
        }

        // Try parent for enhancement controls: AC-2(3) → AC-2
        let base_id = strip_enhancement(candidate);
        if base_id != candidate {
            if let Some((cid, key, conf)) = self.exact_match(&base_id) {
                return Some((cid, key, conf * 0.85, "parent_exact".to_string()));
            }
        }

        // Fall back to fuzzy
        if let Some((cid, key, conf)) = self.fuzzy_match(candidate, fuzz_threshold) {
            return Some((cid, key, conf, "fuzzy".to_string()));
        }

        None
    }

    pub fn get_framework(&self, control_id: &str) -> &str {
        self.frameworks
            .get(control_id)
            .map(|s| s.as_str())
            .unwrap_or("UNKNOWN")
    }
}

/// Strip enhancement suffix: "AC-2(3)" → "AC-2"
fn strip_enhancement(s: &str) -> String {
    if let Some(pos) = s.rfind('(') {
        if s.ends_with(')') {
            let inner = &s[pos + 1..s.len() - 1];
            if inner.chars().all(|c| c.is_ascii_digit()) {
                return s[..pos].to_string();
            }
        }
    }
    s.to_string()
}

// ---------------------------------------------------------------------------
// Tier 1: Regex extraction
// ---------------------------------------------------------------------------

lazy_static::lazy_static! {
    static ref CANDIDATE_PATTERNS: Vec<Regex> = vec![
        // NIST SP 800-53: AC-2, AC-2(3), SI-7(1)
        Regex::new(r"\b([A-Z]{2}-\d{1,3}(?:\(\d{1,2}\))?)").unwrap(),
        // CWE: CWE-787
        Regex::new(r"\b(CWE-\d{1,5})\b").unwrap(),
        // ATT&CK: T1078, T1078.001
        Regex::new(r"\b(T\d{4}(?:\.\d{3})?)\b").unwrap(),
        // SPARTA threats: SV-SP-1, SV-AC-3(2)
        Regex::new(r"\b(SV-[A-Z]{2}-\d+(?:\(\d+\))?)").unwrap(),
        // SPARTA techniques: REC-0001, DE-0010.03
        Regex::new(r"\b((?:REC|DE|EX|EXF|IMP|PER|LM|RD|IA|ST)\d{4}(?:\.\d{2})?)\b").unwrap(),
        Regex::new(r"\b((?:REC|DE|EX|EXF|IMP|PER|LM|RD)-\d{4}(?:\.\d{2})?)\b").unwrap(),
        // SPARTA countermeasures: CM0001
        Regex::new(r"\b(CM\d{4})\b").unwrap(),
        // D3FEND: D3-SPE, D3-AI
        Regex::new(r"\b(D3-[A-Z]{2,8})\b").unwrap(),
        // D3FEND artifacts: d3f:DigitalArtifact
        Regex::new(r"\b(d3f:[A-Za-z]+)\b").unwrap(),
        // ESA: ESA-T2040
        Regex::new(r"\b(ESA-[A-Z]\d{4})\b").unwrap(),
        // ISO: A.5.1, A.8.2
        Regex::new(r"\b(A\.\d{1,2}\.\d{1,2})\b").unwrap(),
        // CAPEC: CAPEC-123
        Regex::new(r"\b(CAPEC-\d{1,5})\b").unwrap(),
    ];

    /// RFC 2119 modal verbs for requirement detection
    static ref MODAL_PATTERN: Regex = Regex::new(
        r"(?i)\b(shall|must|will|required to|is required)\b"
    ).unwrap();
}

/// Extract context window around a match.
fn context_window(text: &str, start: usize, end: usize, window: usize) -> String {
    let ctx_start = start.saturating_sub(window);
    let ctx_end = (end + window).min(text.len());
    // Ensure we don't split a UTF-8 codepoint
    let s = &text[..text.len()];
    let safe_start = s.floor_char_boundary(ctx_start);
    let safe_end = s.ceil_char_boundary(ctx_end);
    s[safe_start..safe_end].trim().to_string()
}

/// Tier 1: Extract all candidate control references via regex.
pub fn extract_candidates(text: &str) -> Vec<ControlCandidate> {
    let mut results = Vec::new();
    let mut seen: HashSet<(String, usize)> = HashSet::new();

    for pattern in CANDIDATE_PATTERNS.iter() {
        for cap in pattern.find_iter(text) {
            let candidate = cap.as_str().to_string();
            let dedup_key = (candidate.to_uppercase(), cap.start());
            if seen.contains(&dedup_key) {
                continue;
            }
            seen.insert(dedup_key);

            results.push(ControlCandidate {
                candidate,
                start: cap.start(),
                end: cap.end(),
                context_window: context_window(text, cap.start(), cap.end(), 200),
            });
        }
    }

    results
}

/// Check if text contains RFC 2119 requirement modals.
pub fn is_requirement_text(text: &str) -> bool {
    MODAL_PATTERN.is_match(text)
}

// ---------------------------------------------------------------------------
// Batch processing
// ---------------------------------------------------------------------------

/// Process a batch of chunks, extracting control references and matching against catalog.
pub fn map_controls(
    chunks: &[(String, String, bool)], // (chunk_key, text, is_requirement)
    catalog: &ControlCatalog,
    fuzz_threshold: f32,
) -> (Vec<ChunkMappingResult>, MappingStats) {
    let mut results = Vec::new();
    let mut stats = MappingStats::default();

    for (chunk_key, text, is_requirement) in chunks {
        if text.is_empty() || chunk_key.is_empty() {
            continue;
        }

        stats.chunks_processed += 1;
        if *is_requirement {
            stats.requirement_chunks += 1;
        }

        let candidates = extract_candidates(text);
        if candidates.is_empty() {
            continue;
        }

        stats.chunks_with_candidates += 1;
        stats.total_candidates += candidates.len();

        let mut matches = Vec::new();
        let mut seen_controls: HashSet<String> = HashSet::new();

        for cand in &candidates {
            let resolved = catalog.resolve(&cand.candidate, fuzz_threshold);
            match resolved {
                None => {
                    stats.unmatched += 1;
                },
                Some((control_id, control_key, confidence, method)) => {
                    if seen_controls.contains(&control_id) {
                        continue;
                    }
                    seen_controls.insert(control_id.clone());

                    match method.as_str() {
                        "exact" => stats.exact_matches += 1,
                        "parent_exact" => stats.parent_exact_matches += 1,
                        "fuzzy" => stats.fuzzy_matches += 1,
                        _ => {},
                    }

                    let framework = catalog.get_framework(&control_id).to_string();

                    matches.push(ControlMatch {
                        candidate: cand.candidate.clone(),
                        control_id,
                        control_key,
                        framework,
                        confidence,
                        method,
                        context_window: cand.context_window.clone(),
                    });
                },
            }
        }

        if !matches.is_empty() {
            stats.chunks_with_matches += 1;
        }

        results.push(ChunkMappingResult {
            chunk_key: chunk_key.clone(),
            is_requirement: *is_requirement,
            candidates_found: candidates.len(),
            matches,
        });
    }

    (results, stats)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_extract_nist_controls() {
        let text = "The system shall implement AC-2 and AC-2(3) for account management.";
        let candidates = extract_candidates(text);
        let ids: Vec<&str> = candidates.iter().map(|c| c.candidate.as_str()).collect();
        assert!(ids.contains(&"AC-2"));
        assert!(ids.contains(&"AC-2(3)"));
    }

    #[test]
    fn test_extract_cwe() {
        let text = "This addresses CWE-787 (out-of-bounds write) and CWE-79.";
        let candidates = extract_candidates(text);
        let ids: Vec<&str> = candidates.iter().map(|c| c.candidate.as_str()).collect();
        assert!(ids.contains(&"CWE-787"));
        assert!(ids.contains(&"CWE-79"));
    }

    #[test]
    fn test_extract_attack() {
        let text = "Adversaries may use T1078 and T1078.001 for initial access.";
        let candidates = extract_candidates(text);
        let ids: Vec<&str> = candidates.iter().map(|c| c.candidate.as_str()).collect();
        assert!(ids.contains(&"T1078"));
        assert!(ids.contains(&"T1078.001"));
    }

    #[test]
    fn test_extract_sparta() {
        let text = "SV-SP-1 covers space platform security. CM0008 is the countermeasure.";
        let candidates = extract_candidates(text);
        let ids: Vec<&str> = candidates.iter().map(|c| c.candidate.as_str()).collect();
        assert!(ids.contains(&"SV-SP-1"));
        assert!(ids.contains(&"CM0008"));
    }

    #[test]
    fn test_extract_d3fend() {
        let text = "Apply D3-SPE (stack pointer examination) as a defensive technique.";
        let candidates = extract_candidates(text);
        let ids: Vec<&str> = candidates.iter().map(|c| c.candidate.as_str()).collect();
        assert!(ids.contains(&"D3-SPE"));
    }

    #[test]
    fn test_catalog_exact_match() {
        let mut catalog = ControlCatalog::new();
        catalog.load(vec![
            ("AC-2".to_string(), "nist_ac_2".to_string(), "NIST".to_string()),
            ("CWE-787".to_string(), "cwe_787".to_string(), "CWE".to_string()),
        ]);

        let result = catalog.resolve("AC-2", 85.0);
        assert!(result.is_some());
        let (cid, key, conf, method) = result.unwrap();
        assert_eq!(cid, "AC-2");
        assert_eq!(key, "nist_ac_2");
        assert_eq!(conf, 1.0);
        assert_eq!(method, "exact");
    }

    #[test]
    fn test_catalog_parent_exact() {
        let mut catalog = ControlCatalog::new();
        catalog.load(vec![("AC-2".to_string(), "nist_ac_2".to_string(), "NIST".to_string())]);

        // AC-2(3) should match parent AC-2 with reduced confidence
        let result = catalog.resolve("AC-2(3)", 85.0);
        assert!(result.is_some());
        let (cid, _key, conf, method) = result.unwrap();
        assert_eq!(cid, "AC-2");
        assert!(conf < 1.0); // Reduced for parent match
        assert_eq!(method, "parent_exact");
    }

    #[test]
    fn test_catalog_fuzzy_match() {
        let mut catalog = ControlCatalog::new();
        catalog.load(vec![
            ("AC-2".to_string(), "nist_ac_2".to_string(), "NIST".to_string()),
            ("SI-7".to_string(), "nist_si_7".to_string(), "NIST".to_string()),
        ]);

        // "AC-2" vs "AC-2" should be exact, but "ac-2" (lowercase) should also work via exact
        let result = catalog.resolve("ac-2", 85.0);
        assert!(result.is_some());
    }

    #[test]
    fn test_is_requirement_text() {
        assert!(is_requirement_text("The system shall implement logging."));
        assert!(is_requirement_text("Users MUST authenticate before access."));
        assert!(is_requirement_text("This will be enforced."));
        assert!(!is_requirement_text("This is a description of the system."));
    }

    #[test]
    fn test_strip_enhancement() {
        assert_eq!(strip_enhancement("AC-2(3)"), "AC-2");
        assert_eq!(strip_enhancement("SI-7(1)"), "SI-7");
        assert_eq!(strip_enhancement("AC-2"), "AC-2");
        assert_eq!(strip_enhancement("CWE-787"), "CWE-787");
    }

    #[test]
    fn test_batch_mapping() {
        let mut catalog = ControlCatalog::new();
        catalog.load(vec![
            ("AC-2".to_string(), "nist_ac_2".to_string(), "NIST".to_string()),
            ("CWE-787".to_string(), "cwe_787".to_string(), "CWE".to_string()),
        ]);

        let chunks = vec![
            (
                "chunk_1".to_string(),
                "Implement AC-2 for account management.".to_string(),
                true,
            ),
            ("chunk_2".to_string(), "No controls here.".to_string(), false),
            ("chunk_3".to_string(), "Mitigate CWE-787 buffer overflow.".to_string(), false),
        ];

        let (results, stats) = map_controls(&chunks, &catalog, 85.0);
        assert_eq!(stats.chunks_processed, 3);
        assert_eq!(stats.chunks_with_candidates, 2);
        assert_eq!(stats.chunks_with_matches, 2);
        assert_eq!(stats.exact_matches, 2);
        assert_eq!(stats.requirement_chunks, 1);

        // chunk_1 should have AC-2
        assert_eq!(results[0].matches.len(), 1);
        assert_eq!(results[0].matches[0].control_id, "AC-2");

        // chunk_3 should have CWE-787
        let chunk3 = results.iter().find(|r| r.chunk_key == "chunk_3").unwrap();
        assert_eq!(chunk3.matches[0].control_id, "CWE-787");
    }

    #[test]
    fn test_context_window() {
        let text = "The quick brown fox jumps over the lazy dog";
        let ctx = context_window(text, 10, 15, 5);
        assert!(ctx.len() > 5);
        assert!(ctx.len() <= 20);
    }

    #[test]
    fn test_dedup_candidates() {
        // Same control appearing twice should only be reported once per position
        let text = "AC-2 and AC-2 again.";
        let candidates = extract_candidates(text);
        // Each occurrence at a different position should be its own candidate
        assert_eq!(candidates.len(), 2);
    }
}
