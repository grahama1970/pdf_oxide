//! Document repair API for fixing common PDF issues.
//!
//! This module provides a high-level repair interface that consolidates several
//! recovery strategies:
//!
//! - **Xref reconstruction**: Rebuilds the cross-reference table by scanning for
//!   object markers (delegates to [`crate::xref_reconstruction`]).
//! - **Stream length fixing**: Detects stream objects whose `/Length` entry does
//!   not match the actual `stream...endstream` byte span.
//! - **Page tree repair**: Validates the `/Pages` tree and rebuilds the `/Kids`
//!   array when entries are missing or broken.
//! - **Orphan removal**: Finds objects that are not reachable from the catalog
//!   and marks them for removal.
//! - **Broken reference fixing**: Replaces indirect references that point to
//!   non-existent objects with `null`.
//!
//! # Example
//!
//! ```no_run
//! use pdf_oxide::repair::{RepairOptions, RepairReport, repair_document};
//!
//! let report = repair_document("damaged.pdf", "repaired.pdf", &RepairOptions::default())
//!     .expect("repair failed");
//! println!("Fixed {} issues", report.total_fixes());
//! ```

use crate::document::PdfDocument;
use crate::error::{Error, Result};
use crate::object::{Object, ObjectRef};
use std::collections::{HashMap, HashSet, VecDeque};

/// Options controlling which repair strategies to apply.
#[derive(Debug, Clone)]
pub struct RepairOptions {
    /// Rebuild the cross-reference table by scanning for object markers.
    pub repair_xref: bool,
    /// Fix stream `/Length` entries that do not match actual stream data.
    pub repair_stream_lengths: bool,
    /// Validate and rebuild the `/Pages` tree if it is broken.
    pub repair_page_tree: bool,
    /// Remove objects not reachable from the document catalog.
    pub remove_orphans: bool,
    /// Replace references to non-existent objects with `null`.
    pub fix_broken_references: bool,
}

impl Default for RepairOptions {
    fn default() -> Self {
        Self {
            repair_xref: true,
            repair_stream_lengths: true,
            repair_page_tree: true,
            remove_orphans: true,
            fix_broken_references: true,
        }
    }
}

/// Report of what was fixed during a repair operation.
#[derive(Debug, Clone, Default)]
pub struct RepairReport {
    /// Whether the xref table was rebuilt.
    pub xref_rebuilt: bool,
    /// Number of stream `/Length` entries that were corrected.
    pub stream_lengths_fixed: usize,
    /// Whether the page tree was rebuilt.
    pub page_tree_rebuilt: bool,
    /// Number of orphan objects removed.
    pub orphan_objects_removed: usize,
    /// Number of broken references replaced with `null`.
    pub broken_references_fixed: usize,
}

impl RepairReport {
    /// Total number of individual fixes applied.
    pub fn total_fixes(&self) -> usize {
        let mut count = 0;
        if self.xref_rebuilt {
            count += 1;
        }
        count += self.stream_lengths_fixed;
        if self.page_tree_rebuilt {
            count += 1;
        }
        count += self.orphan_objects_removed;
        count += self.broken_references_fixed;
        count
    }
}

// ---------------------------------------------------------------------------
// Strategy: repair_xref
// ---------------------------------------------------------------------------

/// Rebuild the cross-reference table by scanning for `N G obj` markers.
///
/// Delegates to [`crate::xref_reconstruction::reconstruct_xref`]. Returns `true`
/// if a new xref was successfully built.
pub(crate) fn do_repair_xref(doc: &mut PdfDocument) -> Result<bool> {
    // The xref is already loaded. We attempt reconstruction only if needed.
    // Open the underlying reader and delegate.
    use crate::xref_reconstruction::reconstruct_xref;

    let reader = doc.reader_mut();
    match reconstruct_xref(reader) {
        Ok((new_xref, new_trailer)) => {
            doc.replace_xref(new_xref, new_trailer);
            log::info!("Xref table rebuilt successfully");
            Ok(true)
        },
        Err(e) => {
            log::warn!("Xref reconstruction failed: {}", e);
            Err(e)
        },
    }
}

// ---------------------------------------------------------------------------
// Strategy: repair_stream_lengths
// ---------------------------------------------------------------------------

/// Scan stream objects and fix `/Length` entries that do not match the actual
/// `stream...endstream` byte span.
pub(crate) fn do_repair_stream_lengths(doc: &mut PdfDocument) -> Result<usize> {
    let obj_nums: Vec<u32> = doc.xref().all_object_numbers().collect();
    let mut fixed = 0usize;

    for obj_num in obj_nums {
        let obj_ref = ObjectRef::new(obj_num, 0);
        let obj = match doc.load_object(obj_ref) {
            Ok(o) => o,
            Err(_) => continue,
        };

        if let Object::Stream { ref dict, ref data } = obj {
            let actual_len = data.len() as i64;
            let declared_len = dict
                .get("Length")
                .and_then(|l| l.as_integer())
                .unwrap_or(-1);

            if declared_len != actual_len {
                // Build corrected dictionary
                let mut new_dict = dict.clone();
                new_dict.insert("Length".to_string(), Object::Integer(actual_len));
                let corrected = Object::Stream {
                    dict: new_dict,
                    data: data.clone(),
                };
                doc.update_object(obj_num, corrected);
                fixed += 1;
                log::debug!(
                    "Fixed stream length for object {}: declared={}, actual={}",
                    obj_num,
                    declared_len,
                    actual_len
                );
            }
        }
    }

    Ok(fixed)
}

// ---------------------------------------------------------------------------
// Strategy: repair_page_tree
// ---------------------------------------------------------------------------

/// Validate the `/Pages` tree and rebuild the `/Kids` array when it contains
/// invalid references.
pub(crate) fn do_repair_page_tree(doc: &mut PdfDocument) -> Result<bool> {
    let catalog = doc.catalog()?;
    let catalog_dict = catalog
        .as_dict()
        .ok_or_else(|| Error::InvalidPdf("Catalog is not a dictionary".to_string()))?;

    let pages_ref = match catalog_dict.get("Pages").and_then(|p| p.as_reference()) {
        Some(r) => r,
        None => return Err(Error::InvalidPdf("Catalog missing /Pages reference".to_string())),
    };

    let pages_obj = doc.load_object(pages_ref)?;
    let pages_dict = match pages_obj.as_dict() {
        Some(d) => d.clone(),
        None => return Err(Error::InvalidPdf("/Pages is not a dictionary".to_string())),
    };

    let kids = match pages_dict.get("Kids").and_then(|k| k.as_array()) {
        Some(k) => k.clone(),
        None => {
            // No Kids at all -- nothing to rebuild
            return Ok(false);
        },
    };

    // Walk the kids and keep only those that resolve to valid page or pages objects
    let mut valid_kids = Vec::new();
    let mut removed = 0usize;

    for kid in &kids {
        if let Some(kid_ref) = kid.as_reference() {
            match doc.load_object(kid_ref) {
                Ok(kid_obj) => {
                    if let Some(d) = kid_obj.as_dict() {
                        let type_name = d.get("Type").and_then(|t| t.as_name()).unwrap_or("");
                        if type_name == "Page" || type_name == "Pages" {
                            valid_kids.push(kid.clone());
                            continue;
                        }
                    }
                    // Object exists but is not a page
                    removed += 1;
                    log::debug!("Removing non-page kid from /Pages: {}", kid_ref);
                },
                Err(_) => {
                    removed += 1;
                    log::debug!("Removing broken kid reference from /Pages: {}", kid_ref);
                },
            }
        } else {
            // Kids should be references; skip inline entries
            removed += 1;
        }
    }

    if removed == 0 {
        return Ok(false);
    }

    // Rebuild the pages dictionary with corrected Kids and Count
    let mut new_pages = pages_dict.clone();
    let count = count_leaf_pages(doc, &valid_kids);
    new_pages.insert("Kids".to_string(), Object::Array(valid_kids));
    new_pages.insert("Count".to_string(), Object::Integer(count as i64));
    doc.update_object(pages_ref.id, Object::Dictionary(new_pages));

    log::info!("Rebuilt page tree: removed {} invalid kids", removed);
    Ok(true)
}

/// Recursively count leaf /Page objects in a Kids array.
fn count_leaf_pages(doc: &mut PdfDocument, kids: &[Object]) -> usize {
    let mut count = 0;
    for kid in kids {
        if let Some(kid_ref) = kid.as_reference() {
            if let Ok(kid_obj) = doc.load_object(kid_ref) {
                if let Some(d) = kid_obj.as_dict() {
                    let type_name = d.get("Type").and_then(|t| t.as_name()).unwrap_or("");
                    if type_name == "Page" {
                        count += 1;
                    } else if type_name == "Pages" {
                        if let Some(sub_kids) = d.get("Kids").and_then(|k| k.as_array()) {
                            let sub = sub_kids.clone();
                            count += count_leaf_pages(doc, &sub);
                        }
                    }
                }
            }
        }
    }
    count
}

// ---------------------------------------------------------------------------
// Strategy: remove_orphan_objects
// ---------------------------------------------------------------------------

/// Find all objects reachable from the catalog and remove the rest.
pub(crate) fn do_remove_orphan_objects(doc: &mut PdfDocument) -> Result<usize> {
    let reachable = find_reachable_objects(doc)?;
    let all_nums: Vec<u32> = doc.xref().all_object_numbers().collect();

    let mut removed = 0usize;
    for obj_num in all_nums {
        if !reachable.contains(&obj_num) {
            doc.update_object(obj_num, Object::Null);
            removed += 1;
            log::debug!("Removed orphan object {}", obj_num);
        }
    }

    Ok(removed)
}

/// BFS from the trailer/catalog to collect all reachable object numbers.
fn find_reachable_objects(doc: &mut PdfDocument) -> Result<HashSet<u32>> {
    let mut reachable = HashSet::new();
    let mut queue: VecDeque<u32> = VecDeque::new();

    // Seed from trailer references
    let trailer = doc.trailer().clone();
    collect_refs_from_object(&trailer, &mut queue, &mut reachable);

    while let Some(obj_num) = queue.pop_front() {
        if reachable.contains(&obj_num) {
            // Already visited via collect_refs_from_object seeding; load and walk children
        }
        let obj_ref = ObjectRef::new(obj_num, 0);
        let obj = match doc.load_object(obj_ref) {
            Ok(o) => o,
            Err(_) => continue,
        };
        collect_refs_from_object(&obj, &mut queue, &mut reachable);
    }

    Ok(reachable)
}

/// Collect all indirect reference IDs from an object tree, adding newly
/// discovered ones to `queue`.
fn collect_refs_from_object(obj: &Object, queue: &mut VecDeque<u32>, seen: &mut HashSet<u32>) {
    match obj {
        Object::Reference(r) => {
            if seen.insert(r.id) {
                queue.push_back(r.id);
            }
        },
        Object::Array(arr) => {
            for item in arr {
                collect_refs_from_object(item, queue, seen);
            }
        },
        Object::Dictionary(dict) => {
            for val in dict.values() {
                collect_refs_from_object(val, queue, seen);
            }
        },
        Object::Stream { dict, .. } => {
            for val in dict.values() {
                collect_refs_from_object(val, queue, seen);
            }
        },
        _ => {},
    }
}

// ---------------------------------------------------------------------------
// Strategy: fix_broken_references
// ---------------------------------------------------------------------------

/// Find references to non-existent objects and replace them with null.
pub(crate) fn do_fix_broken_references(doc: &mut PdfDocument) -> Result<usize> {
    let all_nums: HashSet<u32> = doc.xref().all_object_numbers().collect();
    let obj_nums: Vec<u32> = all_nums.iter().copied().collect();
    let mut total_fixed = 0usize;

    for obj_num in obj_nums {
        let obj_ref = ObjectRef::new(obj_num, 0);
        let obj = match doc.load_object(obj_ref) {
            Ok(o) => o,
            Err(_) => continue,
        };

        let (fixed_obj, count) = fix_refs_in_object(&obj, &all_nums);
        if count > 0 {
            doc.update_object(obj_num, fixed_obj);
            total_fixed += count;
            log::debug!("Fixed {} broken reference(s) in object {}", count, obj_num);
        }
    }

    Ok(total_fixed)
}

/// Recursively walk an object and replace references to missing objects with Null.
/// Returns the (possibly modified) object and a count of replacements.
fn fix_refs_in_object(obj: &Object, valid_ids: &HashSet<u32>) -> (Object, usize) {
    match obj {
        Object::Reference(r) => {
            if valid_ids.contains(&r.id) {
                (obj.clone(), 0)
            } else {
                (Object::Null, 1)
            }
        },
        Object::Array(arr) => {
            let mut new_arr = Vec::with_capacity(arr.len());
            let mut count = 0;
            for item in arr {
                let (fixed, c) = fix_refs_in_object(item, valid_ids);
                count += c;
                new_arr.push(fixed);
            }
            (Object::Array(new_arr), count)
        },
        Object::Dictionary(dict) => {
            let mut new_dict = HashMap::with_capacity(dict.len());
            let mut count = 0;
            for (key, val) in dict {
                let (fixed, c) = fix_refs_in_object(val, valid_ids);
                count += c;
                new_dict.insert(key.clone(), fixed);
            }
            (Object::Dictionary(new_dict), count)
        },
        Object::Stream { dict, data } => {
            let mut new_dict = HashMap::with_capacity(dict.len());
            let mut count = 0;
            for (key, val) in dict {
                let (fixed, c) = fix_refs_in_object(val, valid_ids);
                count += c;
                new_dict.insert(key.clone(), fixed);
            }
            (
                Object::Stream {
                    dict: new_dict,
                    data: data.clone(),
                },
                count,
            )
        },
        _ => (obj.clone(), 0),
    }
}

// ---------------------------------------------------------------------------
// High-level API
// ---------------------------------------------------------------------------

/// Repair a document applying the selected strategies.
///
/// Operates on an already-opened `PdfDocument` in place.
pub fn repair(doc: &mut PdfDocument, options: &RepairOptions) -> Result<RepairReport> {
    let mut report = RepairReport::default();

    // 1. Xref reconstruction (must be first since other strategies depend on xref)
    if options.repair_xref {
        match do_repair_xref(doc) {
            Ok(rebuilt) => report.xref_rebuilt = rebuilt,
            Err(e) => log::warn!("Xref repair skipped: {}", e),
        }
    }

    // 2. Fix broken references (before orphan removal so we don't confuse reachability)
    if options.fix_broken_references {
        report.broken_references_fixed = do_fix_broken_references(doc)?;
    }

    // 3. Stream lengths
    if options.repair_stream_lengths {
        report.stream_lengths_fixed = do_repair_stream_lengths(doc)?;
    }

    // 4. Page tree
    if options.repair_page_tree {
        report.page_tree_rebuilt = do_repair_page_tree(doc)?;
    }

    // 5. Orphan removal (last, since earlier steps may have nullified objects)
    if options.remove_orphans {
        report.orphan_objects_removed = do_remove_orphan_objects(doc)?;
    }

    Ok(report)
}

/// Repair a PDF file on disk and write the result to `output_path`.
///
/// This is a convenience wrapper that opens the document, runs all selected
/// repair strategies, and saves the result.
#[cfg(not(target_arch = "wasm32"))]
pub fn repair_document(
    path: impl AsRef<std::path::Path>,
    output_path: impl AsRef<std::path::Path>,
    options: &RepairOptions,
) -> Result<RepairReport> {
    use crate::editor::{DocumentEditor, EditableDocument};

    let mut editor = DocumentEditor::open(path)?;
    let report = repair(editor.source_mut(), options)?;
    editor.save(output_path)?;
    Ok(report)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    /// Build a minimal in-memory PDF document for testing.
    fn make_test_doc() -> PdfDocument {
        // We use open_from_bytes with a minimal valid PDF
        let pdf_bytes = b"%PDF-1.4\n\
            1 0 obj\n\
            << /Type /Catalog /Pages 2 0 R >>\n\
            endobj\n\
            2 0 obj\n\
            << /Type /Pages /Count 1 /Kids [3 0 R] >>\n\
            endobj\n\
            3 0 obj\n\
            << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\n\
            endobj\n\
            xref\n\
            0 4\n\
            0000000000 65535 f \n\
            0000000009 00000 n \n\
            0000000058 00000 n \n\
            0000000115 00000 n \n\
            trailer\n\
            << /Root 1 0 R /Size 4 >>\n\
            startxref\n\
            183\n\
            %%EOF";

        PdfDocument::open_from_bytes(pdf_bytes.to_vec()).expect("test PDF should parse")
    }

    #[test]
    fn test_repair_options_default() {
        let opts = RepairOptions::default();
        assert!(opts.repair_xref);
        assert!(opts.repair_stream_lengths);
        assert!(opts.repair_page_tree);
        assert!(opts.remove_orphans);
        assert!(opts.fix_broken_references);
    }

    #[test]
    fn test_repair_report_total_fixes() {
        let report = RepairReport {
            xref_rebuilt: true,
            stream_lengths_fixed: 3,
            page_tree_rebuilt: false,
            orphan_objects_removed: 2,
            broken_references_fixed: 1,
        };
        assert_eq!(report.total_fixes(), 7); // 1 + 3 + 0 + 2 + 1
    }

    #[test]
    fn test_repair_report_empty() {
        let report = RepairReport::default();
        assert_eq!(report.total_fixes(), 0);
    }

    #[test]
    fn test_repair_clean_document() {
        // Repairing a valid document should not break anything
        let mut doc = make_test_doc();
        let opts = RepairOptions {
            repair_xref: false, // skip xref rebuild for clean doc
            repair_stream_lengths: true,
            repair_page_tree: true,
            remove_orphans: true,
            fix_broken_references: true,
        };
        let report = repair(&mut doc, &opts).expect("repair should succeed on clean doc");
        assert_eq!(report.stream_lengths_fixed, 0);
        assert!(!report.page_tree_rebuilt);
        assert_eq!(report.broken_references_fixed, 0);
    }

    #[test]
    fn test_fix_refs_in_object_with_valid_refs() {
        let mut valid = HashSet::new();
        valid.insert(1);
        valid.insert(2);

        let obj = Object::Array(vec![
            Object::Reference(ObjectRef::new(1, 0)),
            Object::Reference(ObjectRef::new(2, 0)),
        ]);

        let (fixed, count) = fix_refs_in_object(&obj, &valid);
        assert_eq!(count, 0);
        assert!(matches!(fixed, Object::Array(_)));
    }

    #[test]
    fn test_fix_refs_in_object_with_broken_refs() {
        let mut valid = HashSet::new();
        valid.insert(1);
        // Object 99 does not exist

        let obj = Object::Dictionary({
            let mut d = HashMap::new();
            d.insert("Good".to_string(), Object::Reference(ObjectRef::new(1, 0)));
            d.insert("Bad".to_string(), Object::Reference(ObjectRef::new(99, 0)));
            d
        });

        let (fixed, count) = fix_refs_in_object(&obj, &valid);
        assert_eq!(count, 1);
        if let Object::Dictionary(d) = &fixed {
            assert!(matches!(d.get("Bad"), Some(Object::Null)));
            assert!(matches!(d.get("Good"), Some(Object::Reference(_))));
        } else {
            panic!("Expected dictionary");
        }
    }

    #[test]
    fn test_fix_refs_in_stream() {
        let mut valid = HashSet::new();
        valid.insert(5);

        let obj = Object::Stream {
            dict: {
                let mut d = HashMap::new();
                d.insert("Ref".to_string(), Object::Reference(ObjectRef::new(999, 0)));
                d.insert("Length".to_string(), Object::Integer(0));
                d
            },
            data: bytes::Bytes::new(),
        };

        let (fixed, count) = fix_refs_in_object(&obj, &valid);
        assert_eq!(count, 1);
        if let Object::Stream { dict, .. } = &fixed {
            assert!(matches!(dict.get("Ref"), Some(Object::Null)));
        } else {
            panic!("Expected stream");
        }
    }

    #[test]
    fn test_collect_refs_from_object() {
        let obj = Object::Dictionary({
            let mut d = HashMap::new();
            d.insert("A".to_string(), Object::Reference(ObjectRef::new(1, 0)));
            d.insert("B".to_string(), Object::Array(vec![Object::Reference(ObjectRef::new(2, 0))]));
            d
        });

        let mut queue = VecDeque::new();
        let mut seen = HashSet::new();
        collect_refs_from_object(&obj, &mut queue, &mut seen);

        assert!(seen.contains(&1));
        assert!(seen.contains(&2));
        assert_eq!(seen.len(), 2);
    }
}
