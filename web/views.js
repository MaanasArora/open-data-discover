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
              <div class="score" title="${percent(j.contained)} of this column’s values appear there; ${percent(j.contains)} of its values appear here">
                <span class="value">${j.score.toFixed(2)}</span>
                <span class="bar"><span style="width: ${percent(j.score)}"></span></span>
              </div>
              <button type="button" class="report-join" data-other="${other.id}" ${reported && html`aria-disabled="true"`}>
                <span>${reported ? 'Reported' : 'Report'}</span><span class="visually-hidden"> the join with ${other.column} in ${datasetTitle(data, other)}</span>
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
