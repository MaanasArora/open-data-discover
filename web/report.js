// Reporting a join that makes no sense. Reports go to a Google Sheet through a Google Apps Script
// web app (see reports/README.md); REPORTS_URL is that web app's deployment URL.

const REPORTS_URL = 'https://script.google.com/macros/s/AKfycbwPIql969AdnJ-jQFjARXdB2nBd_PFcfq_gtVeZiIvdezlCVy2NN618KRV2SOESk94A/exec'

const dialog = document.querySelector('#report')
const form = dialog.querySelector('form')
const status = dialog.querySelector('.status')
const send = form.querySelector('[type=submit]')
const sent = new Set()  // joins reported since the page loaded
let current             // { button, report } for the open dialog

const pairKey = (a, b) => [a, b].sort().join('|')
export const wasReported = (a, b) => sent.has(pairKey(a, b))

// Opens the dialog beside `button` for a report of { column_a, column_b, label_a, label_b, score }.
export function openReport(button, report) {
  current = { button, report }
  form.reset()
  send.disabled = false
  status.textContent = ''
  dialog.querySelector('.which').textContent = report.label_b
  dialog.showModal()
  place()
}

// Beside the button, over the join it reports, and inside the window.
function place() {
  const { button } = current
  const join = button.closest('.join').getBoundingClientRect()
  const { width, height } = dialog.getBoundingClientRect()
  const { clientWidth, clientHeight } = document.documentElement  // the window without its scrollbars
  const gap = 8
  dialog.style.left = `${Math.max(gap, Math.min(button.getBoundingClientRect().right + 2 * gap, clientWidth - width - gap))}px`
  dialog.style.top = `${Math.max(gap, Math.min(join.top, clientHeight - height - gap))}px`
}

function showStatus(text) {
  status.textContent = text
  place()  // the message makes the dialog taller
}

form.addEventListener('submit', async event => {
  event.preventDefault()
  const sending = current  // the dialog may be closed, or opened for another join, before the reply
  const fields = new FormData(form)
  const report = { ...sending.report, reason: fields.get('reason'), note: fields.get('note').trim() }
  send.disabled = true
  showStatus('Sending…')
  try {
    if (!REPORTS_URL) throw new Error('reporting is not set up yet')
    // A plain-text body keeps this a simple request, which Apps Script accepts without a CORS preflight.
    const result = await (await fetch(REPORTS_URL, { method: 'POST', body: JSON.stringify(report) })).json()
    if (!result.ok) throw new Error(result.error)
    sent.add(pairKey(report.column_a, report.column_b))
    sending.button.firstElementChild.textContent = 'Reported'  // as views.js renders it after a re-render
    sending.button.setAttribute('aria-disabled', 'true')
    if (current === sending) dialog.close()
  } catch (error) {
    if (current === sending) showStatus(`Could not send the report: ${error.message}.`)
  } finally {
    if (current === sending) {
      send.disabled = false
      if (dialog.open && !dialog.contains(document.activeElement)) send.focus()  // disabling it dropped focus
    }
  }
})

// Cancel, or a click that starts and ends on the backdrop (the form fills the box, so only the backdrop is
// the dialog itself; a drag out of the form also ends there).
let pressedBackdrop = false
dialog.addEventListener('pointerdown', event => { pressedBackdrop = event.target === dialog })
dialog.addEventListener('click', event => {
  if ((pressedBackdrop && event.target === dialog) || event.target.closest('.cancel')) dialog.close()
})
addEventListener('resize', () => dialog.open && place())
addEventListener('hashchange', () => dialog.close())  // runs before app.js re-renders the page under it
