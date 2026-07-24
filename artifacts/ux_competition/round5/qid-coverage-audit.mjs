import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from '../../../ui/node_modules/typescript/lib/typescript.js'

const round5Dir = dirname(fileURLToPath(import.meta.url))
const repositoryRoot = resolve(round5Dir, '../../..')
const files = [
  'ui/src/App.tsx',
  'ui/src/components/annotation/AnnotationQueueRoute.tsx',
  'ui/src/components/calibration/CalibrateRoute.tsx',
  'ui/src/components/retrieval/RetrievalEvidenceView.tsx',
  'ui/src/components/verification/NormalizedPageOverlay.tsx',
]
const interactiveTags = new Set(['a', 'button', 'input', 'select', 'summary', 'textarea'])

function attributeNames(node) {
  return new Set(node.attributes.properties.flatMap((property) => (
    ts.isJsxAttribute(property) ? [property.name.text] : []
  )))
}

function visitSource(sourceFile) {
  const interactive = []
  let registrations = 0

  function visit(node) {
    if (ts.isCallExpression(node) && node.expression.getText(sourceFile) === 'useRegisterAction') {
      registrations += 1
    }
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      const tag = node.tagName.getText(sourceFile)
      if (interactiveTags.has(tag)) {
        const attributes = attributeNames(node)
        const line = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile)).line + 1
        interactive.push({
          line,
          tag,
          data_qid: attributes.has('data-qid'),
          data_qs_action: attributes.has('data-qs-action'),
          title: attributes.has('title'),
        })
      }
    }
    ts.forEachChild(node, visit)
  }

  visit(sourceFile)
  const domComplete = interactive.filter((element) => (
    element.data_qid && element.data_qs_action && element.title
  )).length
  return {
    interactive_elements: interactive.length,
    dom_three_attributes: domComplete,
    useRegisterAction_registrations: registrations,
    all_four_covered: domComplete === interactive.length && registrations === interactive.length
      ? interactive.length
      : Math.min(domComplete, registrations),
    misses: interactive.filter((element) => (
      !element.data_qid || !element.data_qs_action || !element.title
    )),
  }
}

const components = Object.fromEntries(files.map((file) => {
  const path = resolve(repositoryRoot, file)
  const sourceFile = ts.createSourceFile(
    path,
    readFileSync(path, 'utf8'),
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX,
  )
  return [file, visitSource(sourceFile)]
}))

const totals = Object.values(components).reduce((accumulator, component) => ({
  interactive_elements: accumulator.interactive_elements + component.interactive_elements,
  dom_three_attributes: accumulator.dom_three_attributes + component.dom_three_attributes,
  useRegisterAction_registrations: accumulator.useRegisterAction_registrations + component.useRegisterAction_registrations,
  all_four_covered: accumulator.all_four_covered + component.all_four_covered,
}), {
  interactive_elements: 0,
  dom_three_attributes: 0,
  useRegisterAction_registrations: 0,
  all_four_covered: 0,
})

console.log(JSON.stringify({
  schema: 'pdf-lab.qid-coverage-audit.v1',
  interactive_tags: [...interactiveTags],
  components,
  totals,
  pass: Object.values(components).every((component) => (
    component.misses.length === 0
    && component.all_four_covered === component.interactive_elements
  )),
}, null, 2))
