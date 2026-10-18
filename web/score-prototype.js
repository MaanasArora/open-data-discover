// Prototype for #8, #35 and #36: two-way overlap on the existing results page.
// Hover/focus previews the explanation; click/tap pins it. Popovers keep the panel above the results.
const main = document.querySelector('#main')
const mobile = matchMedia('(max-width: 40rem)')
let active = null
let pinned = false
let closeTimer
let restoringFocus = false

function position() {
  if (!active) return
  const { button, panel } = active
  panel.style.left = panel.style.top = ''
  if (mobile.matches) return
  const rect = button.getBoundingClientRect()
  const { width, height } = panel.getBoundingClientRect()
  const below = rect.bottom + 8
  const beside = rect.right + 12 + width <= innerWidth - 16
  const left = beside ? rect.right + 12 : rect.left
  const top = beside ? Math.max(16, Math.min(rect.top, innerHeight - height - 16))
    : below + height <= innerHeight - 16 ? below : Math.max(16, rect.top - height - 8)
  panel.style.left = `${Math.max(16, Math.min(left, innerWidth - width - 16))}px`
  panel.style.top = `${top}px`
}

export function closeScore() {
  clearTimeout(closeTimer)
  if (!active) return
  active.button.setAttribute('aria-expanded', 'false')
  active.panel.hidePopover()
  active = null
  pinned = false
}

function open(button) {
  clearTimeout(closeTimer)
  if (active?.button === button) return
  closeScore()
  const panel = document.getElementById(button.getAttribute('aria-controls'))
  active = { button, panel }
  panel.showPopover()
  button.setAttribute('aria-expanded', 'true')
  position()
}

function inside(target) {
  return target instanceof Node && active && (active.button.contains(target) || active.panel.contains(target))
}

function scheduleClose() {
  clearTimeout(closeTimer)
  closeTimer = setTimeout(() => {
    if (!pinned && !inside(document.activeElement)) closeScore()
  }, 180)
}

function closeAndFocus() {
  const trigger = active.button
  closeScore()
  restoringFocus = true
  trigger.focus({ preventScroll: true })
  restoringFocus = false
}

main.addEventListener('pointerover', event => {
  if (event.pointerType !== 'mouse') return
  if (inside(event.target)) clearTimeout(closeTimer)
  const button = event.target.closest('.score')
  if (button && !pinned) open(button)
})

main.addEventListener('pointerout', event => {
  if (inside(event.target) && !inside(event.relatedTarget)) scheduleClose()
})

main.addEventListener('focusin', event => {
  if (restoringFocus) return
  const button = event.target.closest('.score')
  if (button) open(button)
  if (inside(event.target)) clearTimeout(closeTimer)
})

main.addEventListener('focusout', event => {
  if (inside(event.target) && !inside(event.relatedTarget)) scheduleClose()
})

main.addEventListener('click', event => {
  const button = event.target.closest('.score')
  if (button) {
    if (active?.button === button && pinned) closeScore()
    else { open(button); pinned = true }
  }
  if (event.target.closest('.close-score') && active) {
    closeAndFocus()
  }
})

document.addEventListener('click', event => {
  if (active && !inside(event.target)) closeScore()
})
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && active) {
    event.preventDefault()
    closeAndFocus()
  }
})

// Keep the trigger's accessible state in sync if the browser closes the panel.
main.addEventListener('beforetoggle', event => {
  if (event.newState === 'closed' && event.target === active?.panel) {
    active.button.setAttribute('aria-expanded', 'false')
    active = null
    pinned = false
  }
}, true)

window.addEventListener('resize', position)
document.addEventListener('scroll', event => {
  if (active && !active.panel.contains(event.target)) closeScore()
}, true)
