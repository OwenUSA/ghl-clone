// Shared extraction module.
// THIS FILE IS THE SINGLE SOURCE OF MEASUREMENT for both GHL and our clone.
// Never fork it — two copies drift and produce fake differences.

(() => {
  const PROPS = [
    'display', 'position', 'top', 'right', 'bottom', 'left', 'z-index', 'overflow-y', 'overflow-x',
    'flex-direction', 'align-items', 'justify-content', 'flex-wrap', 'gap', 'row-gap', 'column-gap',
    'grid-template-columns',
    'font-family', 'font-size', 'font-weight', 'font-style', 'line-height', 'letter-spacing',
    'text-transform', 'text-align', 'text-decoration-line', 'white-space',
    'color', 'background-color', 'background-image', 'opacity',
    'padding-top', 'padding-right', 'padding-bottom', 'padding-left',
    'margin-top', 'margin-right', 'margin-bottom', 'margin-left',
    'width', 'height', 'min-height', 'max-width',
    'border-top-width', 'border-right-width', 'border-bottom-width', 'border-left-width',
    'border-top-color', 'border-right-color', 'border-bottom-color', 'border-left-color',
    'border-top-style',
    'border-top-left-radius', 'border-top-right-radius',
    'border-bottom-left-radius', 'border-bottom-right-radius',
    'box-shadow', 'outline-width', 'outline-color',
    'cursor', 'transition-duration', 'transition-property',
  ];

  // Skip chrome that differs by construction or is pure noise.
  const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'META', 'LINK', 'HEAD', 'NOSCRIPT', 'TITLE', 'BR']);

  function isVisible(el) {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') return false;
    if (parseFloat(cs.opacity) === 0) return false;
    return true;
  }

  // Structural path, independent of generated class hashes.
  function pathOf(el) {
    const parts = [];
    let n = el;
    while (n && n.nodeType === 1 && parts.length < 12) {
      let seg = n.tagName.toLowerCase();
      if (n.id && !/^[0-9]/.test(n.id)) { parts.unshift(seg + '#' + n.id); break; }
      const parent = n.parentElement;
      if (parent) {
        const sibs = Array.from(parent.children).filter(c => c.tagName === n.tagName);
        if (sibs.length > 1) seg += ':nth-of-type(' + (sibs.indexOf(n) + 1) + ')';
      }
      parts.unshift(seg);
      n = n.parentElement;
    }
    return parts.join('>');
  }

  function ownText(el) {
    let t = '';
    for (const node of el.childNodes) {
      if (node.nodeType === 3) t += node.nodeValue;
    }
    return t.trim().slice(0, 120);
  }

  function styleOf(el) {
    const cs = getComputedStyle(el);
    const out = {};
    for (const p of PROPS) {
      const v = cs.getPropertyValue(p);
      if (v) out[p] = v.trim();
    }
    return out;
  }

  function themeTokens() {
    // Custom properties declared on :root and on the app shell.
    const out = {};
    for (const sheet of Array.from(document.styleSheets)) {
      let rules;
      try { rules = sheet.cssRules; } catch (e) { continue; } // cross-origin
      if (!rules) continue;
      for (const rule of Array.from(rules)) {
        if (!rule.style || !rule.selectorText) continue;
        if (!/^(:root|html|body|\.hl_wrapper|#app)\b/.test(rule.selectorText)) continue;
        for (const name of Array.from(rule.style)) {
          if (name.startsWith('--')) out[name] = rule.style.getPropertyValue(name).trim();
        }
      }
    }
    return out;
  }

  window.__extract = function () {
    const els = [];
    const all = document.querySelectorAll('*');
    for (const el of all) {
      if (SKIP_TAGS.has(el.tagName)) continue;
      if (!isVisible(el)) continue;
      const r = el.getBoundingClientRect();
      els.push({
        path: pathOf(el),
        tag: el.tagName.toLowerCase(),
        cls: (el.getAttribute('class') || '').slice(0, 200),
        role: el.getAttribute('role') || null,
        aria: el.getAttribute('aria-label') || null,
        // Placeholders are visible text to a user but are not innerText, so a
        // text-only extractor reports "element missing" for any input hint.
        placeholder: el.getAttribute('placeholder') || null,
        href: el.tagName === 'A' ? (el.getAttribute('href') || null) : null,
        text: ownText(el),
        box: {
          x: +r.x.toFixed(1), y: +r.y.toFixed(1),
          w: +r.width.toFixed(1), h: +r.height.toFixed(1),
        },
        style: styleOf(el),
      });
    }
    return {
      url: location.href,
      viewport: { w: window.innerWidth, h: window.innerHeight },
      scroll: { x: window.scrollX, y: window.scrollY },
      docHeight: document.documentElement.scrollHeight,
      count: els.length,
      theme: themeTokens(),
      elements: els,
    };
  };

  // Which element actually scrolls — GHL pins the document to 100vh and
  // scrolls an inner pane, so window.scrollTo() is a no-op on these views.
  // Stash the live elements so the driver can scroll the real one.
  window.__scrollerEls = [];
  window.__scrollers = function () {
    const out = [];
    window.__scrollerEls = [];
    for (const el of document.querySelectorAll('*')) {
      if (el.scrollHeight > el.clientHeight + 20 && el.clientHeight > 150) {
        const cs = getComputedStyle(el);
        if (cs.overflowY === 'auto' || cs.overflowY === 'scroll') {
          window.__scrollerEls.push(el);
          out.push({
            path: pathOf(el),
            cls: (el.getAttribute('class') || '').slice(0, 120),
            clientHeight: el.clientHeight,
            scrollHeight: el.scrollHeight,
            scrollable: el.scrollHeight - el.clientHeight,
          });
        }
      }
    }
    // Biggest scrollable distance first — that's the content pane, not the nav.
    const order = out.map((o, i) => i).sort((a, b) => out[b].scrollable - out[a].scrollable);
    window.__scrollerEls = order.map(i => window.__scrollerEls[i]);
    return order.map(i => out[i]);
  };

  // Horizontal scrollers. The Opportunities kanban scrolls sideways, and a
  // vertical-only capture silently misses every stage past the right edge —
  // measured: 5 stages captured summing to 23 opportunities, header said 25.
  window.__hscrollerEls = [];
  window.__hscrollers = function () {
    const out = [];
    window.__hscrollerEls = [];
    for (const el of document.querySelectorAll('*')) {
      if (el.scrollWidth > el.clientWidth + 20 && el.clientWidth > 200) {
        const cs = getComputedStyle(el);
        if (cs.overflowX === 'auto' || cs.overflowX === 'scroll') {
          window.__hscrollerEls.push(el);
          out.push({
            path: pathOf(el),
            cls: (el.getAttribute('class') || '').slice(0, 120),
            clientWidth: el.clientWidth,
            scrollWidth: el.scrollWidth,
            scrollable: el.scrollWidth - el.clientWidth,
          });
        }
      }
    }
    const order = out.map((o, i) => i).sort((a, b) => out[b].scrollable - out[a].scrollable);
    window.__hscrollerEls = order.map(i => window.__hscrollerEls[i]);
    return order.map(i => out[i]);
  };

  window.__scrollXTo = function (px) {
    window.__hscrollers();
    const el = window.__hscrollerEls[0];
    if (!el) return { target: 'none', scrollLeft: 0, max: 0 };
    el.scrollLeft = px;
    return {
      target: 'pane',
      scrollLeft: el.scrollLeft,
      max: el.scrollWidth - el.clientWidth,
    };
  };

  // Scroll whatever actually scrolls (content pane, else document) to a fraction.
  window.__scrollToFrac = function (f) {
    window.__scrollers();
    const el = window.__scrollerEls[0];
    if (el) {
      el.scrollTop = (el.scrollHeight - el.clientHeight) * f;
      return { target: 'pane', scrollTop: el.scrollTop };
    }
    const doc = document.documentElement;
    window.scrollTo(0, (doc.scrollHeight - window.innerHeight) * f);
    return { target: 'document', scrollTop: window.scrollY };
  };

  return true;
})();
