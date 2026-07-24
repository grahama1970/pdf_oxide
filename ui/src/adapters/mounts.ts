export interface AnnotationCallMount {
  url: string
  doc_id: string
  item_count: number
  reasons: Record<string, number>
}

export interface PageImageIndexMount {
  url: string
  document_ids: string[]
  page_count: number
}

export interface RetrievalResultMount {
  url: string
  label: string
  page_image_index_url?: string
}

export interface CalibrationSampleMount {
  url: string
  item_count: number
  page_image_index_url?: string
  labels_endpoint: string
}

export interface PdfLabMounts {
  artifacts_root: string
  annotation_calls: AnnotationCallMount[]
  page_image_indexes: PageImageIndexMount[]
  retrieval_results: RetrievalResultMount[]
  calibration_samples: CalibrationSampleMount[]
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

export function parsePdfLabMounts(value: unknown): PdfLabMounts {
  if (!isRecord(value)) throw new Error('mount response must be an object')
  const annotationCalls = Array.isArray(value.annotation_calls) ? value.annotation_calls : []
  const pageImageIndexes = Array.isArray(value.page_image_indexes) ? value.page_image_indexes : []
  const retrievalResults = Array.isArray(value.retrieval_results) ? value.retrieval_results : []
  const calibrationSamples = Array.isArray(value.calibration_samples) ? value.calibration_samples : []
  return {
    artifacts_root: typeof value.artifacts_root === 'string' ? value.artifacts_root : '(artifact root unavailable)',
    annotation_calls: annotationCalls.filter(isRecord).map((entry) => ({
      url: String(entry.url ?? ''),
      doc_id: String(entry.doc_id ?? 'unknown document'),
      item_count: Number(entry.item_count ?? 0),
      reasons: isRecord(entry.reasons)
        ? Object.fromEntries(Object.entries(entry.reasons).map(([key, count]) => [key, Number(count)]))
        : {},
    })).filter((entry) => entry.url),
    page_image_indexes: pageImageIndexes.filter(isRecord).map((entry) => ({
      url: String(entry.url ?? ''),
      document_ids: Array.isArray(entry.document_ids) ? entry.document_ids.map(String) : [],
      page_count: Number(entry.page_count ?? 0),
    })).filter((entry) => entry.url),
    retrieval_results: retrievalResults.filter(isRecord).map((entry) => ({
      url: String(entry.url ?? ''),
      label: String(entry.label ?? entry.url ?? 'Retrieval result'),
      ...(typeof entry.page_image_index_url === 'string'
        ? { page_image_index_url: entry.page_image_index_url }
        : {}),
    })).filter((entry) => entry.url),
    calibration_samples: calibrationSamples.filter(isRecord).map((entry) => ({
      url: String(entry.url ?? ''),
      item_count: Number(entry.item_count ?? 0),
      labels_endpoint: typeof entry.labels_endpoint === 'string'
        ? entry.labels_endpoint
        : '/api/pdf-lab/calibration/labels',
      ...(typeof entry.page_image_index_url === 'string'
        ? { page_image_index_url: entry.page_image_index_url }
        : {}),
    })).filter((entry) => entry.url),
  }
}
