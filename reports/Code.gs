// Receives join reports from the site and appends one row per report to the spreadsheet's first sheet.
// Deploy as a web app (Execute as: Me, Who has access: Anyone); see README.md.

/** @OnlyCurrentDoc  The public endpoint may touch only this spreadsheet, not all of the owner's. */

const REASONS = ['unrelated', 'coincidental', 'wrong-entity', 'other']
const HEADER = ['received_at', 'pair', 'column_a', 'column_b', 'label_a', 'label_b', 'score', 'reason', 'note']

function doPost(e) {
  let report
  try {
    report = JSON.parse(e.postData.contents)
  } catch (error) {
    return reply({ ok: false, error: 'not JSON' })
  }
  if (!report || typeof report !== 'object') return reply({ ok: false, error: 'not a report' })

  const a = text(report.column_a, 300)
  const b = text(report.column_b, 300)
  const reason = text(report.reason, 20)
  const score = Number(report.score)
  if (!a.includes('::') || !b.includes('::') || a === b) return reply({ ok: false, error: 'bad columns' })
  if (!REASONS.includes(reason)) return reply({ ok: false, error: 'bad reason' })

  const lock = LockService.getScriptLock()
  if (!lock.tryLock(10000)) return reply({ ok: false, error: 'busy, try again' })
  try {
    const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0]
    if (sheet.getLastRow() === 0) sheet.appendRow(HEADER)
    sheet.appendRow([
      new Date(),
      [a, b].sort().join(' | '),  // the same join reported from either side
      a,
      b,
      text(report.label_a, 300),
      text(report.label_b, 300),
      score >= 0 && score <= 1 ? score : '',
      reason,
      text(report.note, 1000),
    ].map(asTyped))
  } finally {
    lock.releaseLock()
  }
  return reply({ ok: true })
}

function text(value, max) {
  return typeof value === 'string' ? value.trim().slice(0, max) : ''
}

// A leading ', which Sheets hides, keeps text as typed: never run as a formula or turned into a number
// or date (a note of "1/2" would otherwise become 2 January).
function asTyped(cell) {
  return typeof cell === 'string' && cell ? `'${cell}` : cell
}

function reply(body) {
  return ContentService.createTextOutput(JSON.stringify(body)).setMimeType(ContentService.MimeType.JSON)
}
