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

// The dataset box opens a list of every dataset, filtered by title or publisher as you type.
const datasetList = document.querySelector('#datasets')
let matches = []
let active = -1

function showDatasets() {
  // A dataset already picked shows the whole list, so the user can browse to another.
  const query = datasetNamed(datasetInput.value) ? '' : datasetInput.value.trim().toLowerCase()
  matches = data.datasets.filter(d => `${d.title} ${d.publisher}`.toLowerCase().includes(query))
  datasetList.innerHTML = matches.length
    ? html`${matches.map((d, i) => html`
        <li role="option" id="dataset-option-${i}" aria-selected="${String(i === active)}">
          ${d.title} <span class="publisher">${d.publisher}</span>
        </li>`)}`
    : html`<li class="empty">No matching datasets</li>`
  datasetList.hidden = false
  datasetInput.setAttribute('aria-expanded', 'true')
  if (active < 0) return datasetInput.removeAttribute('aria-activedescendant')
  datasetInput.setAttribute('aria-activedescendant', `dataset-option-${active}`)
  document.getElementById(`dataset-option-${active}`).scrollIntoView({ block: 'nearest' })
}

function hideDatasets() {
  active = -1
  datasetList.hidden = true
  datasetInput.setAttribute('aria-expanded', 'false')
  datasetInput.removeAttribute('aria-activedescendant')
}

function pickDataset(dataset) {
  datasetInput.value = dataset.title
  fillColumns(dataset)
  hideDatasets()
  columnSelect.focus()
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

datasetInput.addEventListener('focus', showDatasets)
datasetInput.addEventListener('click', showDatasets)
datasetInput.addEventListener('blur', hideDatasets)
datasetInput.addEventListener('input', () => {
  active = -1
  fillColumns(datasetNamed(datasetInput.value))
  showDatasets()
})
datasetInput.addEventListener('keydown', event => {
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    const n = matches.length
    if (!datasetList.hidden && n) active = event.key === 'ArrowDown' ? (active + 1) % n : (active < 1 ? n : active) - 1
    showDatasets()
  } else if (event.key === 'Enter' && matches[active]) {
    event.preventDefault()
    pickDataset(matches[active])
  } else if (event.key === 'Escape') {
    hideDatasets()
  }
})
// Keep focus in the box while clicking the list; preventDefault on click stops the label refocusing it.
datasetList.addEventListener('mousedown', event => event.preventDefault())
datasetList.addEventListener('click', event => {
  event.preventDefault()
  const option = event.target.closest('[role=option]')
  if (option) pickDataset(matches[option.id.split('-').pop()])
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
  datasetInput.placeholder = 'Search datasets or publishers'
  datasetInput.disabled = false
  render()
} catch (error) {
  console.error(error)
  datasetInput.placeholder = 'No data'
  main.innerHTML = message(
    'Could not load the analysis results.',
    `Run “uv run analyze.py run -r ../web/data” in analyze/, then serve this folder over HTTP. (${error.message})`,
  )
}
