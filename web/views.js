// HTML for each view. Every interpolated value is escaped by `html` unless it is itself `html`.

const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }

class Html {
  constructor(text) { this.text = text }
  toString() { return this.text }
}

function toHtml(value) {
  if (value == null || value === false) return ''
  if (value instanceof Html) return value.text
  if (Array.isArray(value)) return value.map(toHtml).join('')
  return String(value).replace(/[&<>"']/g, c => ESCAPES[c])
}

export function html(strings, ...values) {
  return new Html(strings.reduce((out, s, i) => out + toHtml(values[i - 1]) + s))
}

const number = n => n.toLocaleString('en-CA')
const percent = x => `${Math.round(x * 100)}%`
const plural = (n, word) => `${n ? number(n) : 'no'} ${word}${n === 1 ? '' : 's'}`
const columnHref = column => `#column=${encodeURIComponent(column.key)}`
const portalHref = name => `https://open.toronto.ca/dataset/${encodeURIComponent(name)}/`
export const datasetTitle = (data, column) => data.packages.get(column.package)?.title || column.package

function examples(values) {
  return html`
    <p class="examples"><span class="label">Examples</span>
      ${values.map((v, i) => html`${i ? html`<span class="sep" aria-hidden="true">·</span>` : ''}<q>${v}</q>`)}</p>`
}

const overlapLevel = score => score >= 0.8 ? 'high' : score >= 0.5 ? 'medium' : 'low'

function overlapDirection(data, from, to, ratio, shared) {
  return html`
    <div class="overlap-direction">
      <div class="overlap-heading"><span>${from.column} <span aria-hidden="true">→</span><span class="visually-hidden"> to </span> ${to.column}</span><strong>${percent(ratio)}</strong></div>
      <span class="overlap-bar ${overlapLevel(ratio)}" aria-hidden="true"><span style="width: ${percent(ratio)}"></span></span>
      <p>${number(shared)} of ${number(from.n_distinct)} distinct values appear in ${to.column}.</p>
      <p class="overlap-context">From ${datasetTitle(data, from)} · ${from.resource}</p>
    </div>`
}

function scoreExplanation(data, column, other, join) {
  const id = `overlap-${other.id}`
  return html`
    <div class="score-control">
      <button type="button" class="score ${overlapLevel(join.score)}" aria-expanded="false" aria-controls="${id}" aria-haspopup="dialog">
        <span class="value">${percent(join.score)}</span>
        <span class="score-label">Value overlap <span aria-hidden="true">ⓘ</span></span>
        <span class="bar" aria-hidden="true"><span style="width: ${percent(join.score)}"></span></span>
        <span class="visually-hidden"> with ${other.column} in ${datasetTitle(data, other)}. Show explanation.</span>
      </button>
      <div id="${id}" class="score-popover" popover="manual" role="dialog" aria-labelledby="${id}-title">
        <header><h2 id="${id}-title">Why ${percent(join.score)}?</h2><button type="button" class="close-score" aria-label="Close explanation">×</button></header>
        ${overlapDirection(data, column, other, join.contained, join.shared)}
        ${overlapDirection(data, other, column, join.contains, join.shared)}
        <p class="overlap-note">The score uses the higher percentage. Shared values do not guarantee the columns mean the same thing.</p>
        <a href="about.html#the-method">How scores work →</a>
      </div>
    </div>`
}

export function message(text, detail = '') {
  return html`<section class="message"><p>${text}</p>${detail && html`<p class="dim">${detail}</p>`}</section>`
}

export function columnPage(data, column, { includeSamePackage, shown, wasReported }) {
  const all = data.joinsOf[column.id]
  const samePackage = all.filter(j => data.columns[j.other].package === column.package).length
  const joins = includeSamePackage ? all : all.filter(j => data.columns[j.other].package !== column.package)
  const owner = data.packages.get(column.package)?.owner

  return html`
    <section class="column-info">
      <p class="dim">${[column.resource, owner].filter(Boolean).join(' · ')} ·
        <a href="${portalHref(column.package)}" target="_blank" rel="noopener">open.toronto.ca ↗</a></p>
      ${examples(column.samples)}
    </section>

    <section>
      <div class="list-header">
        <h2>${plural(joins.length, 'joinable column')}</h2>
        ${samePackage > 0 && html`
          <label class="toggle">
            <input type="checkbox" id="same-package" ${includeSamePackage ? html`checked` : ''}>
            Include ${number(samePackage)} from the same dataset
          </label>`}
      </div>
      <ol class="joins">
        ${joins.slice(0, shown).map(j => {
          const other = data.columns[j.other]
          const reported = wasReported(column.key, other.key)
          return html`
            <li class="join">
              ${scoreExplanation(data, column, other, j)}
              <button type="button" class="report-join" data-other="${other.id}" title="${reported ? 'Reported' : 'Report this join'}" ${reported && html`aria-disabled="true"`}>
                <span class="visually-hidden">${reported ? 'Reported' : 'Report'}</span><span class="visually-hidden"> the join with ${other.column} in ${datasetTitle(data, other)}</span>
              </button>
              <div>
                <a class="column-link" href="${columnHref(other)}">
                  <span class="dataset">${datasetTitle(data, other)}</span>
                  <span class="sep" aria-hidden="true">›</span>
                  <span class="column-name">${other.column}</span>
                </a>
                <span class="resource dim">${other.resource}</span>
                ${examples(other.samples.slice(0, 3))}
              </div>
            </li>`
        })}
      </ol>
      ${joins.length > shown && html`<button type="button" id="show-more">Show more (${number(joins.length - shown)} left)</button>`}
    </section>`
}
