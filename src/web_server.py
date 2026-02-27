from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
import threading
import time
from urllib.parse import parse_qs, urlparse
import urllib.request

try:
    from .db_init import init_db
    from .openalex_client import OpenAlexClient, canonical_work_id
    from .storage import (
        add_alert_if_new,
        add_saved_search,
        add_edge,
        add_watch_target,
        connect,
        get_app_setting,
        list_alerts,
        list_notification_targets,
        list_saved_searches,
        list_watch_targets,
        mark_alert_pushed,
        set_app_setting,
        upsert_notification_target,
        update_watch_target_last_check,
        upsert_work,
    )
except ImportError:
    from db_init import init_db
    from openalex_client import OpenAlexClient, canonical_work_id
    from storage import (
        add_alert_if_new,
        add_saved_search,
        add_edge,
        add_watch_target,
        connect,
        get_app_setting,
        list_alerts,
        list_notification_targets,
        list_saved_searches,
        list_watch_targets,
        mark_alert_pushed,
        set_app_setting,
        upsert_notification_target,
        update_watch_target_last_check,
        upsert_work,
    )

LOGGER = logging.getLogger(__name__)
DOI_REGEX = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+$")

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Papermap Dashboard</title>
  <style>
    :root {
      --bg-a: #eef7f8;
      --bg-b: #fcf8f2;
      --ink: #102a43;
      --muted: #5f6c7b;
      --card: rgba(255, 255, 255, 0.82);
      --line: rgba(16, 42, 67, 0.13);
      --ok: #146c2e;
      --err: #b42318;
      --run: #8b5cf6;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      font-family: "IBM Plex Sans", "Avenir Next", "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at 10% 10%, #d7f1e0 0, transparent 38%),
        radial-gradient(circle at 90% 0%, #fee8cb 0, transparent 34%),
        linear-gradient(135deg, var(--bg-a), var(--bg-b));
      min-height: 100vh;
    }
    .shell { max-width: 1100px; margin: 0 auto; padding: 20px 16px 32px; }
    h1 { margin: 0; font-size: 30px; letter-spacing: 0.02em; }
    .sub { margin-top: 6px; color: var(--muted); font-size: 14px; }
    .bar {
      margin-top: 16px;
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: var(--card);
      backdrop-filter: blur(8px);
    }
    .bar input, .bar button {
      height: 36px;
      border-radius: 10px;
      border: 1px solid var(--line);
      padding: 0 10px;
      font: inherit;
    }
    .bar button {
      cursor: pointer;
      background: linear-gradient(135deg, #23395b, #2f6ba8);
      color: #fff;
      border: 0;
    }
    .grid {
      margin-top: 14px;
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 10px;
    }
    .card {
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
      background: var(--card);
      backdrop-filter: blur(8px);
    }
    .label { font-size: 12px; letter-spacing: 0.06em; color: var(--muted); text-transform: uppercase; }
    .value { margin-top: 6px; font-size: 26px; font-weight: 700; }
    .panel {
      margin-top: 14px;
      border: 1px solid var(--line);
      border-radius: 14px;
      overflow: hidden;
      background: var(--card);
      backdrop-filter: blur(8px);
    }
    .panel h2 {
      margin: 0;
      padding: 12px 14px;
      font-size: 17px;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.45);
    }
    .scroll { overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { padding: 10px 12px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
    th { color: var(--muted); font-weight: 600; white-space: nowrap; }
    .status { padding: 3px 8px; border-radius: 999px; font-size: 12px; font-weight: 600; }
    .s-success { color: var(--ok); background: rgba(20, 108, 46, 0.12); }
    .s-failed { color: var(--err); background: rgba(180, 35, 24, 0.12); }
    .s-running { color: var(--run); background: rgba(139, 92, 246, 0.13); }
    .hint { margin-top: 10px; color: var(--muted); font-size: 12px; }
    .error { color: var(--err); margin-top: 8px; font-size: 13px; }
    @media (max-width: 700px) {
      h1 { font-size: 24px; }
      .value { font-size: 22px; }
      th, td { padding: 8px 9px; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <h1>Papermap Dashboard</h1>
    <div class="sub">Interactive local dashboard for runs, watch targets, and graph counts.</div>
    <div class="bar">
      <label for="recentRuns">Recent runs</label>
      <input id="recentRuns" type="number" min="1" max="200" value="20">
      <button id="refreshBtn" type="button">Refresh</button>
      <label><input id="autoRefresh" type="checkbox" checked> Auto refresh (15s)</label>
      <span id="stamp" class="sub"></span>
    </div>
    <section class="panel">
      <h2>OpenAlex Settings</h2>
      <div style="padding:12px 14px; display:grid; grid-template-columns: 120px 1fr; gap:10px; align-items:center;">
        <label for="openalexKey">API Key</label>
        <input id="openalexKey" type="text" placeholder="OpenAlex API key (optional)">
        <label for="openalexMailto">Mailto</label>
        <input id="openalexMailto" type="email" placeholder="you@example.com">
        <span></span>
        <div>
          <button id="saveSettingsBtn" type="button">Save Settings</button>
          <span id="settingsStatus" class="sub" style="margin-left:10px;"></span>
        </div>
      </div>
    </section>
    <section class="panel">
      <h2>Search By DOI</h2>
      <div style="padding:12px 14px; display:grid; grid-template-columns: 120px 1fr; gap:10px; align-items:center;">
        <label for="doiInput">DOI</label>
        <input id="doiInput" type="text" placeholder="10.1093/bib/bbae583 or https://doi.org/...">
        <span></span>
        <div>
          <label><input id="saveWatchOnSearch" type="checkbox" checked> Save as watch target</label>
          <button id="searchDoiBtn" type="button">Search DOI</button>
        </div>
        <label>Result</label>
        <div id="doiResult" class="sub">No search yet.</div>
      </div>
    </section>
    <section class="panel">
      <h2>Batch DOI Search</h2>
      <div style="padding:12px 14px; display:grid; grid-template-columns: 120px 1fr; gap:10px; align-items:start;">
        <label for="doiBatchInput">DOIs</label>
        <textarea id="doiBatchInput" rows="4" placeholder="One DOI per line, or comma separated"></textarea>
        <span></span>
        <div>
          <label><input id="saveWatchOnBatch" type="checkbox" checked> Save as watch target</label>
          <button id="searchDoiBatchBtn" type="button">Search Batch</button>
        </div>
        <label>Batch Result</label>
        <div id="doiBatchResult" class="sub">No batch search yet.</div>
      </div>
    </section>
    <section class="panel">
      <h2>Related Papers</h2>
      <div style="padding:12px 14px; display:grid; grid-template-columns: 120px 1fr; gap:10px; align-items:start;">
        <label for="relatedDoiInput">Seed DOI</label>
        <input id="relatedDoiInput" type="text" placeholder="10.xxxx/...">
        <label for="maxReferencesInput">Max references</label>
        <input id="maxReferencesInput" type="number" min="1" max="20" value="5">
        <label for="maxCitingInput">Max citing</label>
        <input id="maxCitingInput" type="number" min="1" max="20" value="5">
        <span></span>
        <div>
          <label><input id="saveRelatedData" type="checkbox" checked> Save related data</label>
          <button id="relatedBtn" type="button">Explore Related</button>
        </div>
        <label>Related Result</label>
        <div id="relatedResult" class="sub">No related search yet.</div>
      </div>
    </section>
    <section class="panel">
      <h2>Similar Themes</h2>
      <div style="padding:12px 14px; display:grid; grid-template-columns: 120px 1fr; gap:10px; align-items:start;">
        <label for="similarDoiInput">Seed DOI</label>
        <input id="similarDoiInput" type="text" placeholder="10.xxxx/...">
        <label for="maxSimilarInput">Max similar</label>
        <input id="maxSimilarInput" type="number" min="1" max="30" value="8">
        <span></span>
        <div>
          <label><input id="saveSimilarData" type="checkbox" checked> Save similar data</label>
          <button id="similarBtn" type="button">Find Similar Themes</button>
        </div>
        <label>Similar Result</label>
        <div id="similarResult" class="sub">No similar search yet.</div>
      </div>
    </section>
    <section class="panel">
      <h2>Saved Searches</h2>
      <div style="padding:12px 14px; display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
        <label for="savedLimitInput">Limit</label>
        <input id="savedLimitInput" type="number" min="1" max="100" value="10">
        <button id="savedRefreshBtn" type="button">Refresh Saved Searches</button>
      </div>
      <div style="padding: 0 14px 14px;">
        <div id="savedSearchesResult" class="sub">No history loaded.</div>
      </div>
    </section>
    <section class="panel">
      <h2>Latest Scan</h2>
      <div style="padding:12px 14px; display:grid; grid-template-columns: 170px 1fr; gap:10px; align-items:center;">
        <label for="scanLookbackInput">Lookback days</label>
        <input id="scanLookbackInput" type="number" min="1" max="365" value="30">
        <label for="scanPagesInput">Pages per target</label>
        <input id="scanPagesInput" type="number" min="1" max="20" value="1">
        <span></span>
        <div>
          <button id="scanLatestBtn" type="button">Scan Latest Citing Papers</button>
          <span id="scanLatestStatus" class="sub" style="margin-left:10px;"></span>
        </div>
      </div>
    </section>
    <section class="panel">
      <h2>Auto Scan Worker</h2>
      <div style="padding:12px 14px; display:grid; grid-template-columns: 170px 1fr; gap:10px; align-items:center;">
        <label for="autoscanEnabled">Enabled</label>
        <label><input id="autoscanEnabled" type="checkbox"> Run scan in background</label>
        <label for="autoscanIntervalInput">Interval seconds</label>
        <input id="autoscanIntervalInput" type="number" min="1" max="86400" value="300">
        <label for="autoscanLookbackInput">Lookback days</label>
        <input id="autoscanLookbackInput" type="number" min="1" max="365" value="30">
        <label for="autoscanPagesInput">Pages per target</label>
        <input id="autoscanPagesInput" type="number" min="1" max="20" value="1">
        <label for="autoscanPushNew">Push after scan</label>
        <label><input id="autoscanPushNew" type="checkbox" checked> Push new alerts to webhook</label>
        <span></span>
        <div>
          <button id="saveAutoscanBtn" type="button">Save Auto Scan Config</button>
          <span id="autoscanStatus" class="sub" style="margin-left:10px;"></span>
        </div>
      </div>
    </section>
    <section class="panel">
      <h2>Notification Push (Webhook)</h2>
      <div style="padding:12px 14px; display:grid; grid-template-columns: 170px 1fr; gap:10px; align-items:center;">
        <label for="webhookUrlInput">Webhook URL</label>
        <input id="webhookUrlInput" type="text" placeholder="https://your-webhook.example/path">
        <span></span>
        <div>
          <button id="saveWebhookBtn" type="button">Save Webhook</button>
          <button id="pushAlertsBtn" type="button">Push New Alerts</button>
          <span id="pushStatus" class="sub" style="margin-left:10px;"></span>
        </div>
      </div>
    </section>
    <div id="error" class="error"></div>
    <div class="grid" id="cards"></div>
    <section class="panel">
      <h2>Recent Runs</h2>
      <div class="scroll"><table id="runsTable"></table></div>
    </section>
    <section class="panel">
      <h2>Watch Targets</h2>
      <div class="scroll"><table id="watchTable"></table></div>
    </section>
    <div class="hint">Tip: keep this tab open while running CLI jobs to observe changes.</div>
  </div>
  <script>
    const cards = document.getElementById("cards");
    const runsTable = document.getElementById("runsTable");
    const watchTable = document.getElementById("watchTable");
    const errorBox = document.getElementById("error");
    const stamp = document.getElementById("stamp");
    const recentRunsInput = document.getElementById("recentRuns");
    const autoRefresh = document.getElementById("autoRefresh");
    const refreshBtn = document.getElementById("refreshBtn");
    const openalexKey = document.getElementById("openalexKey");
    const openalexMailto = document.getElementById("openalexMailto");
    const saveSettingsBtn = document.getElementById("saveSettingsBtn");
    const settingsStatus = document.getElementById("settingsStatus");
    const doiInput = document.getElementById("doiInput");
    const searchDoiBtn = document.getElementById("searchDoiBtn");
    const saveWatchOnSearch = document.getElementById("saveWatchOnSearch");
    const doiResult = document.getElementById("doiResult");
    const doiBatchInput = document.getElementById("doiBatchInput");
    const saveWatchOnBatch = document.getElementById("saveWatchOnBatch");
    const searchDoiBatchBtn = document.getElementById("searchDoiBatchBtn");
    const doiBatchResult = document.getElementById("doiBatchResult");
    const relatedDoiInput = document.getElementById("relatedDoiInput");
    const maxReferencesInput = document.getElementById("maxReferencesInput");
    const maxCitingInput = document.getElementById("maxCitingInput");
    const saveRelatedData = document.getElementById("saveRelatedData");
    const relatedBtn = document.getElementById("relatedBtn");
    const relatedResult = document.getElementById("relatedResult");
    const similarDoiInput = document.getElementById("similarDoiInput");
    const maxSimilarInput = document.getElementById("maxSimilarInput");
    const saveSimilarData = document.getElementById("saveSimilarData");
    const similarBtn = document.getElementById("similarBtn");
    const similarResult = document.getElementById("similarResult");
    const savedLimitInput = document.getElementById("savedLimitInput");
    const savedRefreshBtn = document.getElementById("savedRefreshBtn");
    const savedSearchesResult = document.getElementById("savedSearchesResult");
    const scanLookbackInput = document.getElementById("scanLookbackInput");
    const scanPagesInput = document.getElementById("scanPagesInput");
    const scanLatestBtn = document.getElementById("scanLatestBtn");
    const scanLatestStatus = document.getElementById("scanLatestStatus");
    const autoscanEnabled = document.getElementById("autoscanEnabled");
    const autoscanIntervalInput = document.getElementById("autoscanIntervalInput");
    const autoscanLookbackInput = document.getElementById("autoscanLookbackInput");
    const autoscanPagesInput = document.getElementById("autoscanPagesInput");
    const autoscanPushNew = document.getElementById("autoscanPushNew");
    const saveAutoscanBtn = document.getElementById("saveAutoscanBtn");
    const autoscanStatus = document.getElementById("autoscanStatus");
    const webhookUrlInput = document.getElementById("webhookUrlInput");
    const saveWebhookBtn = document.getElementById("saveWebhookBtn");
    const pushAlertsBtn = document.getElementById("pushAlertsBtn");
    const pushStatus = document.getElementById("pushStatus");
    let timer = null;

    function esc(v) {
      if (v === null || v === undefined) return "-";
      return String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    function statusClass(status) {
      if (status === "success") return "s-success";
      if (status === "failed") return "s-failed";
      return "s-running";
    }

    function renderCards(counts) {
      const keys = [
        ["papers", "Papers"],
        ["edges", "Edges"],
        ["watch_targets", "Watch Targets"],
        ["runs", "Runs"],
      ];
      cards.innerHTML = keys
        .map(([key, label]) => `<div class="card"><div class="label">${label}</div><div class="value">${esc((counts && counts[key] !== undefined && counts[key] !== null) ? counts[key] : 0)}</div></div>`)
        .join("");
    }

    function renderRuns(rows) {
      const head = "<tr><th>ID</th><th>Job</th><th>Status</th><th>Started</th><th>Finished</th><th>Detail</th></tr>";
      const body = rows.length
        ? rows.map((row) => `<tr>
            <td>${esc(row.id)}</td>
            <td>${esc(row.job_name)}</td>
            <td><span class="status ${statusClass(row.status)}">${esc(row.status)}</span></td>
            <td>${esc(row.started_at)}</td>
            <td>${esc(row.finished_at)}</td>
            <td>${esc(row.detail)}</td>
          </tr>`).join("")
        : "<tr><td colspan='6'>No runs found.</td></tr>";
      runsTable.innerHTML = head + body;
    }

    function renderWatch(rows) {
      const head = "<tr><th>ID</th><th>Type</th><th>Value</th><th>Enabled</th><th>Last Check</th><th>Note</th></tr>";
      const body = rows.length
        ? rows.map((row) => `<tr>
            <td>${esc(row.id)}</td>
            <td>${esc(row.target_type)}</td>
            <td>${esc(row.target_value)}</td>
            <td>${esc(row.enabled)}</td>
            <td>${esc(row.last_check_date)}</td>
            <td>${esc(row.note)}</td>
          </tr>`).join("")
        : "<tr><td colspan='6'>No watch targets found.</td></tr>";
      watchTable.innerHTML = head + body;
    }

    async function loadData() {
      const recentRuns = Math.max(1, Number(recentRunsInput.value || 20));
      recentRunsInput.value = String(recentRuns);
      errorBox.textContent = "";
      try {
        const res = await fetch(`/api/dashboard?recent_runs=${recentRuns}`, { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const payload = await res.json();
        renderCards(payload.counts || {});
        renderRuns(payload.recent_runs || []);
        renderWatch(payload.watch_targets || []);
        stamp.textContent = `Updated: ${new Date().toLocaleString()}`;
      } catch (err) {
        errorBox.textContent = `Load failed: ${err}`;
      }
    }

    async function loadOpenAlexSettings() {
      try {
        const res = await fetch("/api/settings/openalex", { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const payload = await res.json();
        openalexKey.value = payload.api_key || "";
        openalexMailto.value = payload.mailto || "";
      } catch (err) {
        settingsStatus.textContent = `Load failed: ${err}`;
      }
    }

    async function saveOpenAlexSettings() {
      settingsStatus.textContent = "";
      try {
        const body = {
          api_key: openalexKey.value.trim(),
          mailto: openalexMailto.value.trim(),
        };
        const res = await fetch("/api/settings/openalex", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!res.ok) {
          const text = await res.text();
          throw new Error(`HTTP ${res.status}: ${text}`);
        }
        settingsStatus.textContent = "Saved";
      } catch (err) {
        settingsStatus.textContent = `Save failed: ${err}`;
      }
    }

    function renderDoiResult(work) {
      if (!work) {
        doiResult.textContent = "No work found.";
        return;
      }
      const lines = [
        `Title: ${work.title || "-"}`,
        `Work ID: ${work.paper_id || "-"}`,
        `DOI: ${work.doi || "-"}`,
        `Published: ${work.published_date || "-"}`,
        `Journal: ${work.journal || "-"}`,
      ];
      doiResult.innerHTML = lines.map((x) => esc(x)).join("<br>");
    }

    async function searchByDoi() {
      doiResult.textContent = "";
      const doi = doiInput.value.trim();
      if (!doi) {
        doiResult.textContent = "Please fill DOI first.";
        return;
      }
      try {
        const res = await fetch("/api/works/resolve-doi", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            doi,
            save_watch: Boolean(saveWatchOnSearch.checked),
            save_search: true,
          }),
        });
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.error || `HTTP ${res.status}`);
        renderDoiResult(payload.work || null);
        await loadData();
        await loadSavedSearches();
      } catch (err) {
        doiResult.textContent = `Search failed: ${err}`;
      }
    }

    function splitDois(text) {
      return (text || "")
        .split(/[\n,]/g)
        .map((s) => s.trim())
        .filter((s) => s.length > 0);
    }

    async function searchByDoiBatch() {
      doiBatchResult.textContent = "";
      const dois = splitDois(doiBatchInput.value);
      if (dois.length === 0) {
        doiBatchResult.textContent = "Please fill at least one DOI.";
        return;
      }
      try {
        const res = await fetch("/api/works/resolve-dois", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            dois,
            save_watch: Boolean(saveWatchOnBatch.checked),
            save_search: true,
          }),
        });
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.error || `HTTP ${res.status}`);

        const found = payload.found || [];
        const failed = payload.failed || [];
        const lines = [];
        lines.push(`Found: ${found.length}, Failed: ${failed.length}`);
        for (const item of found) {
          lines.push(`- ${item.doi || "-"} => ${item.paper_id || "-"} (${item.title || "-"})`);
        }
        for (const item of failed) {
          lines.push(`- ${item.doi || "-"} => ${item.error || "unknown error"}`);
        }
        doiBatchResult.innerHTML = lines.map((x) => esc(x)).join("<br>");
        await loadData();
        await loadSavedSearches();
      } catch (err) {
        doiBatchResult.textContent = `Batch search failed: ${err}`;
      }
    }

    async function exploreRelated() {
      relatedResult.textContent = "";
      const doi = relatedDoiInput.value.trim();
      if (!doi) {
        relatedResult.textContent = "Please fill seed DOI.";
        return;
      }
      const maxReferences = Math.max(1, Number(maxReferencesInput.value || 5));
      const maxCiting = Math.max(1, Number(maxCitingInput.value || 5));
      try {
        const res = await fetch("/api/works/related", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            doi,
            max_references: maxReferences,
            max_citing: maxCiting,
            save: Boolean(saveRelatedData.checked),
          }),
        });
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.error || `HTTP ${res.status}`);
        const seed = payload.seed || {};
        const refs = payload.references || [];
        const citing = payload.citing || [];
        const lines = [];
        lines.push(`Seed: ${seed.paper_id || "-"} ${seed.title || "-"}`);
        lines.push(`References: ${refs.length}`);
        for (const x of refs) lines.push(`- REF ${x.paper_id || "-"} ${x.title || "-"}`);
        lines.push(`Citing: ${citing.length}`);
        for (const x of citing) lines.push(`- CIT ${x.paper_id || "-"} ${x.title || "-"}`);
        relatedResult.innerHTML = lines.map((x) => esc(x)).join("<br>");
        await loadData();
      } catch (err) {
        relatedResult.textContent = `Related search failed: ${err}`;
      }
    }

    async function findSimilarThemes() {
      similarResult.textContent = "";
      const doi = similarDoiInput.value.trim();
      if (!doi) {
        similarResult.textContent = "Please fill seed DOI.";
        return;
      }
      const maxSimilar = Math.max(1, Number(maxSimilarInput.value || 8));
      try {
        const res = await fetch("/api/works/similar", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            doi,
            max_similar: maxSimilar,
            save: Boolean(saveSimilarData.checked),
          }),
        });
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.error || `HTTP ${res.status}`);
        const lines = [];
        const seed = payload.seed || {};
        lines.push(`Seed: ${seed.paper_id || "-"} ${seed.title || "-"}`);
        lines.push(`Topics: ${(payload.topics || []).join(", ") || "-"}`);
        lines.push(`Similar count: ${(payload.similar || []).length}`);
        for (const x of payload.similar || []) {
          lines.push(`- ${x.paper_id || "-"} ${x.title || "-"} (${x.journal || "-"})`);
        }
        similarResult.innerHTML = lines.map((x) => esc(x)).join("<br>");
        await loadData();
        await loadSavedSearches();
      } catch (err) {
        similarResult.textContent = `Similar search failed: ${err}`;
      }
    }

    async function loadSavedSearches() {
      savedSearchesResult.textContent = "";
      const limit = Math.max(1, Number(savedLimitInput.value || 10));
      savedLimitInput.value = String(limit);
      try {
        const res = await fetch(`/api/saved-searches?limit=${limit}`, { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const payload = await res.json();
        const rows = payload.items || [];
        if (rows.length === 0) {
          savedSearchesResult.textContent = "No saved searches.";
          return;
        }
        const lines = rows.map((row) => {
          const dois = (row.doi_list || []).join(", ");
          const summary = row.result_summary || {};
          const found = summary.found !== undefined && summary.found !== null ? summary.found : "-";
          const failed = summary.failed !== undefined && summary.failed !== null ? summary.failed : "-";
          return `#${row.id} [${row.created_at}] DOIs: ${dois} | found=${found} failed=${failed}`;
        });
        savedSearchesResult.innerHTML = lines.map((x) => esc(x)).join("<br>");
      } catch (err) {
        savedSearchesResult.textContent = `Load failed: ${err}`;
      }
    }

    async function runLatestScan() {
      scanLatestStatus.textContent = "";
      const lookbackDays = Math.max(1, Number(scanLookbackInput.value || 30));
      const pages = Math.max(1, Number(scanPagesInput.value || 1));
      try {
        const res = await fetch("/api/latest/scan", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            lookback_days: lookbackDays,
            max_pages_per_target: pages,
          }),
        });
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.error || `HTTP ${res.status}`);
        scanLatestStatus.textContent = `targets=${payload.targets_scanned}, papers=${payload.citing_papers_processed}, new_alerts=${payload.alerts_new}`;
        await loadData();
      } catch (err) {
        scanLatestStatus.textContent = `Scan failed: ${err}`;
      }
    }

    async function loadAutoscanConfig() {
      autoscanStatus.textContent = "";
      try {
        const res = await fetch("/api/autoscan/config", { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const payload = await res.json();
        autoscanEnabled.checked = Boolean(payload.enabled);
        autoscanIntervalInput.value = String(payload.interval_seconds || 300);
        autoscanLookbackInput.value = String(payload.lookback_days || 30);
        autoscanPagesInput.value = String(payload.max_pages_per_target || 1);
        autoscanPushNew.checked = Boolean(payload.push_new);
        autoscanStatus.textContent = payload.last_run_at ? `last_run_at=${payload.last_run_at}` : "Not run yet";
      } catch (err) {
        autoscanStatus.textContent = `Load config failed: ${err}`;
      }
    }

    async function saveAutoscanConfig() {
      autoscanStatus.textContent = "";
      const intervalSeconds = Math.max(1, Number(autoscanIntervalInput.value || 300));
      const lookbackDays = Math.max(1, Number(autoscanLookbackInput.value || 30));
      const pages = Math.max(1, Number(autoscanPagesInput.value || 1));
      autoscanIntervalInput.value = String(intervalSeconds);
      autoscanLookbackInput.value = String(lookbackDays);
      autoscanPagesInput.value = String(pages);
      try {
        const res = await fetch("/api/autoscan/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            enabled: Boolean(autoscanEnabled.checked),
            interval_seconds: intervalSeconds,
            lookback_days: lookbackDays,
            max_pages_per_target: pages,
            push_new: Boolean(autoscanPushNew.checked),
          }),
        });
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.error || `HTTP ${res.status}`);
        autoscanStatus.textContent = "Auto scan config saved";
      } catch (err) {
        autoscanStatus.textContent = `Save config failed: ${err}`;
      }
    }

    async function loadWebhookConfig() {
      pushStatus.textContent = "";
      try {
        const res = await fetch("/api/notifications/webhook", { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const payload = await res.json();
        const first = (payload.items || [])[0];
        webhookUrlInput.value = first ? (first.target_value || "") : "";
      } catch (err) {
        pushStatus.textContent = `Load webhook failed: ${err}`;
      }
    }

    async function saveWebhookConfig() {
      pushStatus.textContent = "";
      try {
        const url = webhookUrlInput.value.trim();
        const res = await fetch("/api/notifications/webhook", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url, enabled: true }),
        });
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.error || `HTTP ${res.status}`);
        pushStatus.textContent = "Webhook saved";
      } catch (err) {
        pushStatus.textContent = `Save webhook failed: ${err}`;
      }
    }

    async function pushNewAlerts() {
      pushStatus.textContent = "";
      try {
        const res = await fetch("/api/alerts/push-new", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ limit: 50 }),
        });
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.error || `HTTP ${res.status}`);
        pushStatus.textContent = `alerts=${payload.alerts_considered}, pushed=${payload.pushed}, failed=${payload.failed}`;
        await loadData();
      } catch (err) {
        pushStatus.textContent = `Push failed: ${err}`;
      }
    }

    function startTimer() {
      if (timer) clearInterval(timer);
      if (autoRefresh.checked) {
        timer = setInterval(loadData, 15000);
      }
    }

    refreshBtn.addEventListener("click", loadData);
    saveSettingsBtn.addEventListener("click", saveOpenAlexSettings);
    searchDoiBtn.addEventListener("click", searchByDoi);
    searchDoiBatchBtn.addEventListener("click", searchByDoiBatch);
    relatedBtn.addEventListener("click", exploreRelated);
    similarBtn.addEventListener("click", findSimilarThemes);
    savedRefreshBtn.addEventListener("click", loadSavedSearches);
    scanLatestBtn.addEventListener("click", runLatestScan);
    saveAutoscanBtn.addEventListener("click", saveAutoscanConfig);
    saveWebhookBtn.addEventListener("click", saveWebhookConfig);
    pushAlertsBtn.addEventListener("click", pushNewAlerts);
    autoRefresh.addEventListener("change", startTimer);
    recentRunsInput.addEventListener("change", loadData);
    loadOpenAlexSettings();
    loadData();
    loadSavedSearches();
    loadAutoscanConfig();
    loadWebhookConfig();
    startTimer();
  </script>
</body>
</html>
"""


def _parse_stats(value: str | None) -> Any:
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return text


def build_dashboard_payload(db_path: Path, recent_runs: int, watch_limit: int = 200) -> dict[str, Any]:
    conn = connect(db_path)
    try:
        counts = {
            "papers": int(conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]),
            "edges": int(conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]),
            "watch_targets": int(conn.execute("SELECT COUNT(*) FROM watch_targets").fetchone()[0]),
            "runs": int(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]),
        }
        run_rows = conn.execute(
            """
            SELECT id, job_name, status, started_at, finished_at, detail, stats_json
            FROM runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(recent_runs),),
        ).fetchall()
        watch_rows = list_watch_targets(conn, target_type="paper", include_disabled=True, limit=int(watch_limit))
    finally:
        conn.close()

    recent = [
        {
            "id": int(row["id"]),
            "job_name": row["job_name"],
            "status": row["status"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "detail": row["detail"],
            "stats_json": _parse_stats(row["stats_json"]),
        }
        for row in run_rows
    ]
    watches = [
        {
            "id": int(row["id"]),
            "target_type": row["target_type"],
            "target_value": row["target_value"],
            "enabled": int(row["enabled"]),
            "last_check_date": row["last_check_date"],
            "note": row["note"],
        }
        for row in watch_rows
    ]
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "db_path": str(db_path),
        "counts": counts,
        "recent_runs": recent,
        "watch_targets": watches,
    }


def _build_openalex_client_from_db(db_path: Path) -> OpenAlexClient:
    conn = connect(db_path)
    try:
        api_key = get_app_setting(conn, "openalex.api_key", default=None)
        mailto = get_app_setting(conn, "openalex.mailto", default=None)
    finally:
        conn.close()
    return OpenAlexClient(api_key=api_key, mailto=mailto)


def _fallback_from_date(lookback_days: int) -> str:
    return (date.today() - timedelta(days=max(1, int(lookback_days)))).isoformat()


def _parse_int_setting(value: str | None, default: int, *, minimum: int = 0) -> int:
    if value is None or str(value).strip() == "":
        return default
    try:
        out = int(value)
    except ValueError:
        return default
    return out if out >= minimum else default


def _parse_bool_setting(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def get_autoscan_config(db_path: Path) -> dict[str, Any]:
    conn = connect(db_path)
    try:
        enabled = _parse_bool_setting(get_app_setting(conn, "autoscan.enabled", default="0"), default=False)
        interval_seconds = _parse_int_setting(
            get_app_setting(conn, "autoscan.interval_seconds", default="300"),
            default=300,
            minimum=1,
        )
        lookback_days = _parse_int_setting(
            get_app_setting(conn, "autoscan.lookback_days", default="30"),
            default=30,
            minimum=1,
        )
        max_pages_per_target = _parse_int_setting(
            get_app_setting(conn, "autoscan.max_pages_per_target", default="1"),
            default=1,
            minimum=1,
        )
        push_new = _parse_bool_setting(get_app_setting(conn, "autoscan.push_new", default="1"), default=True)
        last_run_at = get_app_setting(conn, "autoscan.last_run_at", default=None)
    finally:
        conn.close()
    return {
        "enabled": enabled,
        "interval_seconds": interval_seconds,
        "lookback_days": lookback_days,
        "max_pages_per_target": max_pages_per_target,
        "push_new": push_new,
        "last_run_at": last_run_at,
    }


def set_autoscan_config(
    db_path: Path,
    *,
    enabled: bool,
    interval_seconds: int,
    lookback_days: int,
    max_pages_per_target: int,
    push_new: bool,
) -> dict[str, Any]:
    conn = connect(db_path)
    try:
        set_app_setting(conn, "autoscan.enabled", "1" if enabled else "0")
        set_app_setting(conn, "autoscan.interval_seconds", str(int(interval_seconds)))
        set_app_setting(conn, "autoscan.lookback_days", str(int(lookback_days)))
        set_app_setting(conn, "autoscan.max_pages_per_target", str(int(max_pages_per_target)))
        set_app_setting(conn, "autoscan.push_new", "1" if push_new else "0")
        conn.commit()
    finally:
        conn.close()
    return get_autoscan_config(db_path)


def latest_scan_core(db_path: Path, *, lookback_days: int, max_pages_per_target: int) -> dict[str, Any]:
    client = _build_openalex_client_from_db(db_path)
    targets_scanned = 0
    citing_processed = 0
    alerts_new = 0

    conn = connect(db_path)
    try:
        targets = list_watch_targets(conn, target_type="paper", include_disabled=False, limit=1000)
        for target in targets:
            target_id = str(target["target_value"])
            watch_target_id = int(target["id"])
            from_date = target["last_check_date"] or _fallback_from_date(lookback_days)
            targets_scanned += 1
            try:
                for work in client.iter_citing_works(
                    target_id,
                    from_publication_date=str(from_date),
                    max_pages=max_pages_per_target,
                ):
                    citing_id = upsert_work(conn, work, source="web-latest-scan")
                    add_edge(conn, citing_id, target_id, "cites")
                    alert_id, created = add_alert_if_new(
                        conn,
                        watch_target_id=watch_target_id,
                        paper_id=citing_id,
                        alert_type="new_citation",
                        status="new",
                        payload_json=json.dumps(
                            {
                                "paper_id": citing_id,
                                "title": work.get("title") or work.get("display_name"),
                                "doi": str(work.get("doi") or "").replace("https://doi.org/", "") or None,
                            },
                            ensure_ascii=False,
                        ),
                    )
                    if alert_id and created:
                        alerts_new += 1
                    citing_processed += 1
                update_watch_target_last_check(conn, "paper", target_id)
            except Exception as exc:
                LOGGER.warning("latest-scan failed target=%s error=%s", target_id, exc)
        conn.commit()
    finally:
        conn.close()

    return {
        "targets_scanned": targets_scanned,
        "citing_papers_processed": citing_processed,
        "alerts_new": alerts_new,
        "lookback_days": lookback_days,
        "max_pages_per_target": max_pages_per_target,
    }


def _send_webhook_post(url: str, payload: dict[str, Any]) -> tuple[bool, str | None]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            status = int(response.status)
        if 200 <= status < 300:
            return True, None
        return False, f"HTTP {status}"
    except Exception as exc:
        return False, str(exc)


def push_new_alerts_core(db_path: Path, *, limit: int = 50) -> dict[str, Any]:
    conn = connect(db_path)
    try:
        webhooks = list_notification_targets(conn, target_type="webhook", include_disabled=False, limit=20)
        alerts = list_alerts(conn, status="new", limit=max(1, int(limit)))
        if not webhooks:
            return {"alerts_considered": len(alerts), "pushed": 0, "failed": len(alerts), "errors": ["No webhook configured"]}

        pushed = 0
        failed = 0
        errors: list[str] = []
        for alert in alerts:
            payload = {
                "alert_id": int(alert["id"]),
                "watch_target_id": int(alert["watch_target_id"]),
                "paper_id": alert["paper_id"],
                "alert_type": alert["alert_type"],
                "status": alert["status"],
                "payload": json.loads(alert["payload_json"]) if alert["payload_json"] else None,
                "created_at": alert["created_at"],
            }
            sent = False
            for webhook in webhooks:
                ok, err = _send_webhook_post(str(webhook["target_value"]), payload)
                if ok:
                    sent = True
                elif err:
                    errors.append(err)
            if sent:
                mark_alert_pushed(conn, int(alert["id"]))
                pushed += 1
            else:
                failed += 1
        conn.commit()
        return {"alerts_considered": len(alerts), "pushed": pushed, "failed": failed, "errors": errors[:10]}
    finally:
        conn.close()


def _auto_scan_loop(db_path: Path, stop_event: threading.Event) -> None:
    last_tick = 0.0
    while not stop_event.wait(1.0):
        cfg = get_autoscan_config(db_path)
        if not cfg["enabled"]:
            continue
        now = time.time()
        if now - last_tick < int(cfg["interval_seconds"]):
            continue
        last_tick = now
        try:
            scan_result = latest_scan_core(
                db_path,
                lookback_days=int(cfg["lookback_days"]),
                max_pages_per_target=int(cfg["max_pages_per_target"]),
            )
            push_result: dict[str, Any] | None = None
            if bool(cfg["push_new"]):
                push_result = push_new_alerts_core(db_path, limit=100)
            conn = connect(db_path)
            try:
                set_app_setting(conn, "autoscan.last_run_at", datetime.now().isoformat(timespec="seconds"))
                set_app_setting(
                    conn,
                    "autoscan.last_result",
                    json.dumps({"scan": scan_result, "push": push_result}, ensure_ascii=False),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            LOGGER.warning("autoscan loop failed error=%s", exc)


class DashboardHandler(BaseHTTPRequestHandler):
    db_path: Path = Path("data/papermap.db")
    default_recent_runs: int = 20

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        LOGGER.info("web request start path=%s", parsed.path)
        try:
            if parsed.path in {"/", "/index.html"}:
                self._send_html(INDEX_HTML)
                LOGGER.info("web request success path=%s", parsed.path)
                return
            if parsed.path == "/api/dashboard":
                self._handle_dashboard(parsed.query)
                LOGGER.info("web request success path=%s", parsed.path)
                return
            if parsed.path == "/api/settings/openalex":
                self._handle_get_openalex_settings()
                LOGGER.info("web request success path=%s", parsed.path)
                return
            if parsed.path == "/api/autoscan/config":
                self._handle_get_autoscan_config()
                LOGGER.info("web request success path=%s", parsed.path)
                return
            if parsed.path == "/api/saved-searches":
                self._handle_get_saved_searches(parsed.query)
                LOGGER.info("web request success path=%s", parsed.path)
                return
            if parsed.path == "/api/notifications/webhook":
                self._handle_get_webhook_targets()
                LOGGER.info("web request success path=%s", parsed.path)
                return
            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            LOGGER.exception("web request failed path=%s error=%s", parsed.path, exc)
            self._send_json({"error": "Internal server error"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        LOGGER.info("web request start method=POST path=%s", parsed.path)
        try:
            if parsed.path == "/api/settings/openalex":
                self._handle_post_openalex_settings()
                LOGGER.info("web request success method=POST path=%s", parsed.path)
                return
            if parsed.path == "/api/autoscan/config":
                self._handle_post_autoscan_config()
                LOGGER.info("web request success method=POST path=%s", parsed.path)
                return
            if parsed.path == "/api/works/resolve-doi":
                self._handle_post_resolve_doi()
                LOGGER.info("web request success method=POST path=%s", parsed.path)
                return
            if parsed.path == "/api/works/resolve-dois":
                self._handle_post_resolve_dois()
                LOGGER.info("web request success method=POST path=%s", parsed.path)
                return
            if parsed.path == "/api/works/related":
                self._handle_post_related_works()
                LOGGER.info("web request success method=POST path=%s", parsed.path)
                return
            if parsed.path == "/api/works/similar":
                self._handle_post_similar_works()
                LOGGER.info("web request success method=POST path=%s", parsed.path)
                return
            if parsed.path == "/api/latest/scan":
                self._handle_post_latest_scan()
                LOGGER.info("web request success method=POST path=%s", parsed.path)
                return
            if parsed.path == "/api/notifications/webhook":
                self._handle_post_webhook_target()
                LOGGER.info("web request success method=POST path=%s", parsed.path)
                return
            if parsed.path == "/api/alerts/push-new":
                self._handle_post_push_new_alerts()
                LOGGER.info("web request success method=POST path=%s", parsed.path)
                return
            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            LOGGER.exception("web request failed method=POST path=%s error=%s", parsed.path, exc)
            self._send_json({"error": "Internal server error"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_dashboard(self, query: str) -> None:
        params = parse_qs(query)
        raw_recent = params.get("recent_runs", [str(self.default_recent_runs)])[0]
        try:
            recent_runs = int(raw_recent)
        except ValueError:
            self._send_json({"error": "recent_runs must be an integer"}, HTTPStatus.BAD_REQUEST)
            return
        if recent_runs <= 0:
            self._send_json({"error": "recent_runs must be > 0"}, HTTPStatus.BAD_REQUEST)
            return

        payload = build_dashboard_payload(self.db_path, recent_runs=recent_runs)
        self._send_json(payload, HTTPStatus.OK)

    def _handle_get_openalex_settings(self) -> None:
        conn = connect(self.db_path)
        try:
            api_key = get_app_setting(conn, "openalex.api_key", default="") or ""
            mailto = get_app_setting(conn, "openalex.mailto", default="") or ""
        finally:
            conn.close()
        self._send_json({"api_key": api_key, "mailto": mailto}, HTTPStatus.OK)

    def _handle_post_openalex_settings(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        api_key = str(body.get("api_key", "")).strip()
        mailto = str(body.get("mailto", "")).strip()
        if mailto and "@" not in mailto:
            self._send_json({"error": "mailto must contain @"}, HTTPStatus.BAD_REQUEST)
            return

        conn = connect(self.db_path)
        try:
            set_app_setting(conn, "openalex.api_key", api_key)
            set_app_setting(conn, "openalex.mailto", mailto)
            conn.commit()
        finally:
            conn.close()
        self._send_json({"ok": True, "api_key_set": bool(api_key), "mailto": mailto}, HTTPStatus.OK)

    def _handle_get_autoscan_config(self) -> None:
        cfg = get_autoscan_config(self.db_path)
        self._send_json(cfg, HTTPStatus.OK)

    def _handle_post_autoscan_config(self) -> None:
        body = self._read_json_body()
        if body is None:
            return

        current = get_autoscan_config(self.db_path)
        enabled = _parse_bool_setting(str(body.get("enabled", current["enabled"])), default=bool(current["enabled"]))
        push_new = _parse_bool_setting(str(body.get("push_new", current["push_new"])), default=bool(current["push_new"]))
        try:
            interval_seconds = int(body.get("interval_seconds", current["interval_seconds"]))
            lookback_days = int(body.get("lookback_days", current["lookback_days"]))
            max_pages_per_target = int(body.get("max_pages_per_target", current["max_pages_per_target"]))
        except (TypeError, ValueError):
            self._send_json(
                {"error": "interval_seconds/lookback_days/max_pages_per_target must be integer"},
                HTTPStatus.BAD_REQUEST,
            )
            return
        if interval_seconds <= 0 or lookback_days <= 0 or max_pages_per_target <= 0:
            self._send_json(
                {"error": "interval_seconds/lookback_days/max_pages_per_target must be > 0"},
                HTTPStatus.BAD_REQUEST,
            )
            return
        cfg = set_autoscan_config(
            self.db_path,
            enabled=bool(enabled),
            interval_seconds=interval_seconds,
            lookback_days=lookback_days,
            max_pages_per_target=max_pages_per_target,
            push_new=bool(push_new),
        )
        self._send_json({"ok": True, **cfg}, HTTPStatus.OK)

    def _normalize_doi(self, value: str) -> str | None:
        text = str(value).strip().lower()
        if text.startswith("https://doi.org/"):
            text = text[len("https://doi.org/") :]
        if text.startswith("doi:"):
            text = text[4:]
        text = text.strip()
        if DOI_REGEX.fullmatch(text):
            return text
        return None

    def _build_openalex_client(self) -> OpenAlexClient:
        conn = connect(self.db_path)
        try:
            api_key = get_app_setting(conn, "openalex.api_key", default=None)
            mailto = get_app_setting(conn, "openalex.mailto", default=None)
        finally:
            conn.close()
        return OpenAlexClient(api_key=api_key, mailto=mailto)

    def _serialize_work(self, work: dict[str, Any]) -> dict[str, Any]:
        source_obj = ((work.get("primary_location") or {}).get("source") or {}) if isinstance(work, dict) else {}
        paper_id = canonical_work_id(work.get("id"))
        title = work.get("title") or work.get("display_name") or paper_id
        doi = str(work.get("doi") or "").replace("https://doi.org/", "")
        return {
            "paper_id": paper_id,
            "title": title,
            "doi": doi or None,
            "published_date": work.get("publication_date"),
            "journal": source_obj.get("display_name"),
            "cited_by_count": work.get("cited_by_count"),
        }

    def _handle_post_resolve_doi(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        doi = self._normalize_doi(str(body.get("doi", "")))
        if not doi:
            self._send_json({"error": "Invalid DOI"}, HTTPStatus.BAD_REQUEST)
            return
        save_watch = bool(body.get("save_watch", True))
        save_search = bool(body.get("save_search", True))

        client = self._build_openalex_client()
        work = client.get_work_by_doi(doi)
        if not work:
            self._send_json({"error": "Work not found for DOI"}, HTTPStatus.NOT_FOUND)
            return

        conn = connect(self.db_path)
        try:
            paper_id = upsert_work(conn, work, source="web-resolve-doi")
            if save_watch and paper_id:
                add_watch_target(
                    conn,
                    target_type="paper",
                    target_value=paper_id,
                    enabled=1,
                    note=f"web doi:{doi}",
                )
            if save_search:
                add_saved_search(
                    conn,
                    [doi],
                    {
                        "mode": "resolve-doi",
                        "found": 1,
                        "failed": 0,
                        "work": self._serialize_work(work),
                    },
                )
            conn.commit()
        finally:
            conn.close()

        self._send_json(
            {"ok": True, "saved_to_watch": save_watch, "saved_search": save_search, "work": self._serialize_work(work)},
            HTTPStatus.OK,
        )

    def _handle_post_resolve_dois(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        raw_dois = body.get("dois", [])
        if not isinstance(raw_dois, list):
            self._send_json({"error": "dois must be an array"}, HTTPStatus.BAD_REQUEST)
            return
        cleaned: list[str] = []
        for raw in raw_dois:
            doi = self._normalize_doi(str(raw))
            if doi:
                cleaned.append(doi)
        cleaned = list(dict.fromkeys(cleaned))
        if not cleaned:
            self._send_json({"error": "No valid DOI in request"}, HTTPStatus.BAD_REQUEST)
            return
        save_watch = bool(body.get("save_watch", True))
        save_search = bool(body.get("save_search", True))

        client = self._build_openalex_client()
        found: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        conn = connect(self.db_path)
        try:
            for doi in cleaned:
                try:
                    work = client.get_work_by_doi(doi)
                    if not work:
                        failed.append({"doi": doi, "error": "not found"})
                        continue
                    paper_id = upsert_work(conn, work, source="web-resolve-dois")
                    if save_watch and paper_id:
                        add_watch_target(
                            conn,
                            target_type="paper",
                            target_value=paper_id,
                            enabled=1,
                            note=f"web doi:{doi}",
                        )
                    found.append(self._serialize_work(work))
                except Exception as exc:
                    failed.append({"doi": doi, "error": str(exc)})
            if save_search:
                add_saved_search(
                    conn,
                    cleaned,
                    {
                        "mode": "resolve-dois",
                        "found": len(found),
                        "failed": len(failed),
                    },
                )
            conn.commit()
        finally:
            conn.close()

        self._send_json(
            {
                "ok": True,
                "save_watch": save_watch,
                "save_search": save_search,
                "requested": len(cleaned),
                "found": found,
                "failed": failed,
            },
            HTTPStatus.OK,
        )

    def _handle_get_saved_searches(self, query: str) -> None:
        params = parse_qs(query)
        raw_limit = params.get("limit", ["10"])[0]
        try:
            limit = int(raw_limit)
        except ValueError:
            self._send_json({"error": "limit must be integer"}, HTTPStatus.BAD_REQUEST)
            return
        if limit <= 0:
            self._send_json({"error": "limit must be > 0"}, HTTPStatus.BAD_REQUEST)
            return

        conn = connect(self.db_path)
        try:
            rows = list_saved_searches(conn, limit=limit)
        finally:
            conn.close()
        items: list[dict[str, Any]] = []
        for row in rows:
            try:
                doi_list = json.loads(row["doi_list"])
            except Exception:
                doi_list = []
            try:
                result_payload = json.loads(row["result_json"])
            except Exception:
                result_payload = {}
            items.append(
                {
                    "id": int(row["id"]),
                    "doi_list": doi_list if isinstance(doi_list, list) else [],
                    "result_summary": {
                        "found": result_payload.get("found"),
                        "failed": result_payload.get("failed"),
                        "mode": result_payload.get("mode"),
                    },
                    "created_at": row["created_at"],
                }
            )
        self._send_json({"items": items, "limit": limit}, HTTPStatus.OK)

    def _handle_post_related_works(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        doi = self._normalize_doi(str(body.get("doi", "")))
        if not doi:
            self._send_json({"error": "Invalid DOI"}, HTTPStatus.BAD_REQUEST)
            return

        try:
            max_references = max(1, int(body.get("max_references", 5)))
            max_citing = max(1, int(body.get("max_citing", 5)))
        except ValueError:
            self._send_json({"error": "max_references/max_citing must be integer"}, HTTPStatus.BAD_REQUEST)
            return
        save = bool(body.get("save", True))

        client = self._build_openalex_client()
        seed_work = client.get_work_by_doi(doi)
        if not seed_work:
            self._send_json({"error": "Seed DOI not found"}, HTTPStatus.NOT_FOUND)
            return
        seed_id = canonical_work_id(seed_work.get("id"))
        if not seed_id:
            self._send_json({"error": "Seed work has invalid id"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        references: list[dict[str, Any]] = []
        citing: list[dict[str, Any]] = []
        reference_ids: list[str] = []
        for raw in (seed_work.get("referenced_works") or []):
            rid = canonical_work_id(raw)
            if rid:
                reference_ids.append(rid)
            if len(reference_ids) >= max_references:
                break

        conn = connect(self.db_path)
        try:
            if save:
                upsert_work(conn, seed_work, source="web-related-seed")
                add_watch_target(
                    conn,
                    target_type="paper",
                    target_value=seed_id,
                    enabled=1,
                    note=f"web related seed doi:{doi}",
                )
            for rid in reference_ids:
                try:
                    ref_work = client.get_work_by_id(rid)
                except Exception:
                    continue
                references.append(self._serialize_work(ref_work))
                if save:
                    upsert_work(conn, ref_work, source="web-related-reference")
                    add_edge(conn, seed_id, rid, "references")

            cited_count = 0
            for work in client.iter_citing_works(seed_id, max_pages=3):
                citing.append(self._serialize_work(work))
                if save:
                    c_id = upsert_work(conn, work, source="web-related-citing")
                    if c_id:
                        add_edge(conn, c_id, seed_id, "cites")
                cited_count += 1
                if cited_count >= max_citing:
                    break
            if save:
                conn.commit()
        finally:
            conn.close()

        self._send_json(
            {
                "ok": True,
                "seed": self._serialize_work(seed_work),
                "references": references,
                "citing": citing,
                "save": save,
            },
            HTTPStatus.OK,
        )

    def _extract_topics(self, work: dict[str, Any], max_topics: int = 3) -> list[str]:
        concepts = work.get("concepts") or []
        scored: list[tuple[float, str]] = []
        for item in concepts:
            if not isinstance(item, dict):
                continue
            name = str(item.get("display_name") or "").strip()
            score = item.get("score")
            if not name:
                continue
            try:
                score_value = float(score) if score is not None else 0.0
            except (TypeError, ValueError):
                score_value = 0.0
            scored.append((score_value, name))
        scored.sort(key=lambda x: x[0], reverse=True)
        topics = [name for _, name in scored[:max_topics]]
        return topics

    def _handle_post_similar_works(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        doi = self._normalize_doi(str(body.get("doi", "")))
        if not doi:
            self._send_json({"error": "Invalid DOI"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            max_similar = max(1, int(body.get("max_similar", 8)))
        except ValueError:
            self._send_json({"error": "max_similar must be integer"}, HTTPStatus.BAD_REQUEST)
            return
        save = bool(body.get("save", True))

        client = self._build_openalex_client()
        seed_work = client.get_work_by_doi(doi)
        if not seed_work:
            self._send_json({"error": "Seed DOI not found"}, HTTPStatus.NOT_FOUND)
            return
        seed_id = canonical_work_id(seed_work.get("id"))
        if not seed_id:
            self._send_json({"error": "Seed work has invalid id"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        topics = self._extract_topics(seed_work, max_topics=3)
        if not topics:
            self._send_json({"error": "No concepts/topics found for seed paper"}, HTTPStatus.BAD_REQUEST)
            return
        query = " ".join(topics)

        similar: list[dict[str, Any]] = []
        seen: set[str] = set()
        for work in client.iter_works(filter_str="has_abstract:true", search=query, sort="cited_by_count:desc", max_pages=2):
            work_id = canonical_work_id(work.get("id"))
            if not work_id or work_id == seed_id or work_id in seen:
                continue
            similar.append(self._serialize_work(work))
            seen.add(work_id)
            if len(similar) >= max_similar:
                break

        if save:
            conn = connect(self.db_path)
            try:
                upsert_work(conn, seed_work, source="web-similar-seed")
                add_watch_target(
                    conn,
                    target_type="paper",
                    target_value=seed_id,
                    enabled=1,
                    note=f"web similar seed doi:{doi}",
                )
                for item in similar:
                    if not item.get("paper_id"):
                        continue
                    raw_work = {
                        "id": f"https://openalex.org/{item['paper_id']}",
                        "title": item.get("title"),
                        "doi": f"https://doi.org/{item['doi']}" if item.get("doi") else None,
                        "publication_date": item.get("published_date"),
                        "cited_by_count": item.get("cited_by_count"),
                        "primary_location": {"source": {"display_name": item.get("journal")}},
                    }
                    upsert_work(conn, raw_work, source="web-similar-result")
                    add_edge(conn, item["paper_id"], seed_id, "similar")
                conn.commit()
            finally:
                conn.close()

        self._send_json(
            {
                "ok": True,
                "seed": self._serialize_work(seed_work),
                "topics": topics,
                "similar": similar,
                "save": save,
            },
            HTTPStatus.OK,
        )

    def _handle_post_latest_scan(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        try:
            lookback_days = max(1, int(body.get("lookback_days", 30)))
            max_pages_per_target = int(body.get("max_pages_per_target", 1))
        except (TypeError, ValueError):
            self._send_json({"error": "lookback_days/max_pages_per_target must be integer"}, HTTPStatus.BAD_REQUEST)
            return
        if max_pages_per_target <= 0:
            self._send_json({"error": "max_pages_per_target must be > 0"}, HTTPStatus.BAD_REQUEST)
            return
        result = latest_scan_core(
            self.db_path,
            lookback_days=lookback_days,
            max_pages_per_target=max_pages_per_target,
        )
        self._send_json({"ok": True, **result}, HTTPStatus.OK)

    def _handle_get_webhook_targets(self) -> None:
        conn = connect(self.db_path)
        try:
            rows = list_notification_targets(conn, target_type="webhook", include_disabled=True, limit=20)
        finally:
            conn.close()
        items = [
            {
                "id": int(row["id"]),
                "target_type": row["target_type"],
                "target_value": row["target_value"],
                "enabled": int(row["enabled"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
        self._send_json({"items": items}, HTTPStatus.OK)

    def _handle_post_webhook_target(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        url = str(body.get("url", "")).strip()
        enabled = bool(body.get("enabled", True))
        if not (url.startswith("http://") or url.startswith("https://")):
            self._send_json({"error": "url must start with http:// or https://"}, HTTPStatus.BAD_REQUEST)
            return
        conn = connect(self.db_path)
        try:
            target_id = upsert_notification_target(
                conn,
                target_type="webhook",
                target_value=url,
                enabled=1 if enabled else 0,
            )
            conn.commit()
        finally:
            conn.close()
        self._send_json({"ok": True, "id": target_id, "url": url, "enabled": enabled}, HTTPStatus.OK)

    def _handle_post_push_new_alerts(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        try:
            limit = max(1, int(body.get("limit", 50)))
        except (TypeError, ValueError):
            self._send_json({"error": "limit must be integer"}, HTTPStatus.BAD_REQUEST)
            return
        result = push_new_alerts_core(self.db_path, limit=limit)
        self._send_json({"ok": True, **result}, HTTPStatus.OK)

    def _read_json_body(self) -> dict[str, Any] | None:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError:
            self._send_json({"error": "Invalid Content-Length"}, HTTPStatus.BAD_REQUEST)
            return None
        if length <= 0:
            self._send_json({"error": "Empty request body"}, HTTPStatus.BAD_REQUEST)
            return None
        body = self.rfile.read(length).decode("utf-8")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            self._send_json({"error": "Invalid JSON body"}, HTTPStatus.BAD_REQUEST)
            return None
        if not isinstance(parsed, dict):
            self._send_json({"error": "JSON body must be an object"}, HTTPStatus.BAD_REQUEST)
            return None
        return parsed

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        LOGGER.info("web access " + format, *args)


class PapermapHTTPServer(ThreadingHTTPServer):
    _autoscan_stop_event: threading.Event | None = None
    _autoscan_thread: threading.Thread | None = None

    def stop_autoscan_worker(self, timeout: float = 5.0) -> None:
        stop_event = self._autoscan_stop_event
        worker = self._autoscan_thread
        if stop_event is not None:
            stop_event.set()
        if worker is not None and worker.is_alive():
            worker.join(timeout=timeout)

    def server_close(self) -> None:  # noqa: D401
        self.stop_autoscan_worker()
        super().server_close()


def create_http_server(
    db_path: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    default_recent_runs: int = 20,
) -> PapermapHTTPServer:
    init_db(db_path)
    handler = type(
        "PapermapDashboardHandler",
        (DashboardHandler,),
        {"db_path": db_path, "default_recent_runs": int(default_recent_runs)},
    )
    server = PapermapHTTPServer((host, int(port)), handler)
    server._autoscan_stop_event = threading.Event()
    server._autoscan_thread = threading.Thread(
        target=_auto_scan_loop,
        args=(db_path, server._autoscan_stop_event),
        daemon=True,
        name="papermap-autoscan",
    )
    server._autoscan_thread.start()
    LOGGER.info("autoscan worker started host=%s port=%s", host, port)
    return server


def serve_web(
    db_path: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    default_recent_runs: int = 20,
) -> int:
    server = create_http_server(
        db_path,
        host=host,
        port=port,
        default_recent_runs=default_recent_runs,
    )
    real_host, real_port = server.server_address
    LOGGER.info("serve-web start host=%s port=%s db_path=%s", real_host, real_port, db_path)
    print(f"Papermap dashboard running at http://{real_host}:{real_port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("serve-web interrupted by user")
    finally:
        server.server_close()
        LOGGER.info("serve-web stopped host=%s port=%s", real_host, real_port)
    return 0
