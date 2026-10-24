// A compact, bounded summary of the page for the agent: title, headings, links, controls.
(limit) => {
  const text = (el) => (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
  };
  const headings = [...document.querySelectorAll("h1,h2,h3")]
    .filter(visible)
    .slice(0, 30)
    .map((h) => `${h.tagName.toLowerCase()}: ${text(h).slice(0, 120)}`);
  const links = [...document.querySelectorAll("a[href]")]
    .map((a) => ({ href: a.href, text: text(a).slice(0, 80), visible: visible(a) }))
    .slice(0, 400);
  const buttons = [...document.querySelectorAll("button,[role=button],summary")]
    .filter(visible)
    .slice(0, 40)
    .map((b) => text(b).slice(0, 60) || b.getAttribute("aria-label") || "(no label)");
  return {
    title: document.title || "",
    lang: document.documentElement.lang || "",
    url: location.href,
    width: document.documentElement.clientWidth,
    height: document.documentElement.scrollHeight,
    headings,
    links,
    buttons,
    forms: document.forms.length,
    text: text(document.body || document.documentElement).slice(0, limit),
  };
};
