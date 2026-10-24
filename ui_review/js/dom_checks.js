// Deterministic layout checks from DOM geometry. No network, no mutation of the page.
// Returns plain data; Python turns it into findings.
(opts) => {
  const MAX_TEXT = 600;
  const doc = document.documentElement;
  const vw = doc.clientWidth;

  const esc = (s) => (window.CSS && CSS.escape ? CSS.escape(s) : s.replace(/[^\w-]/g, "\\$&"));
  const selector = (el) => {
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 4) {
      if (node.id) {
        parts.unshift(`#${esc(node.id)}`);
        break;
      }
      let part = node.tagName.toLowerCase();
      const parent = node.parentElement;
      if (parent) {
        const same = [...parent.children].filter((c) => c.tagName === node.tagName);
        if (same.length > 1) part += `:nth-of-type(${same.indexOf(node) + 1})`;
      }
      parts.unshift(part);
      node = parent;
      if (node === document.body || node === doc) break;
    }
    return parts.join(" > ");
  };
  const style = (el) => getComputedStyle(el);
  const shown = (el) => {
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      const s = style(n);
      if (s.display === "none" || s.visibility === "hidden" || parseFloat(s.opacity) === 0) return false;
    }
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const scrollsX = (el) => {
    for (let n = el.parentElement; n && n !== doc && n !== document.body; n = n.parentElement) {
      const o = style(n).overflowX;
      if (o === "auto" || o === "scroll" || o === "hidden" || o === "clip") return true;
    }
    return false;
  };
  const label = (el) => (el.innerText || el.textContent || el.getAttribute("aria-label") || "")
    .replace(/\s+/g, " ").trim().slice(0, 80);

  // 1. Horizontal overflow: the document is wider than the viewport.
  let overflow = null;
  const sw = Math.max(doc.scrollWidth, document.body ? document.body.scrollWidth : 0);
  if (sw > vw + 1) {
    const offenders = [];
    for (const el of document.body.querySelectorAll("*")) {
      if (offenders.length >= 5) break;
      const r = el.getBoundingClientRect();
      if (r.right > vw + 1 && r.width > 0 && !scrollsX(el)) {
        const parent = el.parentElement;
        const pr = parent ? parent.getBoundingClientRect() : null;
        if (!pr || pr.right <= vw + 1 || parent === document.body) {
          offenders.push({ selector: selector(el), right: Math.round(r.right), width: Math.round(r.width) });
        }
      }
    }
    overflow = { scrollWidth: sw, viewport: vw, offenders };
  }

  // 2. Broken images: finished loading and has no pixels.
  const images = [];
  for (const img of document.images) {
    if (img.complete && img.naturalWidth === 0 && (img.currentSrc || img.getAttribute("src"))) {
      images.push({ selector: selector(img), src: (img.currentSrc || img.src).slice(0, 300) });
    }
  }

  // 3. Text elements: elements with their own visible text, measured by their line boxes.
  const texts = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
  for (let el = walker.nextNode(); el && texts.length < MAX_TEXT; el = walker.nextNode()) {
    if (["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "SVG"].includes(el.tagName)) continue;
    const own = [...el.childNodes].filter((n) => n.nodeType === 3 && n.textContent.trim());
    if (!own.length || !shown(el)) continue;
    const rects = [];
    for (const node of own) {
      const range = document.createRange();
      range.selectNodeContents(node);
      for (const r of range.getClientRects()) {
        if (r.width > 1 && r.height > 1) rects.push({ x: r.left, y: r.top + scrollY, w: r.width, h: r.height });
      }
    }
    if (rects.length) texts.push({ el, rects });
  }

  // 3a. Clipped: content wider/taller than a box that hides overflow (ellipsis and line-clamp are intentional).
  const clipped = [];
  for (const { el } of texts) {
    if (clipped.length >= 20) break;
    const s = style(el);
    const hidesX = ["hidden", "clip"].includes(s.overflowX);
    const hidesY = ["hidden", "clip"].includes(s.overflowY);
    const intentional = s.textOverflow === "ellipsis" || (s.webkitLineClamp && s.webkitLineClamp !== "none");
    if (intentional) continue;
    if ((hidesX && el.scrollWidth > el.clientWidth + 1) || (hidesY && el.scrollHeight > el.clientHeight + 1)) {
      clipped.push({ selector: selector(el), text: label(el), scroll: [el.scrollWidth, el.scrollHeight], box: [el.clientWidth, el.clientHeight] });
    }
  }

  // 3b. Overlap: line boxes of two unrelated text elements intersect.
  const overlaps = [];
  const area = (r) => r.w * r.h;
  const inter = (a, b) => {
    const w = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
    const h = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
    return w > 0 && h > 0 ? w * h : 0;
  };
  const collide = (a, b) =>
    a.rects.some((ra) => b.rects.some((rb) => inter(ra, rb) > 0.25 * Math.min(area(ra), area(rb))));
  for (let i = 0; i < texts.length && overlaps.length < 20; i++) {
    for (let j = i + 1; j < texts.length && overlaps.length < 20; j++) {
      const a = texts[i], b = texts[j];
      if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
      if (collide(a, b)) {
        overlaps.push({ a: selector(a.el), b: selector(b.el), textA: label(a.el), textB: label(b.el) });
      }
    }
  }

  // 4. Tap targets (touch viewports): interactive controls smaller than the minimum.
  const taps = [];
  if (opts.mobile) {
    const controls = document.querySelectorAll(
      "a[href],button,input:not([type=hidden]),select,textarea,[role=button],[role=link],[role=checkbox],[onclick]"
    );
    for (const el of controls) {
      if (taps.length >= 30) break;
      if (!shown(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.width >= opts.minTap && r.height >= opts.minTap) continue;
      // WCAG 2.5.8 exception: a link inside a sentence.
      if (el.tagName === "A" && style(el).display === "inline") {
        const parentText = (el.parentElement ? el.parentElement.textContent : "").trim();
        if (parentText.length > (el.textContent || "").trim().length + 10) continue;
      }
      taps.push({ selector: selector(el), text: label(el), width: Math.round(r.width), height: Math.round(r.height) });
    }
  }

  const links = [...document.querySelectorAll("a[href]")].map((a) => a.href).slice(0, 400);
  return { overflow, images, clipped, overlaps, taps, links, textCount: texts.length };
};
