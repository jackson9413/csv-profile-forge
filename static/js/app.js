/* csv-profile-forge - front end (vanilla JS, no dependencies) */

const $ = (id) => document.getElementById(id);
const state = { sessionId: null, report: null };

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

async function api(path, options) {
  const res = await fetch(path, options);
  let data = null;
  try { data = await res.json(); } catch (err) { data = null; }
  if (!res.ok) {
    const msg = (data && data.error) ? data.error : ("request failed (" + res.status + ")");
    throw new Error(msg);
  }
  return data;
}

function showError(message) {
  const box = $("errorBox");
  if (!message) { box.classList.add("hidden"); box.textContent = ""; return; }
  box.textContent = message;
  box.classList.remove("hidden");
}

function scoreClass(value) {
  if (value >= 85) return "good";
  if (value >= 60) return "ok";
  return "bad";
}

function renderReport(report, sessionId) {
  state.report = report;
  state.sessionId = sessionId || null;
  $("emptyState").classList.add("hidden");
  $("report").classList.remove("hidden");

  const scores = report.scores || {};
  const meta = report.meta || {};
  $("scoreValue").textContent = scores.overall !== undefined ? scores.overall : "-";
  $("reportName").textContent = meta.dataset_name || "dataset";

  const pill = $("verdictPill");
  pill.textContent = scores.verdict_label || "-";
  pill.className = "verdict " + (scores.verdict || "");

  const chips = $("metaChips");
  clear(chips);
  [
    meta.rows + " rows",
    meta.columns + " columns",
    "delimiter: " + meta.delimiter,
    meta.purpose_label,
    "generated " + (meta.generated_at || "").replace("T", " ")
  ].forEach(function (text) { chips.appendChild(el("span", "chip", text)); });

  const axes = $("axisRow");
  clear(axes);
  const labels = report.axis_labels || {};
  const hints = report.axis_hints || {};
  Object.keys(labels).forEach(function (key) {
    const value = (scores.axes || {})[key] || 0;
    const box = el("div", "axis-pill");
    box.appendChild(el("div", "axis-name", labels[key]));
    box.appendChild(el("div", "axis-score", value));
    box.appendChild(el("div", "axis-hint", hints[key] || ""));
    const bar = el("div", "bar");
    const fill = el("span", scoreClass(value));
    fill.style.width = Math.max(2, Math.min(100, value)) + "%";
    bar.appendChild(fill);
    box.appendChild(bar);
    axes.appendChild(box);
  });

  const grid = $("checkGrid");
  clear(grid);
  (report.checks || []).forEach(function (check) {
    const box = el("div", "check " + (check.status === "pass" ? "pass" : "fail"));
    box.appendChild(el("span", "mark", check.status === "pass" ? "PASS" : "FAIL"));
    box.appendChild(el("span", null, check.label));
    grid.appendChild(box);
  });

  const findings = report.findings || [];
  const counts = scores.finding_counts || {};
  $("findingCount").textContent = findings.length
    ? counts.high + " high / " + counts.medium + " medium / " + counts.low + " low"
    : "";

  const list = $("findingsList");
  clear(list);
  if (!findings.length) {
    list.appendChild(el("p", "hint", "No findings. Every check passed."));
  }
  findings.forEach(function (finding) {
    const card = el("div", "finding " + finding.severity);
    const head = el("div", "finding-head");
    head.appendChild(el("span", "sev", finding.severity));
    head.appendChild(el("span", "finding-title", finding.title));
    if (finding.affected) {
      head.appendChild(el("span", "finding-count",
        finding.affected_rows ? finding.affected_rows + " rows" : finding.affected + " cells"));
    }
    card.appendChild(head);
    card.appendChild(el("div", "finding-detail", finding.detail));
    const fix = el("div", "finding-fix");
    fix.appendChild(el("strong", null, "Fix: "));
    fix.appendChild(document.createTextNode(finding.fix));
    card.appendChild(fix);
    if (finding.evidence && finding.evidence.length) {
      const wrap = el("div", "evidence");
      const table = el("table");
      const tbody = el("tbody");
      finding.evidence.forEach(function (item) {
        const tr = el("tr");
        tr.appendChild(el("td", null, "row " + item.row));
        tr.appendChild(el("td", null, item.value));
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      wrap.appendChild(table);
      card.appendChild(wrap);
    }
    list.appendChild(card);
  });

  const plan = $("cleaningPlan");
  clear(plan);
  (report.cleaning_plan || []).forEach(function (step) {
    const li = el("li");
    li.appendChild(document.createTextNode(step.action + " "));
    li.appendChild(el("span", "target", step.target));
    li.appendChild(el("span", "tag " + (step.auto ? "auto" : "manual"), step.auto ? "auto" : "manual"));
    li.appendChild(document.createTextNode(" - " + step.detail));
    plan.appendChild(li);
  });
  if (!(report.cleaning_plan || []).length) {
    plan.appendChild(el("li", "hint", "Nothing to clean."));
  }

  const table = $("profileTable");
  clear(table);
  const thead = el("thead");
  const hrow = el("tr");
  ["Column", "Type", "Missing", "Unique", "Top values", "Stats"].forEach(function (h) {
    hrow.appendChild(el("th", null, h));
  });
  thead.appendChild(hrow);
  table.appendChild(thead);
  const tbody = el("tbody");
  (report.columns || []).forEach(function (col) {
    const tr = el("tr");
    const nameCell = el("td");
    nameCell.appendChild(document.createTextNode(col.name));
    if (col.likely_key) nameCell.appendChild(el("span", "flag", "key"));
    if (col.is_constant) nameCell.appendChild(el("span", "flag", "constant"));
    if (col.mixed_with && col.mixed_with.length) {
      nameCell.appendChild(el("span", "flag", "mixed types"));
    }
    tr.appendChild(nameCell);
    const typeCell = el("td");
    typeCell.appendChild(el("span", "type-pill", col.type));
    tr.appendChild(typeCell);
    tr.appendChild(el("td", null, col.missing_pct + "%"));
    tr.appendChild(el("td", null, col.unique));
    const top = (col.top_values || []).slice(0, 3)
      .map(function (v) { return v.value + " (" + v.count + ")"; }).join(", ");
    tr.appendChild(el("td", null, top));
    let stats = "";
    if (col.stats && col.stats.count) {
      stats = "min " + col.stats.min + " / max " + col.stats.max + " / mean " + col.stats.mean;
    } else if (col.date_stats && col.date_stats.count) {
      stats = col.date_stats.min + " to " + col.date_stats.max;
    } else if (col.string_stats && col.string_stats.avg_len) {
      stats = "avg length " + col.string_stats.avg_len;
    }
    tr.appendChild(el("td", null, stats));
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);

  const preview = $("previewTable");
  clear(preview);
  const pthead = el("thead");
  const phrow = el("tr");
  ((report.preview || {}).headers || []).forEach(function (h) { phrow.appendChild(el("th", null, h)); });
  pthead.appendChild(phrow);
  preview.appendChild(pthead);
  const ptbody = el("tbody");
  ((report.preview || {}).rows || []).forEach(function (row) {
    const tr = el("tr");
    row.forEach(function (cell) { tr.appendChild(el("td", null, cell)); });
    ptbody.appendChild(tr);
  });
  preview.appendChild(ptbody);

  if (state.sessionId) {
    $("exportMd").href = "/api/export/" + state.sessionId + "?fmt=md";
    $("exportJson").href = "/api/export/" + state.sessionId + "?fmt=json";
    $("exportCsv").href = "/api/export/" + state.sessionId + "?fmt=csv";
  }
}

async function runProfile() {
  showError("");
  const btn = $("runBtn");
  btn.disabled = true;
  btn.textContent = "Profiling...";
  try {
    const payload = {
      csv: $("csvInput").value,
      dataset_name: $("datasetName").value,
      purpose: $("purpose").value,
      key_columns: $("keyColumns").value,
      expected_rows: $("expectedRows").value,
      delimiter: $("delimiter").value,
      has_header: $("hasHeader").checked,
      notes: ""
    };
    const report = await api("/api/profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    let sessionId = null;
    try {
      const saved = await api("/api/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile: report })
      });
      sessionId = saved.id;
    } catch (err) {
      /* saving is best effort; the report still renders */
    }
    renderReport(report, sessionId);
    loadHistory();
  } catch (err) {
    showError(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Profile dataset";
  }
}

async function loadHistory() {
  let data;
  try { data = await api("/api/sessions"); } catch (err) { return; }
  const list = $("historyList");
  clear(list);
  const sessions = (data && data.sessions) || [];
  if (!sessions.length) {
    list.appendChild(el("p", "hint", "No saved profiles yet."));
    return;
  }
  sessions.forEach(function (session) {
    const row = el("div", "history-row");
    const name = el("div", "name", session.dataset_name);
    name.addEventListener("click", function () { openSession(session.id); });
    row.appendChild(name);
    row.appendChild(el("div", "score", session.score + "/100"));
    row.appendChild(el("div", "meta",
      session.created_at + " / " + session.rows_count + " rows x " + session.columns_count + " cols"));
    row.appendChild(el("div", "meta", session.notes || ""));

    const actions = el("div", "history-actions");
    const rating = el("select");
    ["-", "1", "2", "3", "4", "5"].forEach(function (value) {
      const option = el("option", null, value === "-" ? "rating" : value + "★");
      option.value = value;
      if (String(session.self_rating || "-") === value) option.selected = true;
      rating.appendChild(option);
    });
    const notes = el("input");
    notes.type = "text";
    notes.placeholder = "notes for this profile";
    notes.value = session.notes || "";
    const save = el("button", "ghost", "Save");
    save.addEventListener("click", async function () {
      save.disabled = true;
      try {
        await api("/api/sessions/" + session.id, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            notes: notes.value,
            self_rating: rating.value === "-" ? null : parseInt(rating.value, 10)
          })
        });
        loadHistory();
      } catch (err) {
        showError(err.message);
      } finally {
        save.disabled = false;
      }
    });
    actions.appendChild(rating);
    actions.appendChild(notes);
    actions.appendChild(save);
    row.appendChild(actions);
    list.appendChild(row);
  });
}

async function openSession(id) {
  showError("");
  try {
    const record = await api("/api/sessions/" + id);
    renderReport(record.profile, id);
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (err) {
    showError(err.message);
  }
}

async function loadSample() {
  try {
    const data = await api("/api/sample");
    $("csvInput").value = data.csv;
    $("datasetName").value = "orders-export";
    showError("");
  } catch (err) {
    showError(err.message);
  }
}

$("runBtn").addEventListener("click", runProfile);
$("sampleBtn").addEventListener("click", loadSample);
$("clearBtn").addEventListener("click", function () {
  $("csvInput").value = "";
  $("report").classList.add("hidden");
  $("emptyState").classList.remove("hidden");
  state.sessionId = null;
  showError("");
});
$("csvInput").addEventListener("keydown", function (event) {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") { runProfile(); }
});

loadHistory();
