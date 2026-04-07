# render_page() Text Rendering — Font Encoding Fix

## Status
Page rendering partially works after XObject key fix (`d191635`). Paths/lines render correctly. Simple ASCII digits appear. Most body text is invisible.

## Root Cause
The text rasterizer (`src/rendering/text_rasterizer.rs`) receives raw bytes from the PDF content stream and tries to look them up as Unicode characters in a system fallback font via `face.glyph_index(ch)`. This works for digits and basic ASCII but fails for most text because:

- PDF fonts use custom encodings (WinAnsi, MacRoman, Identity-H, custom Differences arrays)
- The raw bytes are character codes in the PDF font's encoding, NOT Unicode code points
- The system font expects Unicode, so `glyph_index('S')` works but `glyph_index(char_from_byte_0xC0)` maps to the wrong glyph or returns None

## What Already Works
- **Text extraction** (`extract_spans()`) correctly decodes all text — 149 spans with full readable text from NIST SP 800-171
- The decode path exists: `FontInfo::char_to_unicode()` at `src/fonts/font_dict.rs:1855`
- The full decode function: `decode_text_to_unicode()` at `src/extractors/text.rs:1743`
- `FontInfo` struct has `encoding`, `to_unicode` (CMap), `embedded_font_data`, `truetype_cmap`
- System fonts are loaded via `fontdb` and glyph outlines work via `ttf-parser::OutlineBuilder`
- The `GlyphPathBuilder` in text_rasterizer.rs correctly converts ttf-parser callbacks to tiny-skia paths

## What Needs to Change

### 1. Load FontInfo in the renderer when Tf operator is processed
In `src/rendering/page_renderer.rs`, the `Tf` (set font) operator currently only stores the font name and size in `GraphicsState`. It needs to also resolve the font dictionary from page resources and create/cache a `FontInfo`.

Look at how the text extractor does this — search for `Tf` handling in `src/extractors/text.rs`. It resolves the font name from the `/Font` subdictionary of page resources.

### 2. Pass FontInfo to the text rasterizer
Change the `render_text()` and `render_text_glyphs()` signatures in `text_rasterizer.rs` to accept `Option<&FontInfo>`.

### 3. Decode bytes through FontInfo before glyph lookup
In `render_text_glyphs()`, instead of:
```rust
let text_str: String = match std::str::from_utf8(text) {
    Ok(s) => s.to_string(),
    Err(_) => text.iter().map(|&b| b as char).collect(),
};
// ... then face.glyph_index(ch)
```

Do:
```rust
for byte in text {
    let char_code = *byte as u32;
    let unicode_str = if let Some(font) = font_info {
        font.char_to_unicode(char_code).unwrap_or_else(|| String::from(char::REPLACEMENT_CHARACTER))
    } else {
        String::from(*byte as char)
    };
    for ch in unicode_str.chars() {
        let glyph_id = face.glyph_index(ch);
        // ... render glyph
    }
}
```

### 4. Handle Type0 (CID) fonts
Type0 fonts use 2-byte character codes. The text rasterizer currently iterates one byte at a time. For Type0 fonts (where `FontInfo.subtype == "Type0"`), bytes should be consumed in pairs: `(bytes[i] << 8) | bytes[i+1]` as the char_code passed to `char_to_unicode()`.

## Key Files
| File | What's there | What needs changing |
|------|-------------|-------------------|
| `src/rendering/page_renderer.rs:435` | Tf handler — stores font name/size | Add FontInfo resolution from resources |
| `src/rendering/text_rasterizer.rs` | Glyph rendering with fontdb+ttf-parser | Accept FontInfo, decode through char_to_unicode |
| `src/fonts/font_dict.rs:1855` | `char_to_unicode()` — the decode function | Already works, just needs to be called |
| `src/extractors/text.rs:1743` | `decode_text_to_unicode()` — full decode | Reference implementation |
| `src/fonts/font_dict.rs:19` | `FontInfo` struct definition | No changes needed |

## Fixed Bugs in This Session
- **XObject key typo** (`d191635`): `"XObjects"` (plural) should be `"XObject"` (singular) in page_renderer.rs:576. This was the main blocker — Form XObjects containing all text were silently skipped. Fixed with fallback: `res_dict.get("XObject").or(res_dict.get("XObjects"))`.

## Test
```bash
# Current: renders paths/lines, digits "5" "6" visible, body text invisible
.venv/bin/python -c "
import pdf_oxide
doc = pdf_oxide.PdfDocument('/mnt/storage12tb/extractor_corpus/nist/nist_sp_800_171.pdf')
img = doc.render_page(11, dpi=150)
with open('/tmp/test_render.png', 'wb') as f:
    f.write(img)
"

# Compare with poppler (renders correctly):
pdftoppm -png -f 12 -l 12 -r 150 /mnt/storage12tb/extractor_corpus/nist/nist_sp_800_171.pdf /tmp/poppler_test

# After fix: both should produce readable text
```

## Dependencies Already in Cargo.toml
- `fontdb = "0.23"` — system font database
- `ttf-parser = "0.25"` — TrueType/OpenType glyph outline extraction
- `tiny-skia` — 2D rasterizer (path → pixel)
- `rustybuzz = "0.20"` — text shaping (not yet used, available for complex scripts)

## Existing Tests
99 passed, 3 skipped after the XObject fix. No rendering-specific tests yet. A good test would render a simple PDF with known text and verify pixel regions are non-white where text should appear.
