import { createNotes } from "/notes.js";
const $ = (id) => document.getElementById(id);
const node = (tag, text, cls) => {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (cls) n.className = cls;
  return n;
};
const fmt = (v, d = 1) =>
  Number(v).toLocaleString(undefined, {
    maximumFractionDigits: d,
    minimumFractionDigits: d,
  });
let portfolio,
  currentRound = 0,
  currentRounds = {},
  enabled = new Set(),
  current,
  view = { zoom: 1, x: 0.5, y: 0.5 },
  images = {},
  blinkTimer,
  flash = false,
  requestVersion = 0;
const imageCache = new Map();
const notes = createNotes({
  onCounts: () => {
    if (portfolio) render();
  },
  getView: () => view,
  restoreView: (saved) => {
    view = { ...saved };
    $("zoom").value = String(view.zoom);
    drawBoth();
  },
});
function commentButton(row, id) {
  const count = notes.count(row.id, id);
  const button = node(
    "button",
    count ? `${count} comment${count === 1 ? "" : "s"}` : "Comment",
    "cell-comments",
  );
  button.setAttribute(
    "aria-label",
    `Comment on ${row.title}: ${id === "source" ? "Original" : experiment(id).name}`,
  );
  button.onclick = async () => {
    await openComparison(row.id, id);
    notes.focus();
  };
  return button;
}
const experiment = (id) => portfolio.experiments.find((e) => e.id === id);
const cell = (row, id) => portfolio.cells[row]?.[id];
const artifact = (row, id, file) =>
  `/artifacts/${cell(row, id).artifact}/${file}`;
const imageURL = (row, id) =>
  id === "source"
    ? `/artifacts/${row}/source.png`
    : artifact(row, id, "preview.png");
const penWidth = (width) => width.toFixed(width < 0.1 ? 3 : 2);
function parameters(e) {
  if (e.study === 'palette')
    return `${e.palette_mapping === 'swatch' ? 'Swatch-aware' : 'Ideal CMYK'} · ${Object.keys(e.ink_colors).length} inks · ${penWidth(e.pen_mm)} mm · 11 × 17″`;
  if (e.paper)
    return `${penWidth(e.pen_mm)} mm pen · ${e.paper.width_in} × ${e.paper.height_in}″ sheet · ${e.paper.margin_in.toFixed(2)}″ margins`;
  if (e.join_fill)
    return `Same fill · joins within existing ink · ${penWidth(e.pen_mm)} mm pen`;
  if (e.use_preferred_recovery && e.method === "stroke_study")
    return e.use_profile_pen
      ? "Selected recovery and pen width from your feedback"
      : "Per-image recovery from your feedback · 0.30 mm pen";
  if (e.method === "stroke_study")
    return `${penWidth(e.pen_mm)} mm pen · contrast ≥ ${e.ridge_contrast}${e.ridge_seed_contrast ? ` · seed ≥ ${e.ridge_seed_contrast}` : ""}`;
  if (e.screen_carrier === "highlight-diffusion")
    return `Light tones only · ${e.highlight_run_mm.toFixed(1)} mm packets · error diffusion`;
  if (e.method === "screen_study" && e.round >= 5)
    return `Full AM/FM · cutoff ${(e.white_cutoff * 100).toFixed(1)}% · selected K`;
  if (e.method === "screen_study")
    return `Peak ${Math.round(e.density * 100)}% · interior ${Math.round(e.interior_ink * 100)}%${e.edge_fade_mm ? ` · fade ${(e.edge_fade_mm / 25.4).toFixed(2)}″` : ""}`;
  if (e.fit_tolerance_mm)
    return `Fit ${e.fit_tolerance_mm.toFixed(2)} mm · ${e.recover_weight ? "restore stroke weight" : "smooth centerlines"} · ${penWidth(e.pen_mm)} mm pen`;
  if (e.method === "component_study")
    return `Pixels < ${e.threshold}/255 · ${penWidth(e.pen_mm)} mm pen${e.lettering_upsample === 2 ? " · 2× cubic" : ""}`;
  if (e.method === "ridges") return "Narrow dark strokes · two-sided contrast";
  if (e.method === "edge_wave")
    return `${e.edge_fade_mm ? `Fade ${(e.edge_fade_mm / 25.4).toFixed(2)}″ per side` : "Full-image color"} · ${e.recover_ink ? "CMY + recovered K" : "CMY only"}`;
  if (e.method === "wave")
    return `${e.modulation.toUpperCase()} · ${e.shape} · ${e.row_spacing_mm} mm rows · demand × ${e.density}`;
  if (e.method === "centerline")
    return `Cutoff ${e.threshold}/255 · fixed-width centerlines`;
  if (e.method === "edges")
    return `Blur ${e.sigma}px · thresholds ${e.low}/${e.high}`;
  return `${e.palette.toUpperCase()} · coverage demand × ${e.density}${e.method === "hybrid" ? ` · cutoff ${e.threshold}` : ""}`;
}
function currentExperiment(e) {
  const selected = portfolio.study_columns?.[e.study];
  if (selected) return selected.includes(e.id);
  return Boolean(e.study) && (currentRound ? e.round === currentRounds[e.study] : true);
}
function familyMatch(e) {
  const f = $("family").value;
  return (
    (currentExperiment(e) && e.study === f) ||
    (!currentRound && f === "strokes" && e.id === "ridge-ink")
  );
}
function inkArea(m) {
  return m.ink_area_ratio * m.width_in * m.height_in;
}
function penLabel(m) {
  if (!m.pen_mm_by_ink) return `${penWidth(m.pen_mm)} mm pen`;
  return Object.keys(m.by_ink)
    .map((ink) => `${ink} ${penWidth(m.pen_mm_by_ink[ink] ?? m.pen_mm)} mm`)
    .join(" · ");
}
function inkIndex(row, id) {
  const result = cell(row, id),
    e = experiment(id);
  if (e.paper) return ""; // Different art sizes do not share the old ink index.
  const referenceId =
    e.ink_reference ||
    (e.study === "screens"
      ? "screen-full"
      : e.study === "strokes" || id === "ridge-ink"
        ? "ridge-ink"
        : null);
  const reference = referenceId && cell(row, referenceId);
  if (!reference || !inkArea(reference.metrics)) return "";
  const index = (100 * inkArea(result.metrics)) / inkArea(reference.metrics);
  return `Ink ${fmt(index, 0)} · ${e.study === "screens" ? "full screen" : "recovery"} = 100`;
}
function render() {
  const query = $("search").value.toLowerCase();
  const matchingRows = portfolio.images.filter((i) =>
    (i.title + " " + i.artist).toLowerCase().includes(query),
  );
  const rows = matchingRows.filter(
    (i) =>
      ($("family").value !== "screens" || i.screen_enabled !== false) &&
      ($("family").value !== "lettering" || i.lettering_enabled),
  );
  const columns = portfolio.experiments.filter(
    (e) => enabled.has(e.id) && familyMatch(e),
  );
  const order = portfolio.study_columns?.[$("family").value];
  if (order) columns.sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
  renderTable(rows, columns, "matrix");
  const archived = portfolio.experiments.filter((e) => !currentExperiment(e));
  $("archive-count").textContent = `(${archived.length} columns)`;
  if ($("archive").open)
    renderTable(
      matchingRows,
      archived.filter((e) => enabled.has(e.id)),
      "archive-matrix",
    );
  else
    $("archive-matrix")
      .querySelector("table")
      .replaceChildren(node("thead"), node("tbody"));
  const descriptions = {
    palette: 'Same 0.25 mm test pens, 11 × 17″ sheet, 0.75″ margins and full interior shading; black stroke recovery is off. The corrected Oklab mapper avoids the previous blue-to-purple shift. Earlier CIELAB results and comments are under Earlier experiments. The last column adds recorded Red and provisional Blue (#245bd6). No scanner calibration; this predicts the illustrative preview, not exact physical ink. Click a sample to compare, zoom and comment.',
    combined: "Review: joined fill, including the neutral mask, was rejected for contrast enhancement of colored screened art. Keep earlier line recovery for that purpose; retain joined fill as an explicit choice for monochrome line-art comics. All four comparisons remain available. Every test pen is 0.25 mm on 11 × 17″ paper with 0.75″ minimum margins. C uses recorded Turquoise #009f7c; M/Y colors remain provisional.",
    strokes: "Selected recovery is retained while we investigate highlight screening and The Visitor’s small lettering. Earlier sensitivity comparisons are folded below.",
    screens: "Full AM/FM is the reference. A lower-cutoff control isolates the near-white cutoff change (4% → 0.4%). Highlight diffusion then spends ink error along light rows in short packets, accounting for round caps. Demand at or above one straight carrier keeps continuous waves. Compare screen-path counts as well as mileage; Game Night stays K-only.",
    lettering: "Joined fill is your current leader. The new 0.25 mm comparison regenerates it to fit 11 × 17″ paper with 0.75″ margins; The Visitor’s art is 9.5 × 12.3″. Other columns retain the 0.075 mm diagnostic pen and 8″ longest edge. The source mask and 450 dpi art preview are unchanged in method. Compare at matching image zoom; use Paper preview to see placement on the sheet.",
  };
  if (!portfolio.study_columns?.lettering)
    descriptions.lettering = "Your review: ink-shape fill is most legible. Guo–Hall and medial axis improve readability slightly, but their excessive wiggliness makes the tradeoff unattractive. These are the unchanged thinning comparisons.";
  $("study-description").textContent = descriptions[$("family").value];
  $("tone-strip").hidden = $("family").value !== "screens" || currentRound < 5;
  $("lettering-stages").hidden = $("family").value !== "lettering";
  $("visible-count").textContent =
    `${rows.length} challenges × ${columns.length} active experiments · earlier work is folded below`;
}
function renderTable(rows, columns, target) {
  const table = $(target).querySelector("table");
  const head = table.querySelector("thead");
  head.replaceChildren();
  const tr = node("tr");
  const sourceHead = node("th");
  sourceHead.append(
    node("span", "Challenge / original", "name"),
    node("span", "Preserved source · artist credited", "settings"),
  );
  tr.append(sourceHead);
  for (const e of columns) {
    const th = node("th");
    th.append(
      node("span", e.name, "name"),
      node("span", parameters(e), "settings"),
    );
    th.title = e.description;
    tr.append(th);
  }
  head.append(tr);
  const body = table.querySelector("tbody");
  body.replaceChildren();
  for (const row of rows) {
    const tr = node("tr");
    tr.dataset.challenge = row.id;
    const source = node("td");
    source.className = "cell";
    source.append(
      imageButton(row, "source", `/artifacts/${row.id}/source-thumb.png`),
      node("div", row.title, "source-title"),
      node("div", `by ${row.artist}`, "artist"),
    );
    const meta = node("div", undefined, "source-meta");
    meta.append(node("span", `${row.pixels[0]} × ${row.pixels[1]} px`));
    meta.append(
      node(
        "span",
        row.screen_palette === "black"
          ? "Monochrome · K screening"
          : ($('family').value === 'palette' ? 'Color · selected-ink screening' : "Color · CMY + K screening"),
      ),
    );
    if (row.recovery_reference && $('family').value !== 'palette')
      meta.append(
        node(
          "span",
          `Current K reference: ${experiment(row.recovery_reference)?.name || row.recovery_reference}`,
        ),
      );
    const link = node("a", "Source image");
    link.href = "/originals/" + row.file;
    link.download = row.file;
    meta.append(link);
    source.append(meta, commentButton(row, "source"));
    tr.append(source);
    for (const e of columns) {
      const td = node("td");
      td.className = "cell";
      const result = cell(row.id, e.id);
      if (result) {
        const m = result.metrics;
        td.append(imageButton(row, e.id, artifact(row.id, e.id, "thumb.png")));
        const stats = node("div", undefined, "metrics");
        stats.append(
          node("span", `${fmt(m.meters)} m`, "meters"),
          node("span", `${fmt(m.paths, 0)} paths`),
        );
        td.append(
          stats,
          node(
            "div",
            `${fmt(m.coverage * 100, 0)}% preview coverage · ${penLabel(m)}`,
            "secondary",
          ),
        );
        const budget = inkIndex(row.id, e.id);
        if (e.paper)
          td.append(node("div", `${fmt(m.width_in)} × ${fmt(m.height_in)}″ art · centered on paper`, "secondary"));
        if (e.study === 'palette')
          td.append(node('div', `Coarse preview RGB error ${fmt(100*m.blurred_rgb_mae, 1)}%`, 'secondary'));
        const score = node(
          "div",
          budget ||
            `${fmt(inkArea(m))} in² ink proxy`,
          "ink-budget",
        );
        score.title =
          "Pen width × total path distance; overlaps and multiple inks count. An ink-use proxy, not calibrated ink volume.";
        td.append(score);
        if (e.study === "screens" && e.round >= 5) {
          const inkPaths = cell(row.id, "review-ink")?.metrics.paths || 0;
          const screenPaths = Math.max(0, m.paths - inkPaths);
          const baseline = cell(row.id, "highlight-reference");
          const referencePaths = Math.max(0, (baseline?.metrics.paths || 0) - inkPaths);
          const label = node("div", `Screen paths ${fmt(screenPaths, 0)}${referencePaths ? ` · ${fmt(screenPaths / referencePaths)}× reference` : ""}`, "secondary");
          label.title = "Shading paths only, excluding recovered ink. More separate runs mean more pen lifts before travel optimization.";
          td.append(label);
        }
        td.append(commentButton(row, e.id));
      } else td.append(node("div", "Not generated yet", "pending"));
      tr.append(td);
    }
    body.append(tr);
  }
}
function imageButton(row, id, url) {
  const button = node("button", undefined, "image-button");
  button.setAttribute(
    "aria-label",
    `Compare ${row.title}: ${id === "source" ? "Original" : experiment(id).name}`,
  );
  const img = node("img");
  img.src = url;
  img.loading = "lazy";
  img.alt = `${row.title} — ${id === "source" ? "original" : experiment(id).name}`;
  button.append(img);
  button.onclick = () => openComparison(row.id, id);
  return button;
}
function fillSelect(select, withSource = true) {
  select.replaceChildren();
  if (withSource) {
    const opt = node("option", "Original");
    opt.value = "source";
    select.append(opt);
  }
  for (const [label, choices] of [
    ["Palette-aware color", portfolio.experiments.filter(e=>e.study==='palette')],
    [
      "Current stroke study",
      portfolio.experiments.filter(
        (e) => currentExperiment(e) && e.study === "strokes",
      ),
    ],
    [
      "Current shading study",
      portfolio.experiments.filter(
        (e) => currentExperiment(e) && e.study === "screens",
      ),
    ],
    [
      "Current lettering study",
      portfolio.experiments.filter(
        (e) => currentExperiment(e) && e.study === "lettering",
      ),
    ],
    [
      "Combined ink and screening",
      portfolio.experiments.filter((e) => currentExperiment(e) && e.study === "combined"),
    ],
    [
      "Earlier experiments",
      portfolio.experiments.filter((e) => !currentExperiment(e)),
    ],
  ]) {
    const group = node("optgroup");
    group.label = label;
    for (const e of choices) {
      const opt = node("option", e.name);
      opt.value = e.id;
      group.append(opt);
    }
    if (choices.length) select.append(group);
  }
}
function loadImage(url) {
  if (!imageCache.has(url)) {
    imageCache.set(
      url,
      new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () => {
          imageCache.delete(url);
          reject(Error("Could not load comparison image"));
        };
        img.src = url;
      }),
    );
  }
  return imageCache.get(url);
}
async function refreshComparison() {
  const version = ++requestVersion;
  notes.setLoading(true);
  current = $("challenge-select").value;
  const row = portfolio.images.find((i) => i.id === current);
  $("detail-view").hidden = !row.detail_view;
  $("compare-title").textContent = `${row.title} · ${row.artist}`;
  const left = $("left-select").value,
    right = $("right-select").value;
  try {
    const loaded = await Promise.all([
      loadImage(imageURL(current, left)),
      loadImage(imageURL(current, right)),
      loadImage(imageURL(current, "source")),
    ]);
    if (version !== requestVersion) return;
    images = { left: loaded[0], right: loaded[1], source: loaded[2] };
    for (const [side, id] of [
      ["left", left],
      ["right", right],
    ]) {
      const info = $(side + "-info");
      info.replaceChildren();
      if (id === "source") {
        info.append(
          node(
            "div",
            `${row.pixels.join(" × ")} source pixels · SHA-256 ${row.sha256.slice(0, 12)}…`,
          ),
        );
        const a = node("a", "Download original");
        a.href = "/originals/" + row.file;
        a.download = row.file;
        info.append(a);
      } else {
        const result = cell(current, id),
          m = result.metrics,
          e = experiment(id);
        info.append(
          node(
            "div",
            `${fmt(m.meters)} m · ${fmt(m.paths, 0)} paths · ${fmt(m.width_in)}″ × ${fmt(m.height_in)}″ · ${penLabel(m)}`,
          ),
        );
        const proxy = inkArea(m);
        if (result.combined) {
          const c = result.combined;
          info.append(node("div", `${c.recovered_paths.toLocaleString()} recovered ink paths · ${Object.values(c.screen_paths_by_ink).reduce((a,b)=>a+b,0).toLocaleString()} screen paths · ${e.study === 'palette' ? Object.keys(e.ink_colors).length+' selected inks' : c.screen_palette.toUpperCase()+' screening'}`));
          if (e.study === 'palette') {
            info.append(node('div', '0.25 mm test widths · swatches only, no scanner calibration. Blue is provisional.'));
            for (const [ink, color] of Object.entries(e.ink_colors))
              info.append(node('div', `${e.materials[ink].label} ${color} · ${fmt(m.by_ink[ink]?.meters || 0)} m`));
            info.append(node('div', `Coarse preview RGB error: ${fmt(m.blurred_rgb_mae*100, 1)}% after a 1 mm blur. Describes this simulated preview, not measured ink or a pass/fail gate.`));
          } else {
            info.append(node("div", "0.25 mm test widths · C → Turquoise #009f7c · M/Y colors provisional; physical pens not yet tested."));
          }
          if (["luminance", "neutral"].includes(c.black_policy)) {
            const a = node("a", "Recovered ink mask");
            a.href = artifact(current,id,"ink-mask.png");
            a.target = "_blank"; a.rel = "noopener";
            info.append(a);
          }
        }
        if (result.paper_layout) {
          const p = result.paper_layout;
          info.append(node("div", `Paper: ${fmt(p.width_in)} × ${fmt(p.height_in)}″ · art: ${fmt(m.width_in)} × ${fmt(m.height_in)}″ · minimum margins ${fmt(p.margin_in, 2)}″`));
          info.append(node("div", `Centered margins: left/right ${fmt(p.offset_x_in, 2)}″ · top/bottom ${fmt(p.offset_y_in, 2)}″. Comparison panels align the artwork, not the sheet edges.`));
          for (const [label, file] of [["Paper preview", "paper-preview.png"], ["Paper SVG (actual size)", "paper.svg"]]) {
            const link = node("a", label);
            link.href = artifact(current, id, file);
            link.target = "_blank";
            link.rel = "noopener";
            info.append(link);
          }
        }
        const budget = inkIndex(current, id);
        info.append(
          node(
            "div",
            `${budget ? budget + " · " : ""}${fmt(proxy)} in² pen-width × distance (overlaps count)`,
            "ink-budget",
          ),
        );
        if (e.method === "screen_study")
          info.append(
            node(
              "div",
              result.signature.recipe.palette === "black"
                ? "K only: recovered strokes + monochrome screen"
                : "CMY screen + recovered black strokes",
            ),
          );
        if (result.signature.recipe.recovery_profile_name)
          info.append(
            node(
              "div",
              `Black recovery: ${result.signature.recipe.recovery_profile_name}`,
            ),
          );
        if (["stroke_study", "component_study"].includes(e.method) && result.analysis_pixels)
          info.append(
            node(
              "div",
              `Source analysis: ${result.analysis_pixels.join(" × ")} pixels from the supplied image.`,
            ),
          );
        for (const [name, file] of [
          ["Vector SVG", "paths.svg"],
          ["Pen paths", "paths.json"],
          ["Recipe & metrics", "result.json"],
        ]) {
          const a = node("a", name);
          a.href = artifact(current, id, file);
          a.download = `${current}-${id}-${file}`;
          info.append(a);
        }
        if (result.lettering) {
          if (result.lettering.tracing_pixels)
            info.append(node("div", `Tracing: ${result.lettering.tracing_pixels.join(" × ")} pixels · ${result.lettering.interpolation} · ${result.preview_dpi} dpi preview`));
          const stages = [["Threshold mask", "threshold-mask.png"]];
          if (result.lettering.skeleton_used !== false)
            stages.push([result.lettering.skeleton_role === "before path fitting" ? "Skeleton before fitting" : "Accepted skeleton", "accepted-skeleton.png"]);
          else info.append(node("div", "Dense ink-shape fill · no skeleton"));
          const joining = result.lettering.fill_joining;
          if (joining) {
            info.append(node("div", `Joining: ${fmt(joining.original_runs,0)} → ${fmt(joining.joined_runs,0)} runs · +${fmt(joining.extra_pen_down_meters)} m pen down`));
            info.append(node("div", `Pen-up travel: ${fmt(joining.original_pen_up_meters)} → ${fmt(joining.joined_pen_up_meters)} m in listed order; excludes home/carousel moves.`));
          }
          for (const [label, file] of stages) {
            const a = node("a", label);
            a.href = artifact(current, id, file);
            a.download = `${current}-${id}-${file}`;
            info.append(a);
          }
        }
        info.append(node("div", e.description, "description"));
        if (e.edge_fade_mm > 0) {
          const a = node("a", "Ink emphasis map (white = more ink)");
          a.href = artifact(current, id, "ink-focus.png");
          a.download = `${current}-${id}-ink-focus.png`;
          info.append(a);
        }
      }
    }
    $("parameters").textContent = JSON.stringify(
      {
        source: {
          title: row.title,
          artist: row.artist,
          sha256: row.sha256,
          pixels: row.pixels,
        },
        left:
          left === "source" ? "Original" : cell(current, left).signature.recipe,
        right:
          right === "source"
            ? "Original"
            : cell(current, right).signature.recipe,
        algorithm: portfolio.algorithm,
        build_code_sha256: portfolio.code_sha256,
        sample_code_sha256: {
          left: left === "source" ? null : cell(current, left).signature.code,
          right: right === "source" ? null : cell(current, right).signature.code,
        },
        build_dependencies: portfolio.dependencies,
        sample_dependencies: {
          left: left === "source" ? null : cell(current, left).signature.versions,
          right: right === "source" ? null : cell(current, right).signature.versions,
        },
      },
      null,
      2,
    );
    const result = right === "source" ? null : cell(current, right);
    notes.setSample({
      challenge_id: row.id,
      source_sha256: row.sha256,
      experiment_id: right,
      result_key: result?.key || `${row.sha256.slice(0, 20)}-source`,
      experiment_name: right === "source" ? "Original" : experiment(right).name,
    });
    drawBoth();
  } catch (error) {
    $("right-info").textContent = error.message;
  } finally {
    if (version === requestVersion) notes.setLoading(false);
  }
}
async function openComparison(row, id) {
  $("challenge-select").value = row;
  $("left-select").value = "source";
  $("right-select").value = id;
  resetView();
  $("compare").showModal();
  await refreshComparison();
  if ($("family").value === "lettering") detailView();
}
function detailView() {
  const saved = portfolio.images.find((i) => i.id === current)?.detail_view;
  if (saved) {
    view = { ...saved };
    $("zoom").value = String(view.zoom);
    drawBoth();
  }
}
function resetView() {
  view = { zoom: 1, x: 0.5, y: 0.5 };
  $("zoom").value = "1";
  drawBoth();
}
function fit(canvas, img) {
  const box = canvas.getBoundingClientRect();
  const scale = Math.min(box.width / img.width, box.height / img.height);
  return {
    width: box.width,
    height: box.height,
    w: img.width * scale,
    h: img.height * scale,
  };
}
function drawCanvas(side) {
  const canvas = $(side + "-canvas"),
    img = side === "right" && flash ? images.source : images[side];
  if (!img) return;
  const f = fit(canvas, img);
  if (!f.width || !f.height) return;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(f.width * dpr);
  canvas.height = Math.round(f.height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.fillStyle = "#e3e7df";
  ctx.fillRect(0, 0, f.width, f.height);
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(
    img,
    f.width / 2 - view.x * f.w * view.zoom,
    f.height / 2 - view.y * f.h * view.zoom,
    f.w * view.zoom,
    f.h * view.zoom,
  );
}
function drawBoth() {
  drawCanvas("left");
  drawCanvas("right");
}
function setZoom(z) {
  view.zoom = Math.max(1, Math.min(8, z));
  $("zoom").value = String(view.zoom);
  drawBoth();
}
for (const side of ["left", "right"]) {
  const canvas = $(side + "-canvas");
  let drag;
  canvas.addEventListener("pointerdown", (e) => {
    drag = { x: e.clientX, y: e.clientY, cx: view.x, cy: view.y };
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!drag || !images[side]) return;
    const f = fit(canvas, images[side]);
    view.x = Math.max(
      0,
      Math.min(1, drag.cx - (e.clientX - drag.x) / (f.w * view.zoom)),
    );
    view.y = Math.max(
      0,
      Math.min(1, drag.cy - (e.clientY - drag.y) / (f.h * view.zoom)),
    );
    drawBoth();
  });
  canvas.addEventListener("pointerup", () => (drag = null));
  canvas.addEventListener("pointercancel", () => (drag = null));
  canvas.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      setZoom(view.zoom * (e.deltaY < 0 ? 2 : 0.5));
    },
    { passive: false },
  );
  canvas.addEventListener("dblclick", resetView);
}
$("search").addEventListener("input", render);
$("archive").addEventListener("toggle", () => {
  if (portfolio) render();
});
$("family").addEventListener("change", () => {
  const url = new URL(location.href);
  url.searchParams.set("family", $("family").value);
  if ((new URL(location.href).searchParams.get('family') === 'palette') !== ($('family').value === 'palette')) {
    location.href = url;
    return;
  }
  history.replaceState(null, "", url);
  render();
});
for (const id of ["columns", "about"])
  $(id + "-button").onclick = () => {
    const open = $(id).hidden;
    $(id).hidden = !open;
    $(id + "-button").setAttribute("aria-expanded", String(open));
  };
$("close").onclick = () => $("compare").close();
$("compare").addEventListener("close", () => {
  clearInterval(blinkTimer);
  $("blink").checked = false;
  flash = false;
});
$("challenge-select").onchange = () => {
  resetView();
  refreshComparison();
};
$("left-select").onchange = refreshComparison;
$("right-select").onchange = refreshComparison;
$("zoom").onchange = () => setZoom(Number($("zoom").value));
$("reset").onclick = resetView;
$("detail-view").onclick = detailView;
$("blink").onchange = () => {
  clearInterval(blinkTimer);
  flash = false;
  if ($("blink").checked)
    blinkTimer = setInterval(() => {
      flash = !flash;
      drawBoth();
    }, 650);
  drawBoth();
};
$("hold-original").onpointerdown = (e) => {
  e.currentTarget.setPointerCapture(e.pointerId);
  flash = true;
  drawBoth();
};
for (const event of ["pointerup", "pointercancel", "lostpointercapture"])
  $("hold-original").addEventListener(event, () => {
    flash = false;
    drawBoth();
  });
$("hold-original").onkeydown = (e) => {
  if (e.key === " " || e.key === "Enter") {
    e.preventDefault();
    flash = true;
    drawBoth();
  }
};
$("hold-original").onkeyup = () => {
  flash = false;
  drawBoth();
};
new ResizeObserver(() => drawBoth()).observe($("compare"));
try {
  const paletteStudy = new URL(location.href).searchParams.get('family') === 'palette';
  const response = await fetch('/artifacts/' + (paletteStudy ? 'palette-portfolio.json' : 'portfolio.json'));
  if (!response.ok) throw Error("The portfolio has not been generated yet.");
  portfolio = await response.json();
  currentRound = Math.max(0, ...portfolio.experiments.map((e) => e.round || 0));
  for (const e of portfolio.experiments)
    if (e.study) currentRounds[e.study] = Math.max(currentRounds[e.study] || 0, e.round || 0);
  enabled = new Set(portfolio.experiments.map((e) => e.id));
  $("summary").textContent =
    `${portfolio.images.length} challenges · ${Object.values(portfolio.cells).reduce((n, c) => n + Object.keys(c).length, 0)} results${portfolio.status === "complete" ? "" : " · building"}`;
  for (const e of portfolio.experiments) {
    const label = node("label");
    const check = node("input");
    check.type = "checkbox";
    check.dataset.experiment = e.id;
    check.checked = true;
    check.onchange = () => {
      if (check.checked) enabled.add(e.id);
      else enabled.delete(e.id);
      render();
    };
    label.append(check, document.createTextNode(e.name));
    $("columns").append(label);
  }
  for (const row of portfolio.images) {
    const opt = node("option", row.title);
    opt.value = row.id;
    $("challenge-select").append(opt);
  }
  fillSelect($("left-select"));
  fillSelect($("right-select"));
  const family = new URL(location.href).searchParams.get("family");
  if ([...$("family").options].some((option) => option.value === family))
    $("family").value = family;
  render();
  notes.load().catch((error) => {
    $("comment-status").textContent = error.message;
  });
} catch (error) {
  $("error").hidden = false;
  $("error").textContent = error.message;
}
