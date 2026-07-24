import { resolve } from 'path'
import { fileURLToPath } from 'url'

import { writeAnnotationQueueManifest } from '../server/beforeMainContracts'

const uiRoot = resolve(fileURLToPath(new URL('..', import.meta.url)))
const repoRoot = resolve(uiRoot, '..')
const artifactsRoot = resolve(repoRoot, 'artifacts', 'pdf-lab')
const outputPath = resolve(artifactsRoot, 'annotation_queue_manifest_v1.json')

const manifest = writeAnnotationQueueManifest(artifactsRoot, outputPath)
console.log(JSON.stringify({
  output: outputPath,
  priority_order: manifest.priority_order,
  counts: manifest.counts,
  source_hashes: manifest.source_hashes,
}, null, 2))
