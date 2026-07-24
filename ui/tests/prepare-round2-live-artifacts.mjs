import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { copyFile, mkdir, readFile, writeFile } from 'node:fs/promises'
import { basename, dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const UI_ROOT = resolve(fileURLToPath(new URL('..', import.meta.url)))
const REPO_ROOT = resolve(UI_ROOT, '..')
const DEFAULT_LIVE_ROOT = resolve(REPO_ROOT, 'artifacts/ux_competition/round2/live')
const SHA256_RE = /^[0-9a-f]{64}$/

function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex')
}

function jsonRecord(value, owner) {
  assert(value && typeof value === 'object' && !Array.isArray(value), `${owner} must be an object`)
  return value
}

function finiteBbox(value, owner) {
  assert(Array.isArray(value) && value.length === 4, `${owner} must have four values`)
  const bbox = value.map(Number)
  assert(bbox.every(Number.isFinite), `${owner} must be finite`)
  return bbox
}

function normalizedTopLeftXyxy(pdfBbox, width, height) {
  const [x, y, boxWidth, boxHeight] = finiteBbox(pdfBbox, 'engine bbox')
  assert(x >= 0 && y >= 0 && boxWidth > 0 && boxHeight > 0)
  assert(x + boxWidth <= width && y + boxHeight <= height)
  const top = height - y - boxHeight
  return [x / width, top / height, (x + boxWidth) / width, (top + boxHeight) / height]
}

function pageDimensions(sourcePdf, expectedPageCount) {
  const python = resolve(REPO_ROOT, '.venv/bin/python')
  const program = [
    'import json, sys',
    'from pdf_oxide import PdfDocument',
    'doc = PdfDocument(sys.argv[1])',
    'print(json.dumps([doc.page_dimensions(i) for i in range(doc.page_count())]))',
  ].join('; ')
  const output = execFileSync(python, ['-c', program, sourcePdf], {
    cwd: REPO_ROOT,
    encoding: 'utf8',
  })
  const dimensions = JSON.parse(output)
  assert.equal(dimensions.length, expectedPageCount, 'engine page-dimension count mismatch')
  return dimensions.map((entry, page) => {
    assert(Array.isArray(entry) && entry.length === 2, `page ${page} dimensions are invalid`)
    const [width, height] = entry.map(Number)
    assert(width > 0 && height > 0, `page ${page} dimensions must be positive`)
    return { page, width, height }
  })
}

export async function prepareRound2LiveArtifacts(liveRoot = DEFAULT_LIVE_ROOT) {
  const extractedPath = join(liveRoot, 'extracted.json')
  const annotationPath = join(liveRoot, 'annotation_call.json')
  const extracted = jsonRecord(JSON.parse(await readFile(extractedPath, 'utf8')), 'extracted.json')
  const annotationCall = jsonRecord(JSON.parse(await readFile(annotationPath, 'utf8')), 'annotation_call.json')
  const pdfSha256 = String(extracted.metadata?.pdf_sha256 ?? '')

  assert(SHA256_RE.test(pdfSha256), 'extracted metadata must contain pdf_sha256')
  assert.equal(annotationCall.pdf_sha256, pdfSha256, 'annotation and extraction PDF digests differ')
  assert(/^[0-9a-f]{40}$/.test(String(annotationCall.engine_commit)), 'annotation engine_commit is invalid')
  assert.equal(annotationCall.schema, 'pdf_oxide.annotation_call.v1')
  assert(Array.isArray(annotationCall.items) && annotationCall.items.length > 0, 'live annotation queue is empty')
  assert(Array.isArray(extracted.sections) && extracted.sections.length > 0, 'live section tree is empty')
  assert(Array.isArray(extracted.blocks) && extracted.blocks.length > 0, 'live block stream is empty')

  const sourcePdf = String(extracted.source_pdf ?? '')
  assert(sourcePdf.startsWith('/'), 'source_pdf must be the located absolute corpus path')
  const sourceBytes = await readFile(sourcePdf)
  assert.equal(sha256(sourceBytes), pdfSha256, 'source PDF bytes do not match extraction metadata')

  const manifest = jsonRecord(extracted.metadata?.page_images, 'extracted page-image manifest')
  assert.equal(manifest.schema, 'pdf_oxide.page_image.v1')
  assert(Array.isArray(manifest.images) && manifest.images.length === extracted.page_count)
  const dimensions = pageDimensions(sourcePdf, Number(extracted.page_count))
  const dimensionByPage = new Map(dimensions.map((entry) => [entry.page, entry]))
  const documentId = basename(sourcePdf, '.pdf')

  for (const image of manifest.images) {
    const imagePath = join(liveRoot, 'page_images', image.filename)
    const bytes = await readFile(imagePath)
    assert.equal(sha256(bytes), image.byte_sha256, `page ${image.page} image byte hash mismatch`)
  }

  const sectionTree = {
    schema: 'pdf_oxide.section_tree.v2',
    pdf_sha256: pdfSha256,
    sections: extracted.sections.map((section) => ({
      ...section,
      children: [...(section.children_ids ?? [])],
    })),
  }

  const pageImageIndex = {
    schema: 'pdf_oxide.live_page_images.v1',
    pages: manifest.images.map((image) => {
      const dimensionsForPage = dimensionByPage.get(Number(image.page))
      assert(dimensionsForPage, `missing dimensions for page ${image.page}`)
      return {
        doc: documentId,
        page: image.page,
        pdf_sha256: pdfSha256,
        page_image_refs: [{
          href: `page_images/${image.filename}`,
          sha256: image.filename.replace(/\.png$/, ''),
          page: image.page,
          width: dimensionsForPage.width,
          height: dimensionsForPage.height,
          pdf_sha256: pdfSha256,
        }],
      }
    }),
  }

  const calibrationSource = annotationCall.items.find((item) => (
    item.kind === 'block'
    && Array.isArray(item.bbox)
    && typeof item.confidence === 'number'
    && typeof item.current_type === 'string'
  ))
  assert(calibrationSource, 'no live block annotation can seed calibration')
  const calibrationDimensions = dimensionByPage.get(Number(calibrationSource.page))
  assert(calibrationDimensions, 'calibration page dimensions are missing')
  const calibrationImage = manifest.images.find((image) => image.page === calibrationSource.page)
  assert(calibrationImage, 'calibration page image is missing')
  const calibrationRow = {
    doc: documentId,
    quintile: 0,
    page: calibrationSource.page,
    bbox: normalizedTopLeftXyxy(
      calibrationSource.bbox,
      calibrationDimensions.width,
      calibrationDimensions.height,
    ),
    type: calibrationSource.current_type,
    confidence: calibrationSource.confidence,
    text: calibrationSource.text_excerpt ?? '',
    label: null,
    page_image_refs: [{
      href: `../page_images/${calibrationImage.filename}`,
      sha256: calibrationImage.filename.replace(/\.png$/, ''),
      page: calibrationSource.page,
      width: calibrationDimensions.width,
      height: calibrationDimensions.height,
      pdf_sha256: pdfSha256,
    }],
  }

  const retrievalElement = extracted.blocks.find((block) => (
    typeof block.id === 'string'
    && typeof block.section_id === 'string'
    && Array.isArray(block.bbox)
    && typeof block.text === 'string'
    && block.text.length > 80
  ))
  assert(retrievalElement, 'no live block can seed retrieval evidence')
  const retrievalDimensions = dimensionByPage.get(Number(retrievalElement.page))
  const retrievalImage = manifest.images.find((image) => image.page === retrievalElement.page)
  assert(retrievalDimensions && retrievalImage, 'retrieval page evidence is incomplete')
  const retrievalSection = extracted.sections.find((section) => section.id === retrievalElement.section_id)
  assert(retrievalSection, 'retrieval element section is missing')

  const retrievalResult = {
    answer: 'The current engine extraction is supported by this exact source element and original PDF page.',
    pdf_sha256: pdfSha256,
    evidence: [{
      element_id: retrievalElement.id,
      type: retrievalElement.type,
      page: retrievalElement.page,
      bbox: normalizedTopLeftXyxy(
        retrievalElement.bbox,
        retrievalDimensions.width,
        retrievalDimensions.height,
      ),
      pdf_sha256: pdfSha256,
      section_id: retrievalElement.section_id,
      text: retrievalElement.text,
      provenance: retrievalElement.provenance,
      doc: documentId,
    }],
  }

  await mkdir(join(liveRoot, 'calibration/page_images'), { recursive: true })
  await writeFile(join(liveRoot, 'section_tree.json'), `${JSON.stringify(sectionTree, null, 2)}\n`)
  await writeFile(join(liveRoot, 'page_images_v1.json'), `${JSON.stringify(pageImageIndex, null, 2)}\n`)
  await writeFile(join(liveRoot, 'retrieval_result.json'), `${JSON.stringify(retrievalResult, null, 2)}\n`)
  await writeFile(join(liveRoot, 'calibration/sample_v1.jsonl'), `${JSON.stringify(calibrationRow)}\n`)
  await writeFile(join(liveRoot, 'calibration/page_images_v1.json'), `${JSON.stringify({
    schema: 'pdf_oxide.calibration_page_images.v1',
    documents: {
      [documentId]: {
        ...manifest,
        pdf_sha256: pdfSha256,
        directory: 'page_images',
        images: [calibrationImage],
      },
    },
  }, null, 2)}\n`)
  await copyFile(
    join(liveRoot, 'page_images', calibrationImage.filename),
    join(liveRoot, 'calibration/page_images', calibrationImage.filename),
  )

  const receipt = {
    schema: 'pdf_oxide.ux_round2_live_receipt.v1',
    source_pdf: sourcePdf,
    pdf_sha256: pdfSha256,
    engine_commit: annotationCall.engine_commit,
    page_count: extracted.page_count,
    block_count: extracted.blocks.length,
    table_count: extracted.tables.length,
    figure_count: extracted.figures.length,
    annotation_item_count: annotationCall.items.length,
    annotation_reason_counts: Object.fromEntries(
      annotationCall.items.reduce((counts, item) => {
        counts.set(item.reason, (counts.get(item.reason) ?? 0) + 1)
        return counts
      }, new Map()),
    ),
    retrieval_element_id: retrievalElement.id,
    retrieval_section_id: retrievalSection.id,
    retrieval_image_filename: retrievalImage.filename,
    calibration_page: calibrationSource.page,
    calibration_image_filename: calibrationImage.filename,
    page_dimensions: dimensions,
  }
  await writeFile(join(liveRoot, 'live_receipt.json'), `${JSON.stringify(receipt, null, 2)}\n`)
  return receipt
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  const root = process.argv[2] ? resolve(process.argv[2]) : DEFAULT_LIVE_ROOT
  const receipt = await prepareRound2LiveArtifacts(root)
  console.log(JSON.stringify(receipt, null, 2))
}
