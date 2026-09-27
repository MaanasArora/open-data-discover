// Page state and routing. The URL hash holds the view:
//   #column=<key>   one column and the columns that join with it
//   (empty)         home: pick a dataset, then a column

import { loadData } from './data.js'
import { columnPage, html, message } from './views.js'

const TITLE = 'Open Data Discover: Toronto'
const PAGE_SIZE = 25

const main = document.querySelector('#main')
const datasetInput = document.querySelector('#dataset')
const columnSelect = document.querySelector('#column')
const state = { key: null, includeSamePackage: false, shown: PAGE_SIZE }
let data

// The dataset box suggests titles as you type; typing or picking a full title selects it.
function fillDatasets() {
  document.querySelector('#datasets').innerHTML = html`${data.datasets.map(d => html`<option value="${d.title}">`)}`
  datasetInput.placeholder = 'Type to find a dataset'
  datasetInput.disabled = false
}

function datasetNamed(title) {
  const t = title.trim().toLowerCase()
  return data.datasets.find(d => d.title.toLowerCase() === t)
}

// Columns of one dataset, grouped by resource (file).
function fillColumns(dataset) {
  const groups = new Map()
  for (const c of dataset?.columns ?? []) groups.set(c.resource, [...(groups.get(c.resource) ?? []), c])
  columnSelect.innerHTML = html`
    <option value="">Choose a column</option>
    ${[...groups].map(([resource, columns]) => html`
      <optgroup label="${resource}">
        ${columns.map(c => html`<option value="${c.key}">${c.column}</option>`)}
      </optgroup>`)}`
  columnSelect.disabled = !dataset
}

function render() {
  const key = new URLSearchParams(location.hash.slice(1)).get('column')
  document.body.classList.toggle('home', !key)
  if (!key) {
    main.innerHTML = ''
    datasetInput.value = ''
    fillColumns(null)
    document.title = TITLE
    return
  }

  const column = data.byKey.get(key)
  if (key !== state.key) {
    Object.assign(state, { key, shown: PAGE_SIZE })
    window.scrollTo(0, 0)
  }
  if (!column) {
    main.innerHTML = message('This column is not in the analysis.', key)
    document.title = TITLE
    return
  }
  const dataset = data.datasets.find(d => d.name === column.package)
  if (datasetNamed(datasetInput.value) !== dataset) {
    datasetInput.value = dataset?.title ?? ''
    fillColumns(dataset)
  }
  columnSelect.value = key
  main.innerHTML = columnPage(data, column, state)
  document.title = `${column.column} · ${TITLE}`
}

datasetInput.addEventListener('input', () => {
  const dataset = datasetNamed(datasetInput.value)
  fillColumns(dataset)
  if (!dataset) return
  datasetInput.value = dataset.title
  columnSelect.focus()
})
columnSelect.addEventListener('change', () => {
  if (columnSelect.value) location.hash = `column=${encodeURIComponent(columnSelect.value)}`
})
document.querySelector('#picker').addEventListener('submit', event => event.preventDefault())

main.addEventListener('change', event => {
  if (event.target.id !== 'same-package') return
  state.includeSamePackage = event.target.checked
  render()
  main.querySelector('#same-package')?.focus()
})
main.addEventListener('click', event => {
  if (event.target.id !== 'show-more') return
  state.shown += PAGE_SIZE
  render()
  main.querySelector('#show-more')?.focus()
})
window.addEventListener('hashchange', render)

try {
  data = await loadData()
  fillDatasets()
  render()
} catch (error) {
  console.error(error)
  datasetInput.placeholder = 'No data'
  main.innerHTML = message(
    'Could not load the analysis results.',
    `Run “uv run analyze.py run -r ../web/data” in analyze/, then serve this folder over HTTP. (${error.message})`,
  )
}
