"""Page-context JavaScript that scores live DOM elements against a
learned constellation and returns the best match.

Injected before every `_cdp_eval` that touches an element. Installs
`window.__freuResolve(constellation)` (best match or null) and
`window.__freuResolveAll(constellation, limit)` (top-k above threshold).

Scoring weights and thresholds are tuned for "pages where selectors
rot" — high weights on id/attr/text signals, moderate weights on
class Jaccard and rect similarity, and low-but-bounded weights on
ancestor / neighbor / children / special signals.

Tie-break: earliest in DOM order.
"""

from __future__ import annotations

RESOLVE_JS = r"""
(function installFreuResolve(){
  if (window.__freuResolve && window.__freuResolveAll) return;

  const MIN_SCORE_ACT = 1.0;
  const MIN_SCORE_WAIT = 2.0;

  const SEMANTIC_ATTRS = new Set([
    "role","name","type","aria-label","data-testid",
    "data-action","href","placeholder","for",
  ]);

  function norm(s) {
    return (s == null ? "" : String(s)).replace(/\s+/g, " ").trim().toLowerCase();
  }

  // Glob match for the `*` wildcard the resolve/identify stages emit
  // when redacting build hashes (`Title-module__anchor__*`,
  // `_r_*_-list-view-node-_r_*_`). A pattern with no `*` is matched
  // literally; otherwise we split on `*` and scan for each segment in
  // order, anchoring the first / last segment to the string ends.
  function globMatch(pattern, candidate) {
    if (pattern == null || candidate == null) return false;
    const p = String(pattern);
    const c = String(candidate);
    if (p.indexOf("*") === -1) return p === c;
    const segs = p.split("*");
    let pos = 0;
    for (let i = 0; i < segs.length; i++) {
      const seg = segs[i];
      if (seg === "") continue;
      if (i === 0) {
        if (c.indexOf(seg) !== 0) return false;
        pos = seg.length;
      } else if (i === segs.length - 1) {
        if (c.length - seg.length < pos) return false;
        if (c.slice(c.length - seg.length) !== seg) return false;
      } else {
        const idx = c.indexOf(seg, pos);
        if (idx === -1) return false;
        pos = idx + seg.length;
      }
    }
    return true;
  }

  // Match the capture side's text extraction. innerText respects CSS
  // (skips script/style, honors block/inline boundaries with whitespace);
  // textContent does not, so capture ("home_work Real Property ...") and
  // scoring ("home_workReal Property ...") would diverge on minified DOMs.
  function elText(el) {
    if (!el) return "";
    let raw = "";
    try {
      raw = typeof el.innerText === "string" ? el.innerText : (el.textContent || "");
    } catch (_error) {
      raw = el.textContent || "";
    }
    return raw || "";
  }

  function visibleEl(el) {
    if (!el) return false;
    if (el.offsetWidth || el.offsetHeight) return true;
    if (typeof el.getClientRects === "function") {
      return el.getClientRects().length > 0;
    }
    return false;
  }

  function rect(el) {
    if (!el || typeof el.getBoundingClientRect !== "function") {
      return {left:0, top:0, right:0, bottom:0, width:0, height:0};
    }
    return el.getBoundingClientRect();
  }

  function classList(el) {
    if (!el || !el.className || typeof el.className !== "string") return [];
    return el.className.trim().split(/\s+/).filter(Boolean);
  }

  function attrVal(el, name) {
    return el && el.getAttribute ? el.getAttribute(name) : null;
  }

  function jaccard(a, b) {
    if (!a.length && !b.length) return 0;
    // Class names may carry a `*` wildcard from server-side hash
    // redaction (`Title-module__anchor__*`). Each learned class
    // counts as matched if ANY candidate class glob-matches it.
    let matched = 0;
    const candMatched = new Array(b.length).fill(false);
    for (let i = 0; i < a.length; i++) {
      const learned = a[i];
      for (let j = 0; j < b.length; j++) {
        if (candMatched[j]) continue;
        if (globMatch(learned, b[j])) {
          matched += 1;
          candMatched[j] = true;
          break;
        }
      }
    }
    const union = a.length + b.length - matched;
    return union ? matched / union : 0;
  }

  function textSim(learned, cand) {
    const a = norm(learned);
    const b = norm(cand);
    if (!a || !b) return 0;
    if (a.indexOf("*") !== -1) {
      const segs = a.split("*").filter(function(s) { return s.length > 0; });
      if (segs.length === 0) return 0;
      let pos = 0;
      for (let i = 0; i < segs.length; i++) {
        const idx = b.indexOf(segs[i], pos);
        if (idx === -1) return 0;
        pos = idx + segs[i].length;
      }
      return 1.5;
    }
    let s = 0;
    if (b.indexOf(a) !== -1) s += 1.5;
    if (a.indexOf(b) !== -1) s += 1.0;
    const maxLen = Math.max(a.length, b.length);
    if (maxLen > 0 && Math.abs(a.length - b.length) <= maxLen) {
      let matches = 0;
      const limit = Math.min(a.length, b.length);
      for (let i = 0; i < limit; i++) { if (a[i] === b[i]) matches += 1; }
      s += 0.5 * (matches / maxLen);
    }
    return s;
  }

  function attrScore(learnedAttrs, el) {
    if (!learnedAttrs) return 0;
    let s = 0;
    const keys = Object.keys(learnedAttrs);
    for (const k of keys) {
      const want = learnedAttrs[k];
      const got = attrVal(el, k);
      const weight = SEMANTIC_ATTRS.has(k) ? 1.0 : 0.3;
      if (got != null && globMatch(norm(want), norm(got))) {
        s += weight;
      } else if (got != null && got !== "") {
        s += weight * 0.4;
      }
    }
    return s;
  }

  function rectScore(learned, el) {
    if (learned.x == null || learned.y == null ||
        learned.w == null || learned.h == null) return 0;
    const r = rect(el);
    const ww = window.innerWidth || 1;
    const wh = window.innerHeight || 1;
    const wRef = Math.max(learned.w, r.width, 1);
    const hRef = Math.max(learned.h, r.height, 1);
    const cost = Math.abs(learned.x - r.left) / ww
               + Math.abs(learned.y - r.top) / wh
               + 2 * Math.abs(learned.w - r.width) / wRef
               + 2 * Math.abs(learned.h - r.height) / hRef;
    return Math.exp(-cost);
  }

  function scoreNode(learned, el) {
    if (!el || !learned) return 0;
    let s = 0;
    if (learned.id && globMatch(learned.id, el.id || "")) s += 3.0;
    s += attrScore(learned.attrs || {}, el);
    const lc = learned.classes || [];
    const ec = classList(el);
    if (lc.length || ec.length) s += 1.5 * jaccard(lc, ec);
    s += textSim(learned.text || "", elText(el));
    s += 1.0 * rectScore(learned, el);
    return s;
  }

  function ancestorScore(learnedAncestors, el) {
    // learnedAncestors is [root, ..., target]. The last entry IS the
    // target itself — `selector === ancestors[-1]` per the capture side.
    // When walking candidate's parent chain to score, we also start from
    // `el` itself (not el.parentElement) so index 0 on both sides refers
    // to the same logical node.
    const learned = (learnedAncestors || []).slice().reverse();
    const candChain = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && candChain.length < learned.length + 3) {
      candChain.push(cur);
      cur = cur.parentElement;
    }
    let total = 0;
    for (let k = 0; k < Math.min(learned.length, 6); k++) {
      let best = 0;
      for (let d = k; d < Math.min(k + 4, candChain.length); d++) {
        const ln = learned[k];
        const cn = candChain[d];
        if (!ln || !cn || !ln.tag) continue;
        if (cn.tagName.toLowerCase() !== ln.tag) continue;
        let local = 0.5 + 0.5 * (attrScore(ln.attrs || {}, cn) / 3);
        best = Math.max(best, local);
      }
      total += best / (k + 1);
    }
    return Math.min(total, 3.0);
  }

  function buildVisibilityGrid() {
    const all = (document.body || document.documentElement).getElementsByTagName("*");
    const buckets = new Map();
    for (let i = 0; i < all.length && i < 5000; i++) {
      const el = all[i];
      if (!visibleEl(el)) continue;
      const r = rect(el);
      if (r.width === 0 && r.height === 0) continue;
      const cx = Math.floor((r.left + r.width / 2) / 50);
      const cy = Math.floor((r.top + r.height / 2) / 50);
      const key = cx + ":" + cy;
      if (!buckets.has(key)) buckets.set(key, []);
      buckets.get(key).push(el);
    }
    return buckets;
  }

  let _grid = null;
  function grid() { if (!_grid) _grid = buildVisibilityGrid(); return _grid; }
  function resetGrid() { _grid = null; }

  function nearbyVisible(el) {
    const r = rect(el);
    const cx = Math.floor((r.left + r.width / 2) / 50);
    const cy = Math.floor((r.top + r.height / 2) / 50);
    const found = [];
    for (let dy = -1; dy <= 1; dy += 1) {
      for (let dx = -1; dx <= 1; dx += 1) {
        const key = (cx + dx) + ":" + (cy + dy);
        const bucket = grid().get(key);
        if (bucket) for (const e of bucket) if (e !== el) found.push(e);
      }
    }
    return found;
  }

  function neighborScore(learnedNeighbors, el) {
    const learned = learnedNeighbors || [];
    if (!learned.length) return 0;
    const candidates = nearbyVisible(el);
    let total = 0;
    for (const ln of learned) {
      let best = 0;
      for (const cn of candidates) {
        if (!ln.tag || cn.tagName.toLowerCase() !== ln.tag) continue;
        let local = 1;
        local += 0.5 * (attrScore(ln.attrs || {}, cn) / 2);
        if ((ln.classes || []).length || classList(cn).length) {
          local += 0.3 * jaccard(ln.classes || [], classList(cn));
        }
        local += 0.5 * Math.min(1, textSim(ln.text || "", elText(cn)));
        if (local > best) best = local;
      }
      total += 0.5 * best;
      if (total >= 4.0) break;
    }
    return Math.min(total, 4.0);
  }

  function childrenScore(learnedChildren, el) {
    if (!learnedChildren) return 0;
    const kids = el.children || [];
    if (kids.length === 0 || kids.length > 20) return 0;
    let total = 0;
    for (const lk of learnedChildren) {
      let best = 0;
      for (let i = 0; i < kids.length; i++) {
        const ck = kids[i];
        if (!lk.tag || ck.tagName.toLowerCase() !== lk.tag) continue;
        let local = 1;
        local += 0.5 * (attrScore(lk.attrs || {}, ck) / 2);
        local += 0.3 * jaccard(lk.classes || [], classList(ck));
        local += 0.3 * Math.min(1, textSim(lk.text || "", elText(ck)));
        if (local > best) best = local;
      }
      total += 0.6 * best;
      if (total >= 3.0) break;
    }
    return Math.min(total, 3.0);
  }

  function specialScore(learnedSpecial, el) {
    if (!learnedSpecial || !learnedSpecial.role) return 0;
    const role = learnedSpecial.role;
    let anchor = null;
    if (role === "label") {
      if (el.id && document.querySelector) {
        try { anchor = document.querySelector('label[for="' + (window.CSS ? CSS.escape(el.id) : el.id) + '"]'); }
        catch (_) { anchor = null; }
      }
      if (!anchor) {
        let p = el.parentElement;
        while (p) {
          if (p.tagName && p.tagName.toLowerCase() === "label") { anchor = p; break; }
          p = p.parentElement;
        }
      }
    } else if (role === "select" || role === "list" || role === "table") {
      const wanted = role === "list"
        ? (cur) => cur.tagName === "UL" || cur.tagName === "OL"
        : (cur) => cur.tagName.toLowerCase() === (role === "select" ? "select" : "table");
      let p = el.parentElement;
      while (p) {
        if (wanted(p)) { anchor = p; break; }
        p = p.parentElement;
      }
    }
    if (!anchor) return 0;
    let s = 0;
    s += 0.5 * Math.min(1, textSim(learnedSpecial.text || "", elText(anchor)));
    s += 0.5 * (attrScore(learnedSpecial.attrs || {}, anchor) / 2);
    s += 0.3 * jaccard(learnedSpecial.classes || [], classList(anchor));
    return s > 0.3 ? 1.5 : 0;
  }

  function scoreCandidate(c, el) {
    resetGrid(); // grid is cache-per-call; reset cheaply
    let s = scoreNode(c, el);
    s += 0.8 * ancestorScore(c.ancestors || [], el);
    s += neighborScore(c.neighbors || [], el);
    s += childrenScore(c.children, el);
    s += specialScore(c.special, el);
    return s;
  }

  function scoreAll(constellation) {
    if (!constellation || !constellation.tag) return [];
    resetGrid();
    const matches = document.getElementsByTagName(constellation.tag);
    const scored = [];
    for (let i = 0; i < matches.length; i++) {
      const el = matches[i];
      const s = scoreCandidate(constellation, el);
      scored.push({ el, score: s, index: i });
    }
    scored.sort((a, b) => b.score - a.score || a.index - b.index);
    return scored;
  }

  window.__freuResolve = function(constellation, opts) {
    const minScore = (opts && typeof opts.minScore === "number") ? opts.minScore : MIN_SCORE_ACT;
    const scored = scoreAll(constellation);
    if (!scored.length || scored[0].score < minScore) return null;
    return scored[0].el;
  };

  window.__freuResolveAll = function(constellation, opts) {
    const minScore = (opts && typeof opts.minScore === "number") ? opts.minScore : MIN_SCORE_ACT;
    const limit = (opts && typeof opts.limit === "number") ? opts.limit : 64;
    const scored = scoreAll(constellation);
    const out = [];
    for (const s of scored) {
      if (s.score < minScore) break;
      out.push(s.el);
      if (out.length >= limit) break;
    }
    return out;
  };

  window.__freuResolveConfig = { MIN_SCORE_ACT, MIN_SCORE_WAIT };
})();
"""
