//! Text and content extraction from PDF documents.
//!
//! Provides high-performance extraction of text, images, paths, and layout analysis.

pub mod ccitt_bilevel;
pub mod forms;
pub mod gap_statistics;
pub mod geometric_spacing;
pub mod hierarchical;
pub mod images;
pub mod page_labels;
pub mod paths;
pub mod pattern_detector;
pub mod structured;
pub mod synthetic_structure;
pub mod text;
pub mod block_classifier;
pub mod block_merger;
pub mod content_assembler;
pub mod framework_mapper;
pub mod table_merger;
pub mod document_extractor;
pub mod document_profiler;
pub mod engineering;
pub mod figure_detector;
pub mod prediction;
pub mod section_hierarchy;
pub mod text_normalizer;
pub mod xmp;

#[cfg(feature = "debug-span-merging")]
pub mod debug_span_merging;

pub use forms::{FieldType, FieldValue, FormExtractor, FormField};
pub use gap_statistics::{
    analyze_document_gaps, calculate_statistics, determine_adaptive_threshold, extract_gaps,
    AdaptiveThresholdConfig, AdaptiveThresholdResult, GapStatistics,
};
pub use geometric_spacing::{should_insert_space, SpaceInsertion, SpacingConfig};
pub use hierarchical::HierarchicalExtractor;
pub use images::{
    expand_inline_image_dict, extract_image_from_xobject, ColorSpace, ImageData, ImageMetadata,
    PdfImage, PixelFormat,
};
pub use page_labels::{PageLabelExtractor, PageLabelRange, PageLabelStyle};
pub use paths::{FillRule, PathExtractor};
pub use pattern_detector::{PatternDetector, PatternPreservationConfig};
pub use structured::{
    BoundingBox, DocumentElement, DocumentMetadata, ExtractorConfig, ListItem, StructuredDocument,
    StructuredExtractor, TextAlignment, TextStyle,
};
pub use synthetic_structure::{SyntheticStructureConfig, SyntheticStructureGenerator};
pub use text::{SpanMergingConfig, TextExtractionConfig, TextExtractor};
pub use xmp::{XmpExtractor, XmpMetadata};
