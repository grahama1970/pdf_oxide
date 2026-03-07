//! Lattice parser: line-based table detection from rendered page images.
//!
//! Pipeline:
//! 1. Render page to grayscale image
//! 2. Adaptive threshold → binary image
//! 3. Morphological operations to isolate horizontal/vertical lines
//! 4. Find contours (table regions) and joints (line intersections)
//! 5. Build grid from joints → rows and columns
//! 6. Create Table with cells, assign text
//!
//! Uses `image` + `imageproc` crates (pure Rust, no OpenCV dependency).

use image::{GrayImage, Luma};
use crate::tables::types::{BBox, ExtractConfig, Flavor, Segment, Table, TextElement};
use crate::tables::text_assign::{assign_text_to_cells, compute_accuracy};

// -- Image processing primitives --

/// Adaptive threshold using local mean (Gaussian-like approximation).
///
/// For each pixel, compute the mean of a blocksize×blocksize neighborhood,
/// then threshold: pixel = 255 if src > mean + constant, else 0.
///
/// This is equivalent to cv2.adaptiveThreshold with ADAPTIVE_THRESH_MEAN_C.
pub fn adaptive_threshold(
    img: &GrayImage,
    blocksize: u32,
    constant: f64,
    invert: bool,
) -> GrayImage {
    let (w, h) = img.dimensions();
    let half = (blocksize / 2) as i32;

    // Build integral image for fast mean computation
    let mut integral = vec![0u64; (w as usize + 1) * (h as usize + 1)];
    let iw = w as usize + 1;
    for y in 0..h as usize {
        let mut row_sum = 0u64;
        for x in 0..w as usize {
            row_sum += img.get_pixel(x as u32, y as u32).0[0] as u64;
            integral[(y + 1) * iw + (x + 1)] = row_sum + integral[y * iw + (x + 1)];
        }
    }

    let mut result = GrayImage::new(w, h);
    for y in 0..h as i32 {
        for x in 0..w as i32 {
            // Compute local mean from integral image
            let x0 = (x - half).max(0) as usize;
            let y0 = (y - half).max(0) as usize;
            let x1 = ((x + half + 1) as usize).min(w as usize);
            let y1 = ((y + half + 1) as usize).min(h as usize);
            let area = ((x1 - x0) * (y1 - y0)) as f64;

            let sum = integral[y1 * iw + x1] as f64
                - integral[y0 * iw + x1] as f64
                - integral[y1 * iw + x0] as f64
                + integral[y0 * iw + x0] as f64;
            let mean = sum / area;

            let px = if invert {
                255 - img.get_pixel(x as u32, y as u32).0[0]
            } else {
                img.get_pixel(x as u32, y as u32).0[0]
            };

            let val = if (px as f64) > mean + constant {
                255u8
            } else {
                0u8
            };
            result.put_pixel(x as u32, y as u32, Luma([val]));
        }
    }

    result
}

/// 1D sliding min using a monotonic deque. O(n) regardless of kernel size.
/// For each position i, computes min over data[max(0,i-radius)..=min(n-1,i+radius)].
fn sliding_min(data: &[u8], radius: usize) -> Vec<u8> {
    let n = data.len();
    let mut out = vec![255u8; n];
    let mut deque: std::collections::VecDeque<usize> = std::collections::VecDeque::new();

    for i in 0..n {
        // Remove indices outside the left edge of window for output position (i - radius)
        // But we process ahead: push i, then compute output for (i - radius) when ready
        // Instead, use direct centered approach:
        // For output position p, window is [p.saturating_sub(radius)..=min(p+radius, n-1)]
        // We'll iterate and maintain deque for direct output at position i
        while !deque.is_empty() && *deque.front().unwrap() < i.saturating_sub(radius) {
            deque.pop_front();
        }
        while !deque.is_empty() && data[*deque.back().unwrap()] >= data[i] {
            deque.pop_back();
        }
        deque.push_back(i);
        // We can compute out[j] once we've seen all elements in j's window
        // For position j, the rightmost element is min(j+radius, n-1)
        // We can write out[j] when i >= min(j+radius, n-1), i.e., j <= i - radius (if i >= radius)
        // But we also need to handle the startup
    }

    // Simpler approach: two passes — compute prefix min from left and right
    // then combine. This is the van Herk/Gil-Werman approach.
    let kernel = 2 * radius + 1;
    if kernel >= n {
        // Kernel covers entire array — just fill with global min
        let global_min = data.iter().copied().min().unwrap_or(255);
        return vec![global_min; n];
    }

    // Block-based approach
    let mut prefix_min = vec![255u8; n];
    let mut suffix_min = vec![255u8; n];

    for block_start in (0..n).step_by(kernel) {
        // Forward pass (prefix min within block)
        let block_end = (block_start + kernel).min(n);
        let mut running = 255u8;
        for i in block_start..block_end {
            running = running.min(data[i]);
            prefix_min[i] = running;
        }
        // Backward pass (suffix min within block)
        running = 255;
        for i in (block_start..block_end).rev() {
            running = running.min(data[i]);
            suffix_min[i] = running;
        }
    }

    for i in 0..n {
        let left = i.saturating_sub(radius);
        let right = (i + radius).min(n - 1);
        out[i] = suffix_min[left].min(prefix_min[right]);
    }

    out
}

/// 1D sliding max using van Herk/Gil-Werman block decomposition. O(n).
fn sliding_max(data: &[u8], radius: usize) -> Vec<u8> {
    let n = data.len();
    let kernel = 2 * radius + 1;

    if kernel >= n {
        let global_max = data.iter().copied().max().unwrap_or(0);
        return vec![global_max; n];
    }

    let mut prefix_max = vec![0u8; n];
    let mut suffix_max = vec![0u8; n];

    for block_start in (0..n).step_by(kernel) {
        let block_end = (block_start + kernel).min(n);
        let mut running = 0u8;
        for i in block_start..block_end {
            running = running.max(data[i]);
            prefix_max[i] = running;
        }
        running = 0;
        for i in (block_start..block_end).rev() {
            running = running.max(data[i]);
            suffix_max[i] = running;
        }
    }

    let mut out = vec![0u8; n];
    for i in 0..n {
        let left = i.saturating_sub(radius);
        let right = (i + radius).min(n - 1);
        out[i] = suffix_max[left].max(prefix_max[right]);
    }
    out
}

/// Morphological erosion with a 1D kernel (horizontal or vertical).
///
/// kernel_w and kernel_h: one must be 1, the other is the kernel length.
/// Uses O(n) sliding min algorithm.
fn erode(img: &GrayImage, kernel_w: u32, kernel_h: u32) -> GrayImage {
    let (w, h) = img.dimensions();
    let mut result = GrayImage::new(w, h);

    if kernel_w > 1 && kernel_h <= 1 {
        // Horizontal erosion: slide along each row
        let radius = kernel_w as usize / 2;
        for y in 0..h {
            let row: Vec<u8> = (0..w).map(|x| img.get_pixel(x, y).0[0]).collect();
            let out = sliding_min(&row, radius);
            for (x, &val) in out.iter().enumerate() {
                result.put_pixel(x as u32, y, Luma([val]));
            }
        }
    } else if kernel_h > 1 && kernel_w <= 1 {
        // Vertical erosion: slide along each column
        let radius = kernel_h as usize / 2;
        for x in 0..w {
            let col: Vec<u8> = (0..h).map(|y| img.get_pixel(x, y).0[0]).collect();
            let out = sliding_min(&col, radius);
            for (y, &val) in out.iter().enumerate() {
                result.put_pixel(x, y as u32, Luma([val]));
            }
        }
    } else {
        // Identity or 1x1 kernel
        return img.clone();
    }

    result
}

/// Morphological dilation with a 1D kernel (horizontal or vertical).
///
/// Uses O(n) sliding max algorithm.
fn dilate(img: &GrayImage, kernel_w: u32, kernel_h: u32) -> GrayImage {
    let (w, h) = img.dimensions();
    let mut result = GrayImage::new(w, h);

    if kernel_w > 1 && kernel_h <= 1 {
        let radius = kernel_w as usize / 2;
        for y in 0..h {
            let row: Vec<u8> = (0..w).map(|x| img.get_pixel(x, y).0[0]).collect();
            let out = sliding_max(&row, radius);
            for (x, &val) in out.iter().enumerate() {
                result.put_pixel(x as u32, y, Luma([val]));
            }
        }
    } else if kernel_h > 1 && kernel_w <= 1 {
        let radius = kernel_h as usize / 2;
        for x in 0..w {
            let col: Vec<u8> = (0..h).map(|y| img.get_pixel(x, y).0[0]).collect();
            let out = sliding_max(&col, radius);
            for (y, &val) in out.iter().enumerate() {
                result.put_pixel(x, y as u32, Luma([val]));
            }
        }
    } else {
        return img.clone();
    }

    result
}

/// Morphological open (erode then dilate) to isolate lines in one direction.
fn morph_open(img: &GrayImage, kernel_w: u32, kernel_h: u32) -> GrayImage {
    let eroded = erode(img, kernel_w, kernel_h);
    dilate(&eroded, kernel_w, kernel_h)
}

/// Extract line segments from a binary mask.
///
/// Finds connected runs of white (255) pixels in the specified direction.
/// Returns segments in image pixel coordinates.
fn extract_segments(mask: &GrayImage, direction: Direction) -> Vec<Segment> {
    let (w, h) = mask.dimensions();
    let mut segments = Vec::new();

    match direction {
        Direction::Horizontal => {
            for y in 0..h {
                let mut in_run = false;
                let mut run_start = 0u32;
                for x in 0..w {
                    let px = mask.get_pixel(x, y).0[0];
                    if px > 128 && !in_run {
                        in_run = true;
                        run_start = x;
                    } else if (px <= 128 || x == w - 1) && in_run {
                        let run_end = if px > 128 { x } else { x.saturating_sub(1) };
                        let length = run_end - run_start;
                        if length > 5 {
                            // Min length filter: skip tiny noise
                            segments.push(Segment {
                                x0: run_start as f64,
                                y0: y as f64,
                                x1: run_end as f64,
                                y1: y as f64,
                            });
                        }
                        in_run = false;
                    }
                }
            }
        }
        Direction::Vertical => {
            for x in 0..w {
                let mut in_run = false;
                let mut run_start = 0u32;
                for y in 0..h {
                    let px = mask.get_pixel(x, y).0[0];
                    if px > 128 && !in_run {
                        in_run = true;
                        run_start = y;
                    } else if (px <= 128 || y == h - 1) && in_run {
                        let run_end = if px > 128 { y } else { y.saturating_sub(1) };
                        let length = run_end - run_start;
                        if length > 5 {
                            segments.push(Segment {
                                x0: x as f64,
                                y0: run_start as f64,
                                x1: x as f64,
                                y1: run_end as f64,
                            });
                        }
                        in_run = false;
                    }
                }
            }
        }
    }

    // Merge segments that are adjacent (within 3px) in the same line
    merge_segments(&mut segments, direction);
    segments
}

#[derive(Debug, Clone, Copy, PartialEq)]
enum Direction {
    Horizontal,
    Vertical,
}

/// Merge segments that are close together on the same axis.
fn merge_segments(segments: &mut Vec<Segment>, direction: Direction) {
    if segments.len() < 2 {
        return;
    }

    match direction {
        Direction::Horizontal => {
            // Sort by y, then by x0
            segments.sort_by(|a, b| {
                a.y0.partial_cmp(&b.y0).unwrap().then(a.x0.partial_cmp(&b.x0).unwrap())
            });
            let mut i = 0;
            while i + 1 < segments.len() {
                if (segments[i].y0 - segments[i + 1].y0).abs() < 3.0
                    && (segments[i + 1].x0 - segments[i].x1) < 5.0
                {
                    segments[i].x1 = segments[i].x1.max(segments[i + 1].x1);
                    segments.remove(i + 1);
                } else {
                    i += 1;
                }
            }
        }
        Direction::Vertical => {
            segments.sort_by(|a, b| {
                a.x0.partial_cmp(&b.x0).unwrap().then(a.y0.partial_cmp(&b.y0).unwrap())
            });
            let mut i = 0;
            while i + 1 < segments.len() {
                if (segments[i].x0 - segments[i + 1].x0).abs() < 3.0
                    && (segments[i + 1].y0 - segments[i].y1) < 5.0
                {
                    segments[i].y1 = segments[i].y1.max(segments[i + 1].y1);
                    segments.remove(i + 1);
                } else {
                    i += 1;
                }
            }
        }
    }
}

/// Detect joints (intersections) between horizontal and vertical line masks.
///
/// A joint is where both masks have white pixels at the same location.
/// Returns joint centers as (x, y) in image coordinates.
fn detect_joints(h_mask: &GrayImage, v_mask: &GrayImage) -> Vec<(f64, f64)> {
    let (w, h) = h_mask.dimensions();
    let mut joint_mask = GrayImage::new(w, h);

    // AND the two masks
    for y in 0..h {
        for x in 0..w {
            let hp = h_mask.get_pixel(x, y).0[0];
            let vp = v_mask.get_pixel(x, y).0[0];
            let val = if hp > 128 && vp > 128 { 255 } else { 0 };
            joint_mask.put_pixel(x, y, Luma([val]));
        }
    }

    // Find connected components in joint mask (simple flood fill)
    find_blob_centers(&joint_mask)
}

/// Find centers of connected white blobs in a binary image.
///
/// Simple flood-fill connected component analysis.
fn find_blob_centers(img: &GrayImage) -> Vec<(f64, f64)> {
    let (w, h) = img.dimensions();
    let mut visited = vec![false; w as usize * h as usize];
    let mut centers = Vec::new();

    for y in 0..h {
        for x in 0..w {
            let idx = y as usize * w as usize + x as usize;
            if visited[idx] || img.get_pixel(x, y).0[0] <= 128 {
                continue;
            }

            // Flood fill to find this blob
            let mut stack = vec![(x, y)];
            let mut sum_x: f64 = 0.0;
            let mut sum_y: f64 = 0.0;
            let mut count: f64 = 0.0;

            while let Some((cx, cy)) = stack.pop() {
                let ci = cy as usize * w as usize + cx as usize;
                if visited[ci] {
                    continue;
                }
                visited[ci] = true;

                if img.get_pixel(cx, cy).0[0] > 128 {
                    sum_x += cx as f64;
                    sum_y += cy as f64;
                    count += 1.0;

                    // Add neighbors (4-connected)
                    if cx > 0 {
                        stack.push((cx - 1, cy));
                    }
                    if cx + 1 < w {
                        stack.push((cx + 1, cy));
                    }
                    if cy > 0 {
                        stack.push((cx, cy - 1));
                    }
                    if cy + 1 < h {
                        stack.push((cx, cy + 1));
                    }
                }
            }

            if count > 0.0 {
                centers.push((sum_x / count, sum_y / count));
            }
        }
    }

    centers
}

/// Find table regions from contours of the combined h+v line mask.
///
/// Returns bounding boxes of regions with enough joints (>4) to be tables.
fn find_table_regions(
    h_mask: &GrayImage,
    v_mask: &GrayImage,
    min_joints: usize,
) -> Vec<(BBox, Vec<(f64, f64)>)> {
    let (w, h) = h_mask.dimensions();
    let mut combined = GrayImage::new(w, h);
    for y in 0..h {
        for x in 0..w {
            let hp = h_mask.get_pixel(x, y).0[0];
            let vp = v_mask.get_pixel(x, y).0[0];
            let val = if hp > 128 || vp > 128 { 255 } else { 0 };
            combined.put_pixel(x, y, Luma([val]));
        }
    }

    // Find connected regions in the combined mask
    let blobs = find_blobs_with_bounds(&combined);

    // For each region, find joints within it
    let all_joints = detect_joints(h_mask, v_mask);
    let mut table_regions = Vec::new();

    for bbox in &blobs {
        let region_joints: Vec<(f64, f64)> = all_joints
            .iter()
            .filter(|&&(jx, jy)| {
                jx >= bbox.x0 && jx <= bbox.x1 && jy >= bbox.y0 && jy <= bbox.y1
            })
            .cloned()
            .collect();

        if region_joints.len() > min_joints {
            table_regions.push((*bbox, region_joints));
        }
    }

    table_regions
}

/// Find connected blobs and return their bounding boxes.
fn find_blobs_with_bounds(img: &GrayImage) -> Vec<BBox> {
    let (w, h) = img.dimensions();
    let mut visited = vec![false; w as usize * h as usize];
    let mut blobs = Vec::new();

    for y in 0..h {
        for x in 0..w {
            let idx = y as usize * w as usize + x as usize;
            if visited[idx] || img.get_pixel(x, y).0[0] <= 128 {
                continue;
            }

            let mut stack = vec![(x, y)];
            let mut min_x = x as f64;
            let mut min_y = y as f64;
            let mut max_x = x as f64;
            let mut max_y = y as f64;
            let mut pixel_count = 0usize;

            while let Some((cx, cy)) = stack.pop() {
                let ci = cy as usize * w as usize + cx as usize;
                if visited[ci] {
                    continue;
                }
                visited[ci] = true;

                if img.get_pixel(cx, cy).0[0] > 128 {
                    min_x = min_x.min(cx as f64);
                    min_y = min_y.min(cy as f64);
                    max_x = max_x.max(cx as f64);
                    max_y = max_y.max(cy as f64);
                    pixel_count += 1;

                    if cx > 0 { stack.push((cx - 1, cy)); }
                    if cx + 1 < w { stack.push((cx + 1, cy)); }
                    if cy > 0 { stack.push((cx, cy - 1)); }
                    if cy + 1 < h { stack.push((cx, cy + 1)); }
                }
            }

            // Only keep blobs with reasonable size
            if pixel_count > 20 {
                blobs.push(BBox::new(min_x, min_y, max_x, max_y));
            }
        }
    }

    blobs
}

/// Build column and row boundaries from joint positions.
///
/// Joints are merged within `line_tol` to create discrete grid lines.
fn build_grid_from_joints(
    joints: &[(f64, f64)],
    line_tol: f64,
) -> (Vec<f64>, Vec<f64>) {
    if joints.is_empty() {
        return (Vec::new(), Vec::new());
    }

    // Collect unique x-coordinates (columns)
    let mut xs: Vec<f64> = joints.iter().map(|j| j.0).collect();
    xs.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let cols = merge_close_values(&xs, line_tol);

    // Collect unique y-coordinates (rows)
    let mut ys: Vec<f64> = joints.iter().map(|j| j.1).collect();
    ys.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let rows = merge_close_values(&ys, line_tol);

    (cols, rows)
}

/// Merge values that are within tolerance into their average.
fn merge_close_values(sorted: &[f64], tol: f64) -> Vec<f64> {
    if sorted.is_empty() {
        return Vec::new();
    }

    let mut result = Vec::new();
    let mut group_sum = sorted[0];
    let mut group_count = 1usize;

    for &val in &sorted[1..] {
        if val - (group_sum / group_count as f64) <= tol {
            group_sum += val;
            group_count += 1;
        } else {
            result.push(group_sum / group_count as f64);
            group_sum = val;
            group_count = 1;
        }
    }
    result.push(group_sum / group_count as f64);

    result
}

/// Set cell edge flags based on detected line segments.
///
/// For each vertical segment, find which column boundary it aligns with
/// and mark adjacent cells' left/right flags. Similarly for horizontal.
fn set_edges_from_segments(table: &mut Table, h_segs: &[Segment], v_segs: &[Segment], tol: f64) {
    // Vertical segments → left/right edges
    for seg in v_segs {
        let x = (seg.x0 + seg.x1) / 2.0;
        let y_top = seg.y0.min(seg.y1);
        let y_bot = seg.y0.max(seg.y1);

        // Find which column boundary this line is on
        for (ci, &(cx0, cx1)) in table.cols.iter().enumerate() {
            if (x - cx0).abs() <= tol {
                // Left edge of this column
                for (ri, &(ry0, ry1)) in table.rows.iter().enumerate() {
                    if y_top <= ry1 + tol && y_bot >= ry0 - tol {
                        table.cells[ri][ci].left = true;
                    }
                }
            }
            if (x - cx1).abs() <= tol {
                // Right edge of this column
                for (ri, &(ry0, ry1)) in table.rows.iter().enumerate() {
                    if y_top <= ry1 + tol && y_bot >= ry0 - tol {
                        table.cells[ri][ci].right = true;
                    }
                }
            }
        }
    }

    // Horizontal segments → top/bottom edges
    for seg in h_segs {
        let y = (seg.y0 + seg.y1) / 2.0;
        let x_left = seg.x0.min(seg.x1);
        let x_right = seg.x0.max(seg.x1);

        for (ri, &(ry0, ry1)) in table.rows.iter().enumerate() {
            if (y - ry0).abs() <= tol {
                // Top edge of this row
                for (ci, &(cx0, cx1)) in table.cols.iter().enumerate() {
                    if x_left <= cx1 + tol && x_right >= cx0 - tol {
                        table.cells[ri][ci].top = true;
                    }
                }
            }
            if (y - ry1).abs() <= tol {
                // Bottom edge of this row
                for (ci, &(cx0, cx1)) in table.cols.iter().enumerate() {
                    if x_left <= cx1 + tol && x_right >= cx0 - tol {
                        table.cells[ri][ci].bottom = true;
                    }
                }
            }
        }
    }
}

/// Cluster joints by spatial proximity (for image-based extraction).
///
/// Joints within `gap` distance are grouped together.
fn cluster_joints_by_distance(joints: &[(f64, f64)], gap: f64) -> Vec<Vec<(f64, f64)>> {
    let n = joints.len();
    if n == 0 {
        return Vec::new();
    }

    let mut parent: Vec<usize> = (0..n).collect();

    fn find(parent: &mut [usize], mut i: usize) -> usize {
        while parent[i] != i {
            parent[i] = parent[parent[i]];
            i = parent[i];
        }
        i
    }

    fn union(parent: &mut [usize], a: usize, b: usize) {
        let ra = find(parent, a);
        let rb = find(parent, b);
        if ra != rb {
            parent[ra] = rb;
        }
    }

    let gap_sq = gap * gap;
    for i in 0..n {
        for j in (i + 1)..n {
            let dx = joints[i].0 - joints[j].0;
            let dy = joints[i].1 - joints[j].1;
            if dx * dx + dy * dy <= gap_sq {
                union(&mut parent, i, j);
            }
        }
    }

    let mut groups: std::collections::HashMap<usize, Vec<(f64, f64)>> =
        std::collections::HashMap::new();
    for i in 0..n {
        let root = find(&mut parent, i);
        groups.entry(root).or_default().push(joints[i]);
    }

    groups.into_values().collect()
}

/// Cluster joints into groups by segment connectivity using union-find.
///
/// Two joints belong to the same table if they were produced by the same
/// horizontal or vertical segment. This is more precise than distance-based
/// clustering and correctly separates side-by-side tables that share the
/// same row y-coordinates but have no connecting segments.
fn cluster_joints(
    joints: &[((f64, f64), usize, usize)], // (point, h_segment_idx, v_segment_idx)
) -> Vec<Vec<(f64, f64)>> {
    let n = joints.len();
    if n == 0 {
        return Vec::new();
    }

    // Union-find parent array
    let mut parent: Vec<usize> = (0..n).collect();

    fn find(parent: &mut [usize], mut i: usize) -> usize {
        while parent[i] != i {
            parent[i] = parent[parent[i]]; // path compression
            i = parent[i];
        }
        i
    }

    fn union(parent: &mut [usize], a: usize, b: usize) {
        let ra = find(parent, a);
        let rb = find(parent, b);
        if ra != rb {
            parent[ra] = rb;
        }
    }

    // Union joints that share the same h-segment or v-segment.
    // Build index: segment_id -> first joint seen with that segment.
    let mut h_first: std::collections::HashMap<usize, usize> = std::collections::HashMap::new();
    let mut v_first: std::collections::HashMap<usize, usize> = std::collections::HashMap::new();

    for i in 0..n {
        let h_idx = joints[i].1;
        let v_idx = joints[i].2;

        if let Some(&prev) = h_first.get(&h_idx) {
            union(&mut parent, prev, i);
        } else {
            h_first.insert(h_idx, i);
        }

        if let Some(&prev) = v_first.get(&v_idx) {
            union(&mut parent, prev, i);
        } else {
            v_first.insert(v_idx, i);
        }
    }

    // Group by root
    let mut groups: std::collections::HashMap<usize, Vec<(f64, f64)>> =
        std::collections::HashMap::new();
    for i in 0..n {
        let root = find(&mut parent, i);
        groups.entry(root).or_default().push(joints[i].0);
    }

    groups.into_values().collect()
}

/// Convert image pixel coordinates to PDF page coordinates.
///
/// Image origin: top-left. PDF origin: also top-left in our system.
/// Just scale by the ratio of page dimensions to image dimensions.
fn image_to_page(val: f64, img_dim: f64, page_dim: f64) -> f64 {
    val * page_dim / img_dim
}

/// Full lattice extraction pipeline on a pre-rendered grayscale image.
///
/// Takes a grayscale image (from pdf_oxide's renderer) and text elements,
/// returns extracted tables.
pub fn extract_lattice(
    gray_image: &GrayImage,
    elements: &[TextElement],
    page_width: f64,
    page_height: f64,
    config: &ExtractConfig,
) -> Vec<Table> {
    let (img_w, img_h) = gray_image.dimensions();
    if img_w < 10 || img_h < 10 {
        return Vec::new();
    }

    let scale_x = page_width / img_w as f64;
    let scale_y = page_height / img_h as f64;

    // Step 1: Adaptive threshold (invert foreground)
    let threshold = adaptive_threshold(gray_image, config.threshold_blocksize, config.threshold_constant, true);

    // Step 2: Morphological operations to isolate lines
    let h_kernel_size = (img_w / config.line_scale).max(3);
    let v_kernel_size = (img_h / config.line_scale).max(3);

    let h_mask = morph_open(&threshold, h_kernel_size, 1);
    let v_mask = morph_open(&threshold, 1, v_kernel_size);

    // Additional dilation if requested
    let h_mask = if config.iterations > 0 {
        let mut m = h_mask;
        for _ in 0..config.iterations {
            m = dilate(&m, h_kernel_size, 1);
        }
        m
    } else {
        h_mask
    };

    let v_mask = if config.iterations > 0 {
        let mut m = v_mask;
        for _ in 0..config.iterations {
            m = dilate(&m, 1, v_kernel_size);
        }
        m
    } else {
        v_mask
    };

    // Step 3: Find all joints (intersections of h and v lines)
    let all_joints = detect_joints(&h_mask, &v_mask);

    if all_joints.len() < 4 {
        return Vec::new();
    }

    // Step 4: Cluster joints into table groups by spatial proximity
    let table_joint_groups = cluster_joints_by_distance(&all_joints, img_w as f64 * 0.3);

    let mut tables = Vec::new();

    for joints in &table_joint_groups {
        if joints.len() < 4 {
            continue;
        }

        // Build grid from joints
        let (col_xs, row_ys) = build_grid_from_joints(joints, config.line_tol);

        if col_xs.len() < 2 || row_ys.len() < 2 {
            continue;
        }

        // Convert image coordinates to page coordinates
        let page_cols: Vec<(f64, f64)> = col_xs
            .windows(2)
            .map(|w| {
                (
                    image_to_page(w[0], img_w as f64, page_width),
                    image_to_page(w[1], img_w as f64, page_width),
                )
            })
            .collect();

        let page_rows: Vec<(f64, f64)> = row_ys
            .windows(2)
            .map(|w| {
                (
                    image_to_page(w[0], img_h as f64, page_height),
                    image_to_page(w[1], img_h as f64, page_height),
                )
            })
            .collect();

        if page_cols.is_empty() || page_rows.is_empty() {
            continue;
        }

        let mut table = Table::new(page_cols, page_rows, Flavor::Lattice);

        // Extract line segments for edge detection
        let h_segs: Vec<Segment> = extract_segments(&h_mask, Direction::Horizontal)
            .into_iter()
            .map(|s| Segment {
                x0: s.x0 * scale_x,
                y0: s.y0 * scale_y,
                x1: s.x1 * scale_x,
                y1: s.y1 * scale_y,
            })
            .collect();

        let v_segs: Vec<Segment> = extract_segments(&v_mask, Direction::Vertical)
            .into_iter()
            .map(|s| Segment {
                x0: s.x0 * scale_x,
                y0: s.y0 * scale_y,
                x1: s.x1 * scale_x,
                y1: s.y1 * scale_y,
            })
            .collect();

        // Set edge flags from detected lines
        set_edges_from_segments(&mut table, &h_segs, &v_segs, config.joint_tol * scale_x);

        // Assign text to cells
        let errors = assign_text_to_cells(&mut table, elements);
        table.accuracy = compute_accuracy(&errors);
        table.compute_whitespace();

        tables.push(table);
    }

    tables
}

/// Extract tables from pre-extracted vector path segments (no rendering needed).
///
/// This is faster and more accurate than image-based detection because it uses
/// the actual PDF drawing operations rather than a rasterized approximation.
/// The segments should be in page coordinates (top-left origin, PDF points).
pub fn extract_lattice_from_paths(
    h_segments: &[Segment],
    v_segments: &[Segment],
    elements: &[TextElement],
    page_width: f64,
    page_height: f64,
    config: &ExtractConfig,
) -> Vec<Table> {
    if h_segments.len() < 2 || v_segments.len() < 2 {
        return Vec::new();
    }

    // Find intersections (joints) between horizontal and vertical segments.
    // A joint exists where an h-segment and v-segment cross within tolerance.
    // Each joint tracks which h-segment and v-segment produced it, for
    // connectivity-based clustering.
    let line_tol = config.line_tol;
    let mut joints: Vec<((f64, f64), usize, usize)> = Vec::new();

    for (hi, h) in h_segments.iter().enumerate() {
        let hy = (h.y0 + h.y1) / 2.0;
        let hx_min = h.x0.min(h.x1);
        let hx_max = h.x0.max(h.x1);

        for (vi, v) in v_segments.iter().enumerate() {
            let vx = (v.x0 + v.x1) / 2.0;
            let vy_min = v.y0.min(v.y1);
            let vy_max = v.y0.max(v.y1);

            // Check if they cross: v's x within h's range, h's y within v's range
            if vx >= hx_min - line_tol && vx <= hx_max + line_tol
                && hy >= vy_min - line_tol && hy <= vy_max + line_tol
            {
                joints.push(((vx, hy), hi, vi));
            }
        }
    }

    if joints.len() < 4 {
        return Vec::new();
    }

    // Cluster joints into table groups by segment connectivity
    let table_groups = cluster_joints(&joints);

    let mut tables = Vec::new();

    for group in &table_groups {
        if group.len() < 4 {
            continue;
        }

        // Build grid: collect unique x and y values from joints
        let (col_xs, row_ys) = build_grid_from_joints(group, line_tol);
        if col_xs.len() < 2 || row_ys.len() < 2 {
            continue;
        }

        // Build column and row pairs
        let page_cols: Vec<(f64, f64)> = col_xs.windows(2).map(|w| (w[0], w[1])).collect();
        let page_rows: Vec<(f64, f64)> = row_ys.windows(2).map(|w| (w[0], w[1])).collect();

        if page_cols.is_empty() || page_rows.is_empty() {
            continue;
        }

        let mut table = Table::new(page_cols, page_rows, Flavor::Lattice);

        // Set edge flags from the vector path segments directly
        set_edges_from_segments(&mut table, h_segments, v_segments, line_tol);

        // Assign text to cells
        let errors = assign_text_to_cells(&mut table, elements);
        table.accuracy = compute_accuracy(&errors);
        table.compute_whitespace();

        tables.push(table);
    }

    tables
}

/// Convert PathContent elements into horizontal and vertical Segments.
///
/// Examines each path's operations and bounding box to identify
/// axis-aligned line segments suitable for lattice table detection.
/// Coordinates are in PDF points, top-left origin.
pub fn paths_to_segments(
    paths: &[crate::elements::PathContent],
    page_height: f64,
    min_length: f64,
) -> (Vec<Segment>, Vec<Segment>) {
    use crate::elements::PathOperation;

    let mut h_segments = Vec::new();
    let mut v_segments = Vec::new();

    for path in paths {
        // Consider stroked paths AND filled paths (some PDFs draw table lines
        // as thin filled rectangles rather than stroked lines)
        if path.stroke_color.is_none() && path.fill_color.is_none() {
            continue;
        }

        // Skip white or near-white lines — these are invisible on white backgrounds
        // and are decorative/background lines, not table borders
        let is_white = |color: &Option<crate::layout::Color>| -> bool {
            match color {
                Some(c) => c.r >= 0.95 && c.g >= 0.95 && c.b >= 0.95,
                None => false,
            }
        };
        if is_white(&path.stroke_color) && path.fill_color.is_none() {
            continue;
        }
        if is_white(&path.fill_color) && path.stroke_color.is_none() {
            continue;
        }

        let bbox = &path.bbox;
        let bw = bbox.width as f64;
        let bh = bbox.height as f64;

        // Simple case: thin bbox indicates a single line
        if bh < 2.0 && bw >= min_length {
            // Horizontal line — convert from bottom-left to top-left origin
            let y_tl = page_height - bbox.y as f64;
            h_segments.push(Segment {
                x0: bbox.x as f64,
                y0: y_tl,
                x1: bbox.x as f64 + bw,
                y1: y_tl,
            });
            continue;
        }
        if bw < 2.0 && bh >= min_length {
            // Vertical line — convert from bottom-left to top-left origin
            let y_top = page_height - bbox.y as f64 - bh;
            let y_bot = page_height - bbox.y as f64;
            v_segments.push(Segment {
                x0: bbox.x as f64,
                y0: y_top,
                x1: bbox.x as f64,
                y1: y_bot,
            });
            continue;
        }

        // For stroked rectangles, extract edges as grid lines.
        // Fill-only paths with large bbox are cell backgrounds, not table lines.
        // Thin fill-only paths are already handled above via thin-bbox + `continue`.
        let is_stroked = path.stroke_color.is_some();
        if is_stroked {
            for op in &path.operations {
                if let PathOperation::Rectangle(rx, ry, rw, rh) = op {
                    let rx = *rx as f64;
                    let ry = *ry as f64;
                    let rw = *rw as f64;
                    let rh = *rh as f64;

                    // Convert to top-left origin
                    let y_top = page_height - ry - rh;
                    let y_bot = page_height - ry;

                    // Thin rectangle = line
                    if rh.abs() < 2.0 && rw.abs() >= min_length {
                        let y_mid = (y_top + y_bot) / 2.0;
                        h_segments.push(Segment {
                            x0: rx,
                            y0: y_mid,
                            x1: rx + rw,
                            y1: y_mid,
                        });
                    } else if rw.abs() < 2.0 && rh.abs() >= min_length {
                        let x_mid = rx + rw / 2.0;
                        v_segments.push(Segment {
                            x0: x_mid,
                            y0: y_top,
                            x1: x_mid,
                            y1: y_bot,
                        });
                    } else if rw.abs() >= min_length && rh.abs() >= min_length {
                        // Full rectangle: extract all 4 edges
                        h_segments.push(Segment { x0: rx, y0: y_top, x1: rx + rw, y1: y_top });
                        h_segments.push(Segment { x0: rx, y0: y_bot, x1: rx + rw, y1: y_bot });
                        v_segments.push(Segment { x0: rx, y0: y_top, x1: rx, y1: y_bot });
                        v_segments.push(Segment { x0: rx + rw, y0: y_top, x1: rx + rw, y1: y_bot });
                    }
                }
            }
        }

        // For simple paths with MoveTo/LineTo, extract line segments.
        // Skip compound fill-only paths (large bbox area or Rectangle operations)
        // as their LineTo sequences trace cell-by-cell, duplicating the thin-bbox
        // segments at slightly shifted coordinates.
        //
        // Stroked paths may legitimately contain Rectangle ops for the outer border
        // AND LineTo ops for internal grid lines (e.g. column_span_2.pdf), so we
        // always process LineTo for stroked paths.
        let is_compound = if is_stroked {
            false
        } else {
            let has_rect_ops = path.operations.iter().any(|op| {
                matches!(op, PathOperation::Rectangle(..))
            });
            has_rect_ops || (bw > 5.0 && bh > 5.0)
        };
        if !is_compound {
            let mut current = None;
            for op in &path.operations {
                match op {
                    PathOperation::MoveTo(x, y) => {
                        current = Some((*x as f64, *y as f64));
                    }
                    PathOperation::LineTo(x, y) => {
                        if let Some((cx, cy)) = current {
                            let x1 = *x as f64;
                            let y1 = *y as f64;
                            let dx = (x1 - cx).abs();
                            let dy = (y1 - cy).abs();

                            // Convert to top-left origin
                            let cy_tl = page_height - cy;
                            let y1_tl = page_height - y1;

                            if dy < 2.0 && dx >= min_length {
                                h_segments.push(Segment {
                                    x0: cx.min(x1),
                                    y0: (cy_tl + y1_tl) / 2.0,
                                    x1: cx.max(x1),
                                    y1: (cy_tl + y1_tl) / 2.0,
                                });
                            } else if dx < 2.0 && dy >= min_length {
                                v_segments.push(Segment {
                                    x0: (cx + x1) / 2.0,
                                    y0: cy_tl.min(y1_tl),
                                    x1: (cx + x1) / 2.0,
                                    y1: cy_tl.max(y1_tl),
                                });
                            }
                            current = Some((x1, y1));
                        }
                    }
                    _ => {}
                }
            }
        }
    }

    // Deduplicate segments: merge segments at the same position (within tolerance).
    // This prevents double-counting when a path's bbox AND its operations both
    // produce segments for the same line. Use 2pt tolerance to catch duplicates
    // at slightly different coordinates.
    let dedup_tol = 2.0;
    let h_segments = dedup_segments(h_segments, true, dedup_tol);
    let v_segments = dedup_segments(v_segments, false, dedup_tol);

    (h_segments, v_segments)
}

/// Deduplicate segments that represent the same line.
///
/// For horizontal segments, group by y-position and merge overlapping x-ranges.
/// For vertical segments, group by x-position and merge overlapping y-ranges.
fn dedup_segments(mut segments: Vec<Segment>, horizontal: bool, tol: f64) -> Vec<Segment> {
    if segments.is_empty() {
        return segments;
    }

    // Sort by the varying coordinate minimum so that overlap checks work.
    // Group segments at similar constant coordinate positions together.
    if horizontal {
        segments.sort_by(|a, b| {
            a.x0.min(a.x1).partial_cmp(&b.x0.min(b.x1)).unwrap()
        });
    } else {
        segments.sort_by(|a, b| {
            a.y0.min(a.y1).partial_cmp(&b.y0.min(b.y1)).unwrap()
        });
    }

    // Multi-pass: repeatedly merge overlapping segments at the same position
    // until no more merges occur. This handles out-of-order segments.
    let mut result = segments;
    loop {
        let mut merged = Vec::with_capacity(result.len());
        let mut changed = false;
        let mut used = vec![false; result.len()];

        for i in 0..result.len() {
            if used[i] {
                continue;
            }
            let mut current = result[i].clone();
            for j in (i + 1)..result.len() {
                if used[j] {
                    continue;
                }
                let same_line = if horizontal {
                    (result[j].y0 - current.y0).abs() < tol
                } else {
                    (result[j].x0 - current.x0).abs() < tol
                };
                if !same_line {
                    continue;
                }

                // Check true overlap or adjacency (bidirectional)
                let overlaps = if horizontal {
                    let cur_min = current.x0.min(current.x1);
                    let cur_max = current.x0.max(current.x1);
                    let seg_min = result[j].x0.min(result[j].x1);
                    let seg_max = result[j].x0.max(result[j].x1);
                    seg_min <= cur_max + tol && seg_max >= cur_min - tol
                } else {
                    let cur_min = current.y0.min(current.y1);
                    let cur_max = current.y0.max(current.y1);
                    let seg_min = result[j].y0.min(result[j].y1);
                    let seg_max = result[j].y0.max(result[j].y1);
                    seg_min <= cur_max + tol && seg_max >= cur_min - tol
                };

                if overlaps {
                    // Merge
                    if horizontal {
                        current.x0 = current.x0.min(result[j].x0);
                        current.x1 = current.x1.max(result[j].x1);
                    } else {
                        current.y0 = current.y0.min(result[j].y0);
                        current.y1 = current.y1.max(result[j].y1);
                    }
                    used[j] = true;
                    changed = true;
                }
            }
            merged.push(current);
        }

        result = merged;
        if !changed {
            break;
        }
    }

    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn adaptive_threshold_basic() {
        let mut img = GrayImage::new(20, 20);
        // Draw a white rectangle on black background
        for y in 5..15 {
            for x in 5..15 {
                img.put_pixel(x, y, Luma([200]));
            }
        }
        let result = adaptive_threshold(&img, 5, -2.0, false);
        // Center should be white after threshold
        assert!(result.get_pixel(10, 10).0[0] > 128);
    }

    #[test]
    fn merge_close_values_basic() {
        let vals = vec![10.0, 10.5, 11.0, 50.0, 50.5, 100.0];
        let merged = merge_close_values(&vals, 2.0);
        assert_eq!(merged.len(), 3); // Three groups
    }

    #[test]
    fn build_grid_from_joints_2x2() {
        // 4 joints forming a 2x2 grid
        let joints = vec![
            (10.0, 10.0),
            (100.0, 10.0),
            (10.0, 50.0),
            (100.0, 50.0),
        ];
        let (cols, rows) = build_grid_from_joints(&joints, 2.0);
        assert_eq!(cols.len(), 2, "expected 2 column lines");
        assert_eq!(rows.len(), 2, "expected 2 row lines");
    }

    #[test]
    fn extract_segments_horizontal() {
        let mut img = GrayImage::new(100, 20);
        // Draw a horizontal line at y=10
        for x in 10..90 {
            img.put_pixel(x, 10, Luma([255]));
        }
        let segs = extract_segments(&img, Direction::Horizontal);
        assert!(!segs.is_empty(), "should find horizontal segment");
        assert!(segs[0].is_horizontal(1.0));
    }

    #[test]
    fn find_blob_centers_single() {
        let mut img = GrayImage::new(50, 50);
        for y in 20..30 {
            for x in 20..30 {
                img.put_pixel(x, y, Luma([255]));
            }
        }
        let centers = find_blob_centers(&img);
        assert_eq!(centers.len(), 1);
        let (cx, cy) = centers[0];
        assert!((cx - 24.5).abs() < 1.0, "center x: {cx}");
        assert!((cy - 24.5).abs() < 1.0, "center y: {cy}");
    }

    /// End-to-end lattice extraction test against Camelot reference shapes.
    /// Tests vector-path-based extraction for PDFs with line-drawn borders.
    #[test]
    fn lattice_vs_camelot_parity() {
        let files_dir = "/home/graham/workspace/experiments/camelot/tests/files";
        // (file, expected_shape, tolerance): tolerance=0 means exact match required
        let cases = [
            ("column_span_1.pdf", 50, 8, 0),
            ("column_span_2.pdf", 11, 7, 0),
            ("row_span_2.pdf",     7, 10, 0),
            ("agstat.pdf",        33, 11, 0),
            ("row_span_1.pdf",    40,  4, 1),  // off by 1 row (missing compound boundary)
        ];
        let config = ExtractConfig::default();
        for (file, exp_rows, exp_cols, row_tol) in &cases {
            let path = format!("{}/{}", files_dir, file);
            let mut doc = crate::document::PdfDocument::open(&path)
                .unwrap_or_else(|_| panic!("open {}", file));
            let info = doc.get_page_info(0).expect("page info");
            let page_height = info.media_box.height as f64;
            let page_width = info.media_box.width as f64;
            let paths = doc.extract_paths(0).unwrap_or_default();
            let (h_segs, v_segs) = paths_to_segments(&paths, page_height, 5.0);

            let spans = doc.extract_spans(0).unwrap_or_default();
            let elements: Vec<TextElement> = spans.iter().map(|s| {
                TextElement {
                    text: s.text.clone(),
                    x0: s.bbox.x as f64,
                    y0: page_height - s.bbox.y as f64 - s.bbox.height as f64,
                    x1: s.bbox.x as f64 + s.bbox.width as f64,
                    y1: page_height - s.bbox.y as f64,
                    font_size: s.font_size as f64,
                }
            }).collect();

            let tables = extract_lattice_from_paths(
                &h_segs, &v_segs, &elements, page_width, page_height, &config,
            );
            assert!(!tables.is_empty(), "{}: no tables found", file);
            let t = &tables[0];
            let rows = t.rows.len();
            let cols = t.cols.len();
            assert_eq!(cols, *exp_cols, "{}: cols mismatch", file);
            assert!(
                (rows as i32 - *exp_rows as i32).unsigned_abs() <= *row_tol as u32,
                "{}: rows={} expected={} (tol={})", file, rows, exp_rows, row_tol
            );
        }
    }

    /// twotables_1.pdf: Two separate tables should be split into 2 groups.
    /// Camelot reference: table 1 = 3x10, table 2 = 5x9.
    #[test]
    fn twotables_split() {
        let files_dir = "/home/graham/workspace/experiments/camelot/tests/files";
        let path = format!("{}/twotables_1.pdf", files_dir);
        let mut doc = crate::document::PdfDocument::open(&path).expect("open");
        let info = doc.get_page_info(0).expect("page info");
        let page_height = info.media_box.height as f64;
        let page_width = info.media_box.width as f64;
        let paths = doc.extract_paths(0).unwrap_or_default();
        let (h_segs, v_segs) = paths_to_segments(&paths, page_height, 5.0);

        let spans = doc.extract_spans(0).unwrap_or_default();
        let elements: Vec<TextElement> = spans.iter().map(|s| {
            TextElement {
                text: s.text.clone(),
                x0: s.bbox.x as f64,
                y0: page_height - s.bbox.y as f64 - s.bbox.height as f64,
                x1: s.bbox.x as f64 + s.bbox.width as f64,
                y1: page_height - s.bbox.y as f64,
                font_size: s.font_size as f64,
            }
        }).collect();

        let config = ExtractConfig::default();
        let tables = extract_lattice_from_paths(
            &h_segs, &v_segs, &elements, page_width, page_height, &config,
        );
        assert_eq!(tables.len(), 2, "twotables_1 should produce 2 tables, got {}", tables.len());
    }

    /// White-only paths (background_lines_1) should produce 0 segments,
    /// correctly falling through to the rendering fallback.
    #[test]
    fn white_paths_filtered() {
        let files_dir = "/home/graham/workspace/experiments/camelot/tests/files";
        let path = format!("{}/background_lines_1.pdf", files_dir);
        let mut doc = crate::document::PdfDocument::open(&path).expect("open");
        let info = doc.get_page_info(0).expect("page info");
        let page_height = info.media_box.height as f64;
        let paths = doc.extract_paths(0).unwrap_or_default();
        let (h_segs, v_segs) = paths_to_segments(&paths, page_height, 5.0);
        assert_eq!(h_segs.len(), 0, "white h_segs should be filtered");
        assert_eq!(v_segs.len(), 0, "white v_segs should be filtered");
    }

    #[test]
    fn cluster_joints_basic() {
        // Two tables, each with 4 joints from their own segments.
        // Table 1: h-segments 0,1 x v-segments 0,1
        // Table 2: h-segments 2,3 x v-segments 2,3
        // Same y-coordinates but no shared segments -> 2 clusters.
        let joints = vec![
            ((10.0, 10.0), 0, 0), ((100.0, 10.0), 0, 1),
            ((10.0, 50.0), 1, 0), ((100.0, 50.0), 1, 1),
            ((500.0, 10.0), 2, 2), ((600.0, 10.0), 2, 3),
            ((500.0, 50.0), 3, 2), ((600.0, 50.0), 3, 3),
        ];
        let groups = cluster_joints(&joints);
        assert_eq!(groups.len(), 2, "expected 2 clusters, got {}", groups.len());
        for g in &groups {
            assert_eq!(g.len(), 4, "each cluster should have 4 joints");
        }
    }

    #[test]
    fn cluster_joints_shared_segment_merges() {
        // If two tables share a segment, they should be merged.
        // Joints 0-3 use h-segments 0,1, joints 4-7 use h-segments 1,2
        // Shared h-segment 1 -> single cluster.
        let joints = vec![
            ((10.0, 10.0), 0, 0), ((100.0, 10.0), 0, 1),
            ((10.0, 50.0), 1, 0), ((100.0, 50.0), 1, 1),
            ((200.0, 50.0), 1, 2), ((300.0, 50.0), 1, 3),
            ((200.0, 90.0), 2, 2), ((300.0, 90.0), 2, 3),
        ];
        let groups = cluster_joints(&joints);
        assert_eq!(groups.len(), 1, "shared segment should merge into 1 cluster");
    }

}
