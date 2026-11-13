//! PDF document outline (bookmarks) support.
//!
//! Provides access to the PDF document outline, also known as bookmarks,
//! which allow users to navigate a PDF document.

use crate::document::PdfDocument;
use crate::error::{Error, Result};
use crate::object::{Object, ObjectRef};
use std::collections::{HashMap, HashSet};

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
enum NamedDestinationKey {
    Name(String),
    String(Vec<u8>),
}

type NamedDestinations = HashMap<NamedDestinationKey, Object>;

/// Maximum indirection steps when resolving one destination.
const MAX_DESTINATION_DEPTH: u32 = 32;
/// Maximum nesting depth of the /Names /Dests name tree.
const MAX_NAME_TREE_DEPTH: u32 = 64;

/// Cycle and depth guard for one destination resolution.
#[derive(Default)]
struct DestinationResolveState {
    names: HashSet<NamedDestinationKey>,
    refs: HashSet<ObjectRef>,
    depth: u32,
}

/// A single outline item (bookmark) in the document hierarchy.
#[derive(Debug, Clone)]
pub struct OutlineItem {
    /// The title of this bookmark
    pub title: String,

    /// The destination (page number or named destination)
    /// None if destination cannot be determined
    pub dest: Option<Destination>,

    /// Child bookmarks under this item
    pub children: Vec<OutlineItem>,
}

/// Destination in the PDF
#[derive(Debug, Clone)]
pub enum Destination {
    /// Direct page reference (page index, 0-based)
    PageIndex(usize),

    /// Named destination (string identifier)
    Named(String),
}

impl PdfDocument {
    /// Get the document outline (bookmarks) if present.
    ///
    /// Returns a hierarchical structure of bookmarks that can be used
    /// for document navigation.
    ///
    /// # Returns
    ///
    /// - `Ok(Some(Vec<OutlineItem>))` - Bookmarks found and parsed
    /// - `Ok(None)` - No bookmarks in document
    /// - `Err` - Error parsing bookmarks
    ///
    /// # Example
    ///
    /// ```no_run
    /// use pdf_oxide::PdfDocument;
    ///
    /// let mut doc = PdfDocument::open("sample.pdf")?;
    /// if let Some(outline) = doc.get_outline()? {
    ///     for item in outline {
    ///         println!("Bookmark: {}", item.title);
    ///     }
    /// }
    /// # Ok::<(), pdf_oxide::error::Error>(())
    /// ```
    pub fn get_outline(&mut self) -> Result<Option<Vec<OutlineItem>>> {
        // Get catalog
        let catalog = self.catalog()?;

        // Check if catalog has /Outlines entry
        let outlines_ref = match catalog.as_dict() {
            Some(dict) => match dict.get("Outlines") {
                Some(Object::Reference(obj_ref)) => *obj_ref,
                _ => return Ok(None),
            },
            None => return Ok(None),
        };

        // Load the outlines dictionary
        let outlines_dict = self.load_object(outlines_ref)?;

        // Get the first outline item
        let first_ref = match outlines_dict.as_dict() {
            Some(dict) => match dict.get("First") {
                Some(Object::Reference(obj_ref)) => *obj_ref,
                _ => return Ok(None),
            },
            None => return Ok(None),
        };

        // Parse outline items at root level
        let mut items = Vec::new();
        let mut current_ref = Some(first_ref);
        let mut visited = HashSet::new();
        let page_index_map = self.build_page_index_map()?;
        // Best-effort: a malformed /Dests or /Names tree must not prevent the
        // outline itself from being returned.
        let named_destinations = self.build_named_destinations().unwrap_or_default();

        while let Some(item_ref) = current_ref {
            if !visited.insert(item_ref) {
                break;
            }

            if let Ok(item) = self.parse_outline_item(
                item_ref,
                &mut visited,
                &page_index_map,
                &named_destinations,
            ) {
                items.push(item);
            }

            // Get next sibling
            let item_obj = self.load_object(item_ref)?;
            current_ref = match item_obj.as_dict() {
                Some(dict) => match dict.get("Next") {
                    Some(Object::Reference(obj_ref)) => Some(*obj_ref),
                    _ => None,
                },
                None => None,
            };
        }

        if items.is_empty() {
            Ok(None)
        } else {
            Ok(Some(items))
        }
    }

    /// Parse a single outline item and its children recursively.
    fn parse_outline_item(
        &mut self,
        item_ref: ObjectRef,
        visited: &mut HashSet<ObjectRef>,
        page_index_map: &HashMap<ObjectRef, usize>,
        named_destinations: &NamedDestinations,
    ) -> Result<OutlineItem> {
        let item_obj = self.load_object(item_ref)?;

        // Extract and decode the required text string (§12.3.3, Table 153).
        // Although direct strings are preferred, real producers commonly store
        // /Title as an indirect string object.
        let title = match item_obj.as_dict().and_then(|dict| dict.get("Title")) {
            Some(Object::String(bytes)) => self.decode_pdf_text_string_for_object(bytes, item_ref),
            Some(Object::Reference(title_ref)) => match self.load_object(*title_ref)? {
                Object::String(bytes) => self.decode_pdf_text_string_for_object(&bytes, *title_ref),
                _ => "(No Title)".to_string(),
            },
            _ => "(No Title)".to_string(),
        };

        // Extract destination
        let dest = self.parse_outline_destination(&item_obj, page_index_map, named_destinations)?;

        // Parse children if present
        let mut children = Vec::new();
        if let Some(dict) = item_obj.as_dict() {
            if let Some(Object::Reference(first_child_ref)) = dict.get("First") {
                let mut child_ref = Some(*first_child_ref);

                while let Some(c_ref) = child_ref {
                    if !visited.insert(c_ref) {
                        break;
                    }

                    if let Ok(child) =
                        self.parse_outline_item(c_ref, visited, page_index_map, named_destinations)
                    {
                        children.push(child);
                    }

                    // Get next sibling
                    let child_obj = self.load_object(c_ref)?;
                    child_ref = match child_obj.as_dict() {
                        Some(dict) => match dict.get("Next") {
                            Some(Object::Reference(obj_ref)) => Some(*obj_ref),
                            _ => None,
                        },
                        None => None,
                    };
                }
            }
        }

        Ok(OutlineItem {
            title,
            dest,
            children,
        })
    }

    /// Parse destination from an outline item.
    fn parse_outline_destination(
        &mut self,
        item: &Object,
        page_index_map: &HashMap<ObjectRef, usize>,
        named_destinations: &NamedDestinations,
    ) -> Result<Option<Destination>> {
        let dict = match item.as_dict() {
            Some(d) => d,
            None => return Ok(None),
        };

        // Try /Dest entry first
        if let Some(dest_obj) = dict.get("Dest") {
            return self.resolve_destination(dest_obj, page_index_map, named_destinations);
        }

        // Try /A (action) entry
        let mut action = dict.get("A");

        // Resolve indirect reference to action
        let obj;
        if let Some(Object::Reference(obj_ref)) = action {
            obj = self.load_object(*obj_ref)?;
            action = Some(&obj);
        }

        // Look for destination under /D key
        if let Some(Object::Dictionary(action)) = action {
            if let Some(dest_obj) = action.get("D") {
                return self.resolve_destination(dest_obj, page_index_map, named_destinations);
            }
        }

        Ok(None)
    }

    /// Resolve a destination object to a Destination enum.
    fn resolve_destination(
        &mut self,
        dest_obj: &Object,
        page_index_map: &HashMap<ObjectRef, usize>,
        named_destinations: &NamedDestinations,
    ) -> Result<Option<Destination>> {
        let mut state = DestinationResolveState::default();
        self.resolve_destination_inner(dest_obj, page_index_map, named_destinations, &mut state)
    }

    fn resolve_destination_inner(
        &mut self,
        dest_obj: &Object,
        page_index_map: &HashMap<ObjectRef, usize>,
        named_destinations: &NamedDestinations,
        state: &mut DestinationResolveState,
    ) -> Result<Option<Destination>> {
        state.depth += 1;
        if state.depth > MAX_DESTINATION_DEPTH {
            return Ok(None);
        }
        let visited_names = state;
        match dest_obj {
            Object::String(name) => {
                let key = NamedDestinationKey::String(name.clone());
                let display_name = Self::decode_pdf_text_string(name);
                if !visited_names.names.insert(key.clone()) {
                    return Ok(Some(Destination::Named(display_name)));
                }

                let resolved = named_destinations.get(&key).or_else(|| {
                    named_destinations.get(&NamedDestinationKey::Name(display_name.clone()))
                });
                match resolved {
                    Some(resolved) => self.resolve_destination_inner(
                        resolved,
                        page_index_map,
                        named_destinations,
                        visited_names,
                    ),
                    None => Ok(Some(Destination::Named(display_name))),
                }
            },

            Object::Name(name) => {
                let key = NamedDestinationKey::Name(name.clone());
                if !visited_names.names.insert(key.clone()) {
                    return Ok(Some(Destination::Named(name.clone())));
                }

                let resolved = named_destinations.get(&key).or_else(|| {
                    named_destinations.get(&NamedDestinationKey::String(name.as_bytes().to_vec()))
                });
                match resolved {
                    Some(resolved) => self.resolve_destination_inner(
                        resolved,
                        page_index_map,
                        named_destinations,
                        visited_names,
                    ),
                    None => Ok(Some(Destination::Named(name.clone()))),
                }
            },

            Object::Array(arr) if !arr.is_empty() => match &arr[0] {
                Object::Reference(page_ref) => Ok(page_index_map
                    .get(page_ref)
                    .copied()
                    .map(Destination::PageIndex)),
                _ => Ok(None),
            },

            // Named destination values may be dictionaries with a /D entry
            // rather than explicit destination arrays (§12.3.2.3).
            Object::Dictionary(dict) => match dict.get("D") {
                Some(destination) => self.resolve_destination_inner(
                    destination,
                    page_index_map,
                    named_destinations,
                    visited_names,
                ),
                None => Ok(None),
            },

            Object::Reference(dest_ref) => {
                // A destination that refers back to itself (e.g.
                // `6 0 obj << /D 6 0 R >>`) has no page; stop instead of
                // recursing until the stack overflows.
                if !visited_names.refs.insert(*dest_ref) {
                    return Ok(None);
                }
                let resolved = self.load_object(*dest_ref)?;
                self.resolve_destination_inner(
                    &resolved,
                    page_index_map,
                    named_destinations,
                    visited_names,
                )
            },

            _ => Ok(None),
        }
    }

    /// Build both named-destination namespaces defined by §12.3.2.3:
    /// the PDF 1.1 catalog /Dests dictionary and the PDF 1.2 /Names /Dests tree.
    fn build_named_destinations(&mut self) -> Result<NamedDestinations> {
        let catalog = self.catalog()?;
        let Some(catalog_dict) = catalog.as_dict() else {
            return Ok(HashMap::new());
        };

        let legacy_dests = catalog_dict.get("Dests").cloned();
        let names = catalog_dict.get("Names").cloned();
        let mut destinations = HashMap::new();

        if let Some(legacy_dests) = legacy_dests {
            let legacy_dests = self.resolve_indirect_object(&legacy_dests).unwrap_or(Object::Null);
            if let Some(dict) = legacy_dests.as_dict() {
                for (name, destination) in dict {
                    destinations
                        .insert(NamedDestinationKey::Name(name.clone()), destination.clone());
                }
            }
        }

        if let Some(names) = names {
            let names = self.resolve_indirect_object(&names).unwrap_or(Object::Null);
            let dests_root = names.as_dict().and_then(|dict| dict.get("Dests")).cloned();
            if let Some(dests_root) = dests_root {
                let mut visited = HashSet::new();
                self.collect_name_tree_destinations(
                    &dests_root,
                    &mut destinations,
                    &mut visited,
                    0,
                );
            }
        }

        Ok(destinations)
    }

    fn collect_name_tree_destinations(
        &mut self,
        node: &Object,
        destinations: &mut NamedDestinations,
        visited: &mut HashSet<ObjectRef>,
        depth: u32,
    ) {
        if depth > MAX_NAME_TREE_DEPTH {
            return;
        }
        let node = match node {
            Object::Reference(node_ref) => {
                if !visited.insert(*node_ref) {
                    return;
                }
                // Best-effort: an unreadable kid is skipped, not fatal.
                match self.load_object(*node_ref) {
                    Ok(node) => node,
                    Err(_) => return,
                }
            },
            other => other.clone(),
        };
        let Some(dict) = node.as_dict() else {
            return;
        };

        if let Some(Object::Array(entries)) = dict.get("Names") {
            for pair in entries.chunks_exact(2) {
                if let Object::String(name) = &pair[0] {
                    destinations
                        .entry(NamedDestinationKey::String(name.clone()))
                        .or_insert_with(|| pair[1].clone());
                }
            }
        }

        if let Some(Object::Array(kids)) = dict.get("Kids") {
            for kid in kids {
                self.collect_name_tree_destinations(kid, destinations, visited, depth + 1);
            }
        }
    }

    fn resolve_indirect_object(&mut self, object: &Object) -> Result<Object> {
        match object {
            Object::Reference(object_ref) => self.load_object(*object_ref),
            other => Ok(other.clone()),
        }
    }

    fn build_page_index_map(&mut self) -> Result<HashMap<ObjectRef, usize>> {
        let count = self.page_count()?;
        let mut page_index_map = HashMap::with_capacity(count);

        for i in 0..count {
            if let Ok(page_obj_ref) = self.get_page_ref(i) {
                page_index_map.insert(page_obj_ref, i);
            }
        }

        Ok(page_index_map)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_outline_item_creation() {
        let item = OutlineItem {
            title: "Chapter 1".to_string(),
            dest: Some(Destination::PageIndex(0)),
            children: vec![],
        };

        assert_eq!(item.title, "Chapter 1");
        assert!(matches!(item.dest, Some(Destination::PageIndex(0))));
        assert!(item.children.is_empty());
    }

    #[test]
    fn test_outline_hierarchy() {
        let child = OutlineItem {
            title: "Section 1.1".to_string(),
            dest: Some(Destination::PageIndex(1)),
            children: vec![],
        };

        let parent = OutlineItem {
            title: "Chapter 1".to_string(),
            dest: Some(Destination::PageIndex(0)),
            children: vec![child],
        };

        assert_eq!(parent.children.len(), 1);
        assert_eq!(parent.children[0].title, "Section 1.1");
    }
}
