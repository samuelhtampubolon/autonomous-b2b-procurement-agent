/**
 * Minimal, dependency-free SVG charts for the showcase.
 *
 * Built with DOM APIs only (no innerHTML), so the page can run under a Trusted Types
 * CSP. Every chart has a hover tooltip, direct value labels, a text summary for screen
 * readers, and re-renders on resize.
 */
(function (root) {
  "use strict";

  const PA = (root.PA = root.PA || {});
  const NS = "http://www.w3.org/2000/svg";

  function svgEl(tag, attrs, parent) {
    const node = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, String(v));
    if (parent) parent.appendChild(node);
    return node;
  }

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
  }

  // ------------------------------------------------------------------ tooltip
  const tip = () => document.getElementById("chart-tip");
  function showTip(evt, title, value) {
    const t = tip();
    if (!t) return;
    const strong = document.createElement("strong");
    strong.textContent = title;
    const span = document.createElement("span");
    span.textContent = value;
    t.replaceChildren(strong, span);
    t.hidden = false;
    const pad = 14;
    const rect = t.getBoundingClientRect();
    let x = evt.clientX + pad;
    let y = evt.clientY + pad;
    if (x + rect.width > window.innerWidth - 8) x = evt.clientX - rect.width - pad;
    if (y + rect.height > window.innerHeight - 8) y = evt.clientY - rect.height - pad;
    t.style.setProperty("left", x + "px");
    t.style.setProperty("top", y + "px");
  }
  function hideTip() {
    const t = tip();
    if (t) t.hidden = true;
  }

  function bindHover(container, hitEl, markEl, title, value) {
    hitEl.addEventListener("pointermove", (e) => {
      container.classList.add("dim");
      markEl.classList.add("on");
      showTip(e, title, value);
    });
    hitEl.addEventListener("pointerleave", () => {
      container.classList.remove("dim");
      markEl.classList.remove("on");
      hideTip();
    });
  }

  /** Horizontal bar path with 4px rounded data end, square at the baseline. */
  function hbarPath(x, y, w, h, r) {
    if (w <= 0) return "";
    const rr = Math.min(r, w, h / 2);
    return `M${x},${y} H${x + w - rr} Q${x + w},${y} ${x + w},${y + rr} V${y + h - rr} Q${x + w},${y + h} ${x + w - rr},${y + h} H${x} Z`;
  }

  // Re-render registered charts when their container width changes.
  const renderers = new Map();
  const ro =
    typeof ResizeObserver !== "undefined"
      ? new ResizeObserver((entries) => {
          for (const entry of entries) {
            const fn = renderers.get(entry.target);
            if (fn && Math.abs((entry.target._lastW || 0) - entry.contentRect.width) > 2) fn();
          }
        })
      : null;
  function register(container, fn) {
    if (!renderers.has(container) && ro) ro.observe(container);
    renderers.set(container, fn);
    fn();
  }

  function legend(items) {
    const ul = document.createElement("ul");
    ul.className = "legend";
    items.forEach((it) => {
      const li = document.createElement("li");
      const sw = document.createElement("span");
      sw.className = "sw";
      sw.style.setProperty("background", it.color);
      const label = document.createElement("span");
      label.textContent = it.label;
      li.append(sw, label);
      ul.appendChild(li);
    });
    return ul;
  }

  // ------------------------------------------------------------------ bar list
  /**
   * rows: [{ label, value, colorVar, display }]
   */
  function barList(container, rows, opts) {
    const options = Object.assign({ barH: 18, gap: 14, labelW: 170, format: (v) => String(v) }, opts || {});
    register(container, () => {
      const width = Math.max(260, container.clientWidth);
      container._lastW = width;
      const narrow = width < 480;
      const labelW = narrow ? 0 : options.labelW;
      const rowH = options.barH + (narrow ? 20 : 0);
      const valueW = 92;
      const plotW = width - labelW - valueW;
      const height = rows.length * (rowH + options.gap);
      const max = Math.max(1, ...rows.map((r) => Math.abs(r.value)));

      const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, role: "img" });
      svg.setAttribute(
        "aria-label",
        rows.map((r) => `${r.label}: ${r.display || options.format(r.value)}`).join("; ")
      );
      svgEl("line", { x1: labelW, x2: labelW, y1: 0, y2: height - options.gap + 4, class: "baseline" }, svg);

      rows.forEach((r, i) => {
        const top = i * (rowH + options.gap);
        const barY = top + (narrow ? 20 : 0);
        const w = (Math.abs(r.value) / max) * (plotW - 8);
        const text = r.display || options.format(r.value);
        const label = svgEl("text", { x: narrow ? 0 : labelW - 10, y: narrow ? top + 13 : barY + options.barH / 2 + 4, "text-anchor": narrow ? "start" : "end" }, svg);
        label.textContent = r.label;
        const mark = svgEl("path", { d: hbarPath(labelW, barY, Math.max(w, 2), options.barH, 4), fill: cssVar(r.colorVar), class: "mark" }, svg);
        const val = svgEl("text", { x: labelW + Math.max(w, 2) + 8, y: barY + options.barH / 2 + 4, class: "val" }, svg);
        val.textContent = text;
        const hit = svgEl("rect", { x: 0, y: top - options.gap / 2, width, height: rowH + options.gap, class: "hit" }, svg);
        bindHover(container, hit, mark, r.label, text);
      });
      container.replaceChildren(svg);
    });
  }

  // ------------------------------------------------------------------ stacked 100% bar
  /** segments: [{ label, value, colorVar }] */
  function stackedBar(container, segments, opts) {
    const options = Object.assign({ height: 34, format: (v) => String(v) }, opts || {});
    register(container, () => {
      const width = Math.max(260, container.clientWidth);
      container._lastW = width;
      const total = segments.reduce((s, x) => s + x.value, 0) || 1;
      const gap = 2;
      const visible = segments.filter((s) => s.value > 0);
      const usable = width - gap * (visible.length - 1);
      const h = options.height;
      const svg = svgEl("svg", { viewBox: `0 0 ${width} ${h + 26}`, role: "img" });
      svg.setAttribute(
        "aria-label",
        segments.map((s) => `${s.label}: ${s.value} (${Math.round((s.value / total) * 100)}%)`).join("; ")
      );

      let x = 0;
      visible.forEach((s, i) => {
        const w = (s.value / total) * usable;
        const isFirst = i === 0;
        const isLast = i === visible.length - 1;
        const r = 6;
        const d = [
          `M${x + (isFirst ? r : 0)},0`,
          `H${x + w - (isLast ? r : 0)}`,
          isLast ? `Q${x + w},0 ${x + w},${r} V${h - r} Q${x + w},${h} ${x + w - r},${h}` : `V${h}`,
          `H${x + (isFirst ? r : 0)}`,
          isFirst ? `Q${x},${h} ${x},${h - r} V${r} Q${x},0 ${x + r},0` : `V0`,
          "Z",
        ].join(" ");
        const mark = svgEl("path", { d, fill: cssVar(s.colorVar), class: "mark" }, svg);
        const pct = Math.round((s.value / total) * 100);
        if (w > 34) {
          const t = svgEl("text", { x: x + w / 2, y: h + 18, "text-anchor": "middle", class: "val" }, svg);
          t.textContent = `${pct}%`;
        }
        const hit = svgEl("rect", { x, y: 0, width: w, height: h + 22, class: "hit" }, svg);
        bindHover(container, hit, mark, s.label, `${options.format(s.value)} (${pct}%)`);
        x += w + gap;
      });
      container.replaceChildren(svg, legend(segments.map((s) => ({ label: `${s.label} (${s.value})`, color: cssVar(s.colorVar) }))));
    });
  }

  // ------------------------------------------------------------------ line / area
  /** points: [{ label, y }] ; one series with crosshair tooltip. */
  function lineChart(container, points, opts) {
    const options = Object.assign({ height: 200, colorVar: "--series-1", format: (v) => String(v), tickFormat: (v) => String(v) }, opts || {});
    register(container, () => {
      const width = Math.max(280, container.clientWidth);
      container._lastW = width;
      const m = { t: 10, r: 14, b: 26, l: 58 };
      const pw = width - m.l - m.r;
      const ph = options.height - m.t - m.b;
      const maxY = Math.max(1, ...points.map((p) => p.y));
      const niceMax = niceCeil(maxY);
      const xAt = (i) => m.l + (points.length === 1 ? pw / 2 : (i / (points.length - 1)) * pw);
      const yAt = (v) => m.t + ph - (v / niceMax) * ph;
      const color = cssVar(options.colorVar);

      const svg = svgEl("svg", { viewBox: `0 0 ${width} ${options.height}`, role: "img" });
      svg.setAttribute("aria-label", points.map((p) => `${p.label}: ${options.format(p.y)}`).join("; "));

      for (let k = 0; k <= 4; k++) {
        const v = (niceMax / 4) * k;
        const y = yAt(v);
        svgEl("line", { x1: m.l, x2: width - m.r, y1: y, y2: y, class: k === 0 ? "baseline" : "gridline" }, svg);
        const t = svgEl("text", { x: m.l - 8, y: y + 4, "text-anchor": "end", class: "axis-label" }, svg);
        t.textContent = options.tickFormat(v);
      }
      points.forEach((p, i) => {
        if (points.length > 8 && i % 2 === 1 && i !== points.length - 1) return;
        const t = svgEl("text", { x: xAt(i), y: options.height - 6, "text-anchor": "middle", class: "axis-label" }, svg);
        t.textContent = p.label;
      });

      const linePts = points.map((p, i) => `${xAt(i)},${yAt(p.y)}`);
      const gradId = "g" + Math.random().toString(36).slice(2, 8);
      const defs = svgEl("defs", {}, svg);
      const grad = svgEl("linearGradient", { id: gradId, x1: 0, x2: 0, y1: 0, y2: 1 }, defs);
      svgEl("stop", { offset: "0%", "stop-color": color, "stop-opacity": 0.28 }, grad);
      svgEl("stop", { offset: "100%", "stop-color": color, "stop-opacity": 0 }, grad);
      svgEl("path", { d: `M${linePts[0]} L${linePts.join(" L")} L${xAt(points.length - 1)},${yAt(0)} L${xAt(0)},${yAt(0)} Z`, fill: `url(#${gradId})` }, svg);
      svgEl("polyline", { points: linePts.join(" "), fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);

      const last = points[points.length - 1];
      svgEl("circle", { cx: xAt(points.length - 1), cy: yAt(last.y), r: 4, fill: color, stroke: cssVar("--surface-solid"), "stroke-width": 2 }, svg);
      const endLabel = svgEl("text", { x: xAt(points.length - 1) - 6, y: yAt(last.y) - 10, "text-anchor": "end", class: "val" }, svg);
      endLabel.textContent = options.format(last.y);

      const cross = svgEl("line", { x1: 0, x2: 0, y1: m.t, y2: m.t + ph, stroke: cssVar("--axis"), "stroke-dasharray": "3 3", visibility: "hidden" }, svg);
      const dot = svgEl("circle", { r: 5, fill: color, stroke: cssVar("--surface-solid"), "stroke-width": 2, visibility: "hidden" }, svg);
      const hit = svgEl("rect", { x: m.l, y: m.t, width: pw, height: ph, class: "hit" }, svg);
      hit.addEventListener("pointermove", (e) => {
        const box = svg.getBoundingClientRect();
        const scale = width / box.width;
        const px = (e.clientX - box.left) * scale;
        const i = Math.max(0, Math.min(points.length - 1, Math.round(((px - m.l) / pw) * (points.length - 1))));
        cross.setAttribute("x1", xAt(i));
        cross.setAttribute("x2", xAt(i));
        cross.setAttribute("visibility", "visible");
        dot.setAttribute("cx", xAt(i));
        dot.setAttribute("cy", yAt(points[i].y));
        dot.setAttribute("visibility", "visible");
        showTip(e, points[i].label, options.format(points[i].y));
      });
      hit.addEventListener("pointerleave", () => {
        cross.setAttribute("visibility", "hidden");
        dot.setAttribute("visibility", "hidden");
        hideTip();
      });
      container.replaceChildren(svg);
    });
  }

  function niceCeil(v) {
    const exp = Math.pow(10, Math.floor(Math.log10(v)));
    const f = v / exp;
    const nice = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
    return nice * exp;
  }

  function rerenderAll() {
    renderers.forEach((fn) => fn());
  }

  PA.charts = { barList, stackedBar, lineChart, rerenderAll };
})(window);
