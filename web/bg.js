// Decorative dot-grid background: dots near the cursor light up and the glow
// spreads a few hops along random links to neighbouring dots.
// Effect modelled on https://carte.utoronto.ca/ (reimplemented, not copied).
const STEP = 24, RADIUS = 60, HOPS = 4, DECAY = 0.55, EASE = 0.08;
const DOT_BASE = 0.04, DOT_LIT = 0.18, LINE_LIT = 0.15;

const canvas = document.createElement("canvas");
canvas.setAttribute("aria-hidden", "true");
canvas.style.cssText = "position:fixed;inset:0;z-index:-1;pointer-events:none";
document.body.prepend(canvas);
const ctx = canvas.getContext("2d");
const still = matchMedia("(prefers-reduced-motion: reduce)");

let cols, dots, mouse = null, frame = 0, rgb = "0,0,0";

function build() {
  const dpr = devicePixelRatio || 1, w = innerWidth, h = innerHeight;
  canvas.width = w * dpr; canvas.height = h * dpr;
  canvas.style.width = w + "px"; canvas.style.height = h + "px";
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  cols = Math.ceil(w / STEP) + 1;
  const rows = Math.ceil(h / STEP) + 1;
  dots = [];
  for (let y = 0; y < rows; y++)
    for (let x = 0; x < cols; x++)
      dots.push({ x: x * STEP, y: y * STEP, links: [], now: 0, target: 0 });
  // Link each dot to 2–4 random dots within 3 steps "ahead" of it (so each pair is considered once).
  dots.forEach((d, i) => {
    const cx = i % cols, cy = Math.floor(i / cols), near = [];
    for (let dy = 0; dy <= 3; dy++)
      for (let dx = -2; dx <= 3; dx++) {
        if (dy === 0 && dx <= 0) continue;
        const nx = cx + dx, ny = cy + dy;
        if (nx < cols && nx >= 0 && ny < rows && Math.hypot(dx, dy) <= 3) near.push(ny * cols + nx);
      }
    near.sort(() => Math.random() - 0.5);
    for (const j of near.slice(0, 2 + Math.floor(Math.random() * 3))) {
      const weight = 0.6 + Math.random() * 0.4;
      d.links.push({ j, weight });
      dots[j]?.links.push({ j: i, weight });
    }
  });
  rgb = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim()
    .match(/[0-9a-f]{2}/gi).map(h => parseInt(h, 16)).join(",");
  draw();
}

// Breadth-first spread of activation from the dots under the cursor.
function activate() {
  for (const d of dots) d.target = 0;
  if (!mouse) return;
  const queue = [];
  dots.forEach((d, i) => {
    const dist = Math.hypot(d.x - mouse.x, d.y - mouse.y);
    if (dist < RADIUS) { d.target = (1 - dist / RADIUS) ** 1.5; queue.push([i, 0]); }
  });
  while (queue.length) {
    const [i, hops] = queue.shift();
    if (hops >= HOPS) continue;
    for (const { j, weight } of dots[i].links) {
      const a = dots[i].target * DECAY * weight;
      if (a > dots[j].target && a > 0.01) { dots[j].target = a; queue.push([j, hops + 1]); }
    }
  }
}

function draw() {
  frame = 0;
  let moving = false;
  for (const d of dots) {
    d.now += (d.target - d.now) * EASE;
    if (Math.abs(d.target - d.now) > 0.001) moving = true; else d.now = d.target;
  }
  ctx.clearRect(0, 0, innerWidth, innerHeight);
  ctx.lineWidth = 0.5;
  for (const d of dots)
    for (const { j, weight } of d.links) {
      const a = Math.min(d.now, dots[j].now) * LINE_LIT * weight;
      if (a < 0.002) continue;
      ctx.strokeStyle = `rgba(${rgb},${a})`;
      ctx.beginPath(); ctx.moveTo(d.x, d.y); ctx.lineTo(dots[j].x, dots[j].y); ctx.stroke();
    }
  for (const d of dots) {
    ctx.fillStyle = `rgba(${rgb},${DOT_BASE + (DOT_LIT - DOT_BASE) * d.now})`;
    ctx.beginPath(); ctx.arc(d.x, d.y, 1.5, 0, 2 * Math.PI); ctx.fill();
  }
  if (moving) frame = requestAnimationFrame(draw);  // idle = no animation loop
}

function wake() { activate(); if (!frame) frame = requestAnimationFrame(draw); }

addEventListener("pointermove", e => {
  if (still.matches || e.pointerType !== "mouse") return;
  mouse = { x: e.clientX, y: e.clientY }; wake();
});
document.addEventListener("pointerleave", () => { mouse = null; wake(); });
addEventListener("resize", build);
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", build);
build();
