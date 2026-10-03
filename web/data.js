// Load the analysis results and index them for the page.
//
// data/columns.parquet  one row per column (id = row number)
// data/joins.parquet    one row per scored pair of columns, best first
// data/manifest.json    what was analyzed
//
// Nothing is scored here: joins arrive sorted by score, and each column's list keeps that order.
// The score (0-1) is the share of one column's values found in the other beyond chance; analyze
// only keeps joins with enough evidence that the overlap is not chance (see analyze/README.md).
// Joins with a match strength below MIN_SCORE are dropped.

import { parquetReadObjects } from './vendor/hyparquet.js'

const MIN_SCORE = 0.5  // weaker matches are not shown

async function fetchOk(url) {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`)
  return response
}

async function readParquet(url) {
  const file = await (await fetchOk(url)).arrayBuffer()
  return parquetReadObjects({ file })
}

export async function loadData(base = 'data') {
  const [manifest, columns, joins] = await Promise.all([
    fetchOk(`${base}/manifest.json`).then(r => r.json()),
    readParquet(`${base}/columns.parquet`),
    readParquet(`${base}/joins.parquet`),
  ])

  const packages = new Map(manifest.packages.map(p => [p.name, p]))
  const byKey = new Map(columns.map(c => [c.key, c]))

  // Each column's joins, from its own point of view, in score order:
  // contained = share of this column's values found in the other, expected = the share
  // expected by chance, contains = share of the other's values found in this one.
  const joinsOf = columns.map(() => [])
  for (const j of joins) {
    if (j.score < MIN_SCORE) break  // joins are sorted by score
    const { shared, score } = j
    joinsOf[j.id_a].push({ other: j.id_b, contained: j.containment_a_in_b, expected: j.expected_a_in_b,
      contains: j.containment_b_in_a, shared, score })
    joinsOf[j.id_b].push({ other: j.id_a, contained: j.containment_b_in_a, expected: j.expected_b_in_a,
      contains: j.containment_a_in_b, shared, score })
  }

  // The picker offers datasets, and their columns, that join with another dataset.
  const title = name => packages.get(name)?.title || name
  const datasets = new Map()
  for (const column of columns) {
    if (!joinsOf[column.id].some(j => columns[j.other].package !== column.package)) continue
    if (!datasets.has(column.package)) datasets.set(column.package, { name: column.package, title: title(column.package), columns: [] })
    datasets.get(column.package).columns.push(column)
  }

  return {
    columns,
    packages,
    byKey,
    joinsOf,
    datasets: [...datasets.values()].sort((a, b) => a.title.localeCompare(b.title)),
  }
}
