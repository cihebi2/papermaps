from __future__ import annotations

import json
import logging
import math
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
    from .openalex_client import OpenAlexClient, canonical_work_id, reconstruct_abstract
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
    from openalex_client import OpenAlexClient, canonical_work_id, reconstruct_abstract
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
SIM_TOKEN_REGEX = re.compile(r"[a-z0-9]+")
SIM_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "have",
    "in",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "our",
    "such",
    "that",
    "the",
    "their",
    "this",
    "to",
    "was",
    "were",
    "with",
    "we",
    "they",
    "these",
    "those",
    "using",
    "use",
    "used",
    "method",
    "methods",
    "results",
    "result",
    "study",
    "data",
}

def token_counts_for_similarity(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for tok in SIM_TOKEN_REGEX.findall((text or "").lower()):
        if len(tok) < 3:
            continue
        if tok in SIM_STOPWORDS:
            continue
        if tok.isdigit():
            continue
        counts[tok] = counts.get(tok, 0) + 1
    return counts


def _build_idf(doc_counts: list[dict[str, int]]) -> dict[str, float]:
    df: dict[str, int] = {}
    for counts in doc_counts:
        for tok in counts.keys():
            df[tok] = df.get(tok, 0) + 1
    n_docs = max(1, len(doc_counts))
    idf: dict[str, float] = {}
    for tok, freq in df.items():
        idf[tok] = math.log((n_docs + 1.0) / (freq + 1.0)) + 1.0
    return idf


def _tfidf_vector(counts: dict[str, int], idf: dict[str, float]) -> tuple[dict[str, float], float]:
    vec: dict[str, float] = {}
    norm2 = 0.0
    for tok, c in counts.items():
        w = math.log1p(float(c)) * float(idf.get(tok, 0.0))
        if w <= 0:
            continue
        vec[tok] = w
        norm2 += w * w
    return vec, math.sqrt(norm2)


def _cosine_similarity(a: dict[str, float], norm_a: float, b: dict[str, float], norm_b: float) -> float:
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
        norm_a, norm_b = norm_b, norm_a
    dot = 0.0
    for tok, w in a.items():
        wb = b.get(tok)
        if wb is not None:
            dot += w * wb
    return dot / (norm_a * norm_b)


def _top_shared_terms_by_weight(
    vec_a: dict[str, float],
    vec_b: dict[str, float],
    *,
    limit: int = 8,
) -> list[str]:
    if not vec_a or not vec_b:
        return []
    items: list[tuple[float, str]] = []
    for tok, wa in vec_a.items():
        wb = vec_b.get(tok)
        if wb is None:
            continue
        items.append((wa * wb, tok))
    items.sort(reverse=True)
    return [tok for _w, tok in items[: max(0, int(limit))]]

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
    /* Recursive Similarity "Loom" (graph visualization) */
    .rec-stage {
      border: 1px dashed rgba(16, 42, 67, 0.25);
      border-radius: 16px;
      padding: 14px;
      background:
        radial-gradient(circle at 12% 18%, rgba(20, 184, 166, 0.12), transparent 44%),
        radial-gradient(circle at 86% 28%, rgba(245, 158, 11, 0.14), transparent 42%),
        linear-gradient(180deg, rgba(255, 255, 255, 0.72), rgba(255, 255, 255, 0.40));
      box-shadow:
        0 14px 42px rgba(16, 42, 67, 0.10),
        inset 0 1px 0 rgba(255, 255, 255, 0.55);
      position: relative;
      min-height: 420px;
    }
    .rec-empty {
      height: 100%;
      border-radius: 14px;
      padding: 18px 18px 16px;
      border: 1px solid rgba(16, 42, 67, 0.10);
      background:
        repeating-linear-gradient(90deg, rgba(16, 42, 67, 0.06) 0, rgba(16, 42, 67, 0.06) 1px, transparent 1px, transparent 22px),
        repeating-linear-gradient(0deg, rgba(16, 42, 67, 0.05) 0, rgba(16, 42, 67, 0.05) 1px, transparent 1px, transparent 22px),
        linear-gradient(180deg, rgba(255, 255, 255, 0.75), rgba(255, 255, 255, 0.35));
    }
    .rec-empty-kicker { font-size: 12px; letter-spacing: 0.18em; text-transform: uppercase; color: rgba(16, 42, 67, 0.65); }
    .rec-empty-title { margin-top: 8px; font-size: 18px; font-weight: 800; letter-spacing: 0.01em; }
    .rec-empty-sub { margin-top: 6px; font-size: 13px; color: var(--muted); max-width: 64ch; }
    .rec-head {
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 10px;
      padding-bottom: 10px;
      border-bottom: 1px solid rgba(16, 42, 67, 0.10);
    }
    .rec-head h3 { margin: 0; font-size: 16px; letter-spacing: 0.01em; }
    .rec-meta { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; color: rgba(16, 42, 67, 0.72); font-size: 12px; }
    .rec-pill { padding: 4px 9px; border-radius: 999px; border: 1px solid rgba(16, 42, 67, 0.14); background: rgba(255, 255, 255, 0.55); }
    .rec-actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    .btn-ghost {
      height: 34px;
      padding: 0 10px;
      border-radius: 12px;
      border: 1px solid rgba(16, 42, 67, 0.18);
      background: rgba(255, 255, 255, 0.55);
      cursor: pointer;
      font: inherit;
      color: rgba(16, 42, 67, 0.88);
    }
    .btn-ghost:hover { background: rgba(255, 255, 255, 0.78); }
    .btn-ghost:active { transform: translateY(1px); }
    .rec-body { margin-top: 12px; display: grid; grid-template-columns: 290px 1fr; gap: 12px; align-items: start; }
    .rec-side {
      border: 1px solid rgba(16, 42, 67, 0.12);
      border-radius: 14px;
      padding: 12px;
      background: rgba(255, 255, 255, 0.55);
    }
    .rec-side .k { font-size: 12px; letter-spacing: 0.16em; text-transform: uppercase; color: rgba(16, 42, 67, 0.62); }
    .rec-detail { margin-top: 10px; font-size: 13px; color: rgba(16, 42, 67, 0.92); line-height: 1.4; }
    .rec-detail a { color: rgba(47, 107, 168, 0.95); text-decoration: none; border-bottom: 1px solid rgba(47, 107, 168, 0.35); }
    .rec-detail a:hover { border-bottom-color: rgba(47, 107, 168, 0.75); }
    .rec-help { margin-top: 10px; font-size: 12px; color: var(--muted); }
    .rec-loom {
      border: 1px solid rgba(16, 42, 67, 0.12);
      border-radius: 14px;
      background:
        radial-gradient(circle at 30% 18%, rgba(16, 185, 129, 0.10), transparent 44%),
        radial-gradient(circle at 70% 40%, rgba(251, 191, 36, 0.11), transparent 46%),
        repeating-linear-gradient(90deg, rgba(16, 42, 67, 0.06) 0, rgba(16, 42, 67, 0.06) 1px, transparent 1px, transparent 26px),
        repeating-linear-gradient(0deg, rgba(16, 42, 67, 0.05) 0, rgba(16, 42, 67, 0.05) 1px, transparent 1px, transparent 26px),
        rgba(255, 255, 255, 0.35);
      overflow: auto;
      height: 360px;
      position: relative;
    }
    .rec-loom-inner { position: relative; padding: 14px; min-height: 100%; }
    .rec-cols { position: relative; display: flex; align-items: flex-start; gap: 12px; z-index: 2; }
    .rec-col { min-width: 260px; max-width: 260px; }
    .rec-col-title {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 8px;
      margin: 0 0 10px;
      padding: 8px 10px;
      border-radius: 12px;
      border: 1px solid rgba(16, 42, 67, 0.14);
      background: rgba(255, 255, 255, 0.55);
      font-size: 13px;
      font-weight: 700;
    }
    .rec-col-title span { font-weight: 600; color: rgba(16, 42, 67, 0.65); font-size: 12px; }
    .rec-list { display: flex; flex-direction: column; gap: 10px; }
    .rec-node {
      border-radius: 14px;
      border: 1px solid rgba(16, 42, 67, 0.14);
      background: rgba(255, 255, 255, 0.72);
      padding: 10px 10px 10px 10px;
      cursor: pointer;
      box-shadow: 0 10px 22px rgba(16, 42, 67, 0.06);
      transform: translateZ(0);
      transition: transform 180ms ease, box-shadow 180ms ease, border-color 180ms ease;
    }
    .rec-node:hover {
      transform: translateY(-2px);
      box-shadow: 0 16px 34px rgba(16, 42, 67, 0.10);
      border-color: rgba(16, 42, 67, 0.24);
    }
    .rec-node.is-focus { outline: 2px solid rgba(20, 184, 166, 0.55); outline-offset: 2px; }
    .rec-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
    .rec-title { font-size: 13px; font-weight: 800; line-height: 1.18; letter-spacing: 0.01em; }
    .rec-mini { margin-top: 4px; font-size: 12px; color: rgba(16, 42, 67, 0.68); }
    .rec-badge {
      flex: 0 0 auto;
      min-width: 44px;
      height: 28px;
      padding: 0 8px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: 999px;
      border: 1px solid rgba(16, 42, 67, 0.14);
      background: rgba(255, 255, 255, 0.65);
      font-size: 12px;
      font-weight: 800;
      letter-spacing: 0.02em;
    }
    .rec-tags { margin-top: 8px; display: flex; gap: 6px; flex-wrap: wrap; }
    .rec-tag {
      padding: 3px 7px;
      border-radius: 999px;
      border: 1px solid rgba(16, 42, 67, 0.12);
      background: rgba(255, 255, 255, 0.50);
      font-size: 11px;
      color: rgba(16, 42, 67, 0.72);
      white-space: nowrap;
    }
    .rec-edges { position: absolute; inset: 0; z-index: 1; pointer-events: none; }
    .rec-edge { fill: none; stroke: rgba(16, 42, 67, 0.18); stroke-width: 1.2; opacity: 0.70; }
    .rec-edge.is-hot { opacity: 0.98; stroke-width: 1.8; }
    .rec-edge.is-dim { opacity: 0.18; }
    .rec-raw {
      margin-top: 12px;
      padding: 12px;
      border-radius: 14px;
      border: 1px solid rgba(16, 42, 67, 0.12);
      background: rgba(255, 255, 255, 0.60);
      overflow: auto;
      max-height: 220px;
      font-size: 12px;
    }
    .rec-spinner {
      width: 18px;
      height: 18px;
      border-radius: 999px;
      border: 2px solid rgba(16, 42, 67, 0.18);
      border-top-color: rgba(20, 184, 166, 0.78);
      animation: recspin 900ms linear infinite;
      display: inline-block;
      vertical-align: -3px;
      margin-right: 8px;
    }
    @keyframes recspin { to { transform: rotate(360deg); } }
    @media (max-width: 700px) {
      h1 { font-size: 24px; }
      .value { font-size: 22px; }
      th, td { padding: 8px 9px; }
      .rec-stage { min-height: 360px; }
      .rec-body { grid-template-columns: 1fr; }
      .rec-col { min-width: 240px; max-width: 240px; }
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
      <h2>Recursive Similar References</h2>
      <div style="padding:12px 14px; display:grid; grid-template-columns: 170px 1fr; gap:10px; align-items:start;">
        <label for="recursiveDoiInput">Seed DOI</label>
        <input id="recursiveDoiInput" type="text" placeholder="10.xxxx/...">
        <label for="recursiveDepthInput">Depth</label>
        <input id="recursiveDepthInput" type="number" min="1" max="5" value="3">
        <label for="recursiveRefsInput">Refs per paper</label>
        <input id="recursiveRefsInput" type="number" min="1" max="50" value="10">
        <label for="recursiveTopKInput">Top K per paper</label>
        <input id="recursiveTopKInput" type="number" min="1" max="10" value="2">
        <label for="recursiveMinScoreInput">Min score</label>
        <input id="recursiveMinScoreInput" type="number" min="0" max="1" step="0.01" value="0.08">
        <span></span>
        <div>
          <label><input id="saveRecursiveData" type="checkbox" checked> Save to graph</label>
          <button id="recursiveBtn" type="button">Expand (depth)</button>
        </div>
        <label>Recursive Result</label>
        <div id="recursiveResult" class="rec-stage">
          <div class="rec-empty">
            <div class="rec-empty-kicker">Papermap Loom</div>
            <div class="rec-empty-title">No map yet.</div>
            <div class="rec-empty-sub">Run an expansion to weave a layered trail of similar references. Hover cards to see connections, click a card for details.</div>
          </div>
        </div>
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
    const recursiveDoiInput = document.getElementById("recursiveDoiInput");
    const recursiveDepthInput = document.getElementById("recursiveDepthInput");
    const recursiveRefsInput = document.getElementById("recursiveRefsInput");
    const recursiveTopKInput = document.getElementById("recursiveTopKInput");
    const recursiveMinScoreInput = document.getElementById("recursiveMinScoreInput");
    const saveRecursiveData = document.getElementById("saveRecursiveData");
    const recursiveBtn = document.getElementById("recursiveBtn");
    const recursiveResult = document.getElementById("recursiveResult");
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

    // ---- Recursive Loom (graph visualization) ----
    let recState = null;
    let recResizeBound = false;

    function clamp01(x) {
      const n = Number(x);
      if (Number.isNaN(n)) return 0;
      return Math.max(0, Math.min(1, n));
    }

    function fmtScore(x) {
      const n = Number(x);
      if (Number.isNaN(n)) return "-";
      return n.toFixed(2);
    }

    function scoreStroke(score) {
      // Warm-to-cool ink: low score = slate, high score = teal.
      const s = clamp01(score);
      const hue = 210 - Math.round(55 * s); // 210 -> 155
      const alpha = 0.35 + 0.55 * s;
      return `hsla(${hue}, 70%, 42%, ${alpha})`;
    }

    function firstNonEmpty(arr) {
      if (!arr || !arr.length) return null;
      for (const item of arr) {
        if (item) return item;
      }
      return null;
    }

    function bestIncomingEdge(node) {
      const incoming = (node && node.incoming) ? node.incoming : [];
      if (!incoming.length) return null;
      let best = incoming[0];
      for (const e of incoming) {
        if ((e.score || 0) > (best.score || 0)) best = e;
      }
      return best;
    }

    function renderRecursiveEmptyStage() {
      recursiveResult.innerHTML = `
        <div class="rec-empty">
          <div class="rec-empty-kicker">Papermap Loom</div>
          <div class="rec-empty-title">No map yet.</div>
          <div class="rec-empty-sub">Run an expansion to weave a layered trail of similar references. Hover cards to see connections, click a card for details.</div>
        </div>
      `;
      recState = null;
    }

    function renderRecursiveLoadingStage(metaText) {
      recursiveResult.innerHTML = `
        <div class="rec-empty">
          <div class="rec-empty-kicker"><span class="rec-spinner"></span>Building map</div>
          <div class="rec-empty-title">Fetching references and scoring similarity…</div>
          <div class="rec-empty-sub">${esc(metaText || "This can take a bit depending on depth and refs per paper. Keep this tab open.")}</div>
        </div>
      `;
    }

    function renderRecursiveErrorStage(message) {
      recursiveResult.innerHTML = `
        <div class="rec-empty">
          <div class="rec-empty-kicker" style="color: rgba(180, 35, 24, 0.90);">Error</div>
          <div class="rec-empty-title">Recursive expansion failed</div>
          <div class="rec-empty-sub">${esc(message || "Unknown error")}</div>
        </div>
      `;
    }

    function buildRecursiveGraphState(payload) {
      const seed = payload.seed || {};
      const seedId = seed.paper_id || "";
      const nodes = {};
      const edges = [];

      if (seedId) {
        nodes[seedId] = {
          id: seedId,
          work: seed,
          min_level: 0,
          best_score: 1.0,
          incoming: [],
          outgoing: [],
          shared_terms: [],
        };
      }

      let maxLevel = 0;
      const layers = payload.layers || [];
      for (const layer of layers) {
        const level = Number(layer.level || 0);
        if (level > maxLevel) maxLevel = level;
        const arr = layer.nodes || [];
        for (const n of arr) {
          const parent = n.parent_paper_id || "";
          const work = n.work || {};
          const id = work.paper_id || "";
          if (!id) continue;
          const score = (n.score !== undefined && n.score !== null) ? Number(n.score) : 0;
          const shared = Array.isArray(n.shared_terms) ? n.shared_terms : [];
          const edge = { from: parent, to: id, level, score: clamp01(score), shared_terms: shared };
          edges.push(edge);

          if (!nodes[id]) {
            nodes[id] = {
              id,
              work,
              min_level: level,
              best_score: clamp01(score),
              incoming: [],
              outgoing: [],
              shared_terms: [],
            };
          }
          const node = nodes[id];
          node.work = work;
          node.incoming.push(edge);
          if (level < node.min_level) node.min_level = level;
          if (clamp01(score) > node.best_score) node.best_score = clamp01(score);

          // Track "best" shared terms for quick scanning on cards.
          if (shared.length) {
            const bestEdge = bestIncomingEdge(node);
            const bestTerms = bestEdge ? bestEdge.shared_terms : [];
            node.shared_terms = bestTerms || [];
          }

          if (parent) {
            if (!nodes[parent]) {
              nodes[parent] = {
                id: parent,
                work: { paper_id: parent, title: parent },
                min_level: Math.max(0, level - 1),
                best_score: 0,
                incoming: [],
                outgoing: [],
                shared_terms: [],
              };
            }
            nodes[parent].outgoing.push(edge);
          }
        }
      }

      // Choose a single display level per paper: the earliest level it appears.
      const displayLevel = {};
      for (const [pid, node] of Object.entries(nodes)) {
        displayLevel[pid] = node.min_level || 0;
      }

      return { payload, seed, seedId, nodes, edges, maxLevel, displayLevel };
    }

    function renderRecursiveGraph(payload) {
      recState = buildRecursiveGraphState(payload);
      const seed = recState.seed || {};
      const seedId = recState.seedId || "";

      const metaBits = [];
      metaBits.push(`<span class="rec-pill">seed: ${esc(seedId || "-")}</span>`);
      metaBits.push(`<span class="rec-pill">depth: ${esc(payload.depth)}</span>`);
      metaBits.push(`<span class="rec-pill">selected: ${esc((recState.edges || []).length)}</span>`);
      metaBits.push(`<span class="rec-pill">fetched: ${esc(payload.fetch_count)}</span>`);
      metaBits.push(`<span class="rec-pill">elapsed: ${esc(payload.elapsed_ms)}ms</span>`);

      recursiveResult.innerHTML = `
        <div class="rec-head">
          <div>
            <h3>Recursive Similarity Loom</h3>
            <div class="rec-meta">${metaBits.join("")}</div>
          </div>
          <div class="rec-actions">
            <button id="recRedrawBtn" class="btn-ghost" type="button">Redraw edges</button>
            <button id="recRawBtn" class="btn-ghost" type="button">Raw JSON</button>
            <button id="recClearBtn" class="btn-ghost" type="button">Clear</button>
          </div>
        </div>
        <div class="rec-body">
          <div class="rec-side">
            <div class="k">Selection</div>
            <div id="recDetail" class="rec-detail">Click a paper card to inspect why it was selected.</div>
            <div class="rec-help">Tip: start with depth=3, refs=10–30, top_k=2–5, then raise min_score until the map reads clean.</div>
          </div>
          <div class="rec-loom" id="recLoom">
            <div class="rec-loom-inner" id="recLoomInner">
              <svg class="rec-edges" id="recEdges" aria-hidden="true"></svg>
              <div class="rec-cols" id="recCols"></div>
            </div>
          </div>
        </div>
        <pre id="recRaw" class="rec-raw" style="display:none;"></pre>
      `;

      const colsEl = document.getElementById("recCols");
      if (!colsEl) return;

      function mkCol(title, countText) {
        const col = document.createElement("div");
        col.className = "rec-col";
        const h = document.createElement("div");
        h.className = "rec-col-title";
        const t = document.createElement("div");
        t.textContent = title;
        const c = document.createElement("span");
        c.textContent = countText || "";
        h.appendChild(t);
        h.appendChild(c);
        const list = document.createElement("div");
        list.className = "rec-list";
        col.appendChild(h);
        col.appendChild(list);
        return { col, list };
      }

      function mkNodeCard(node, isSeed) {
        const w = node.work || {};
        const el = document.createElement("div");
        el.className = "rec-node";
        el.dataset.paperId = node.id;
        el.style.borderTop = `3px solid ${scoreStroke(isSeed ? 1.0 : node.best_score)}`;

        const top = document.createElement("div");
        top.className = "rec-top";
        const left = document.createElement("div");
        const ttl = document.createElement("div");
        ttl.className = "rec-title";
        ttl.textContent = w.title || w.paper_id || node.id;
        const mini = document.createElement("div");
        mini.className = "rec-mini";
        const bits = [];
        if (w.published_date) bits.push(String(w.published_date).slice(0, 4));
        if (w.journal) bits.push(w.journal);
        if (w.cited_by_count !== undefined && w.cited_by_count !== null) bits.push(`cited_by=${w.cited_by_count}`);
        mini.textContent = bits.join(" • ");
        left.appendChild(ttl);
        left.appendChild(mini);

        const badge = document.createElement("div");
        badge.className = "rec-badge";
        badge.textContent = isSeed ? "seed" : fmtScore(node.best_score);
        badge.title = isSeed ? "seed" : `score=${fmtScore(node.best_score)}`;

        top.appendChild(left);
        top.appendChild(badge);
        el.appendChild(top);

        const tags = document.createElement("div");
        tags.className = "rec-tags";
        const terms = (node.shared_terms || []).slice(0, 4);
        if (terms.length === 0) {
          const tag = document.createElement("div");
          tag.className = "rec-tag";
          tag.textContent = isSeed ? "starting point" : "no shared terms";
          tags.appendChild(tag);
        } else {
          for (const term of terms) {
            const tag = document.createElement("div");
            tag.className = "rec-tag";
            tag.textContent = term;
            tags.appendChild(tag);
          }
        }
        el.appendChild(tags);

        el.addEventListener("mouseenter", () => focusRecursiveNode(node.id));
        el.addEventListener("mouseleave", () => clearRecursiveFocus());
        el.addEventListener("click", () => showRecursiveDetail(node.id));
        return el;
      }

      // Build columns by earliest (min) level.
      const maxLevel = Math.max(0, recState.maxLevel || 0);
      for (let level = 0; level <= maxLevel; level++) {
        const title = level === 0 ? "Seed" : `L${level}`;
        const ids = [];
        for (const [pid, node] of Object.entries(recState.nodes)) {
          if (!pid) continue;
          if (pid === seedId && level !== 0) continue;
          if ((recState.displayLevel[pid] || 0) !== level) continue;
          if (level === 0 && pid !== seedId) continue;
          ids.push(pid);
        }
        const col = mkCol(title, `${ids.length}`);
        colsEl.appendChild(col.col);

        if (level === 0) {
          const seedNode = recState.nodes[seedId];
          if (seedNode) col.list.appendChild(mkNodeCard(seedNode, true));
          continue;
        }

        ids.sort((a, b) => {
          const na = recState.nodes[a] || {};
          const nb = recState.nodes[b] || {};
          const sa = Number(na.best_score || 0);
          const sb = Number(nb.best_score || 0);
          if (sb !== sa) return sb - sa;
          const ca = Number(((na.work || {}).cited_by_count) || 0);
          const cb = Number(((nb.work || {}).cited_by_count) || 0);
          return cb - ca;
        });
        for (const pid of ids) {
          const node = recState.nodes[pid];
          if (!node) continue;
          col.list.appendChild(mkNodeCard(node, false));
        }
      }

      // Actions
      const rawBtn = document.getElementById("recRawBtn");
      const clearBtn = document.getElementById("recClearBtn");
      const redrawBtn = document.getElementById("recRedrawBtn");
      const rawEl = document.getElementById("recRaw");
      if (rawEl) rawEl.textContent = JSON.stringify(payload, null, 2);
      if (rawBtn && rawEl) {
        rawBtn.addEventListener("click", () => {
          rawEl.style.display = rawEl.style.display === "none" ? "block" : "none";
        });
      }
      if (clearBtn) clearBtn.addEventListener("click", () => renderRecursiveEmptyStage());
      if (redrawBtn) redrawBtn.addEventListener("click", () => drawRecursiveEdges());

      if (!recResizeBound) {
        recResizeBound = true;
        window.addEventListener("resize", () => {
          if (recState) drawRecursiveEdges();
        });
      }

      // Initial draw after layout.
      setTimeout(() => drawRecursiveEdges(), 60);
      setTimeout(() => drawRecursiveEdges(), 240);
      showRecursiveDetail(seedId);
    }

    function focusRecursiveNode(paperId) {
      const stage = document.getElementById("recursiveResult");
      const svg = document.getElementById("recEdges");
      if (!stage || !svg || !recState) return;
      const node = recState.nodes[paperId];
      if (!node) return;
      const hot = {};
      hot[paperId] = true;
      for (const e of node.incoming || []) {
        if (e.from) hot[e.from] = true;
        if (e.to) hot[e.to] = true;
      }
      for (const e of node.outgoing || []) {
        if (e.from) hot[e.from] = true;
        if (e.to) hot[e.to] = true;
      }

      for (const card of stage.querySelectorAll(".rec-node")) {
        const id = card.dataset.paperId || "";
        card.classList.toggle("is-focus", Boolean(hot[id]));
      }
      for (const p of svg.querySelectorAll("path.rec-edge")) {
        const from = p.dataset.from || "";
        const to = p.dataset.to || "";
        const on = Boolean(hot[from] || hot[to]);
        p.classList.toggle("is-hot", on);
        p.classList.toggle("is-dim", !on);
      }
    }

    function clearRecursiveFocus() {
      const stage = document.getElementById("recursiveResult");
      const svg = document.getElementById("recEdges");
      if (!stage || !svg) return;
      for (const card of stage.querySelectorAll(".rec-node")) {
        card.classList.remove("is-focus");
      }
      for (const p of svg.querySelectorAll("path.rec-edge")) {
        p.classList.remove("is-hot");
        p.classList.remove("is-dim");
      }
    }

    function showRecursiveDetail(paperId) {
      const detail = document.getElementById("recDetail");
      if (!detail || !recState) return;
      const node = recState.nodes[paperId];
      if (!node) return;
      const w = node.work || {};
      const title = w.title || w.paper_id || node.id;

      detail.textContent = "";
      const h = document.createElement("div");
      h.style.fontWeight = "800";
      h.style.fontSize = "13px";
      h.textContent = title;
      detail.appendChild(h);

      const pillRow = document.createElement("div");
      pillRow.style.marginTop = "8px";
      const pillA = document.createElement("span");
      pillA.className = "rec-pill";
      pillA.textContent = `level: ${node.min_level || 0}`;
      const pillB = document.createElement("span");
      pillB.className = "rec-pill";
      pillB.style.marginLeft = "6px";
      pillB.textContent = paperId === (recState.seedId || "") ? "seed" : `score: ${fmtScore(node.best_score)}`;
      pillRow.appendChild(pillA);
      pillRow.appendChild(pillB);
      detail.appendChild(pillRow);

      const linkRow = document.createElement("div");
      linkRow.style.marginTop = "8px";
      const links = [];
      if (w.doi) {
        const a = document.createElement("a");
        a.href = `https://doi.org/${w.doi}`;
        a.target = "_blank";
        a.rel = "noopener";
        a.textContent = "DOI";
        links.push(a);
      }
      if (w.paper_id) {
        const a = document.createElement("a");
        a.href = `https://openalex.org/${w.paper_id}`;
        a.target = "_blank";
        a.rel = "noopener";
        a.textContent = "OpenAlex";
        links.push(a);
      }
      if (links.length) {
        for (let i = 0; i < links.length; i++) {
          if (i > 0) linkRow.appendChild(document.createTextNode(" • "));
          linkRow.appendChild(links[i]);
        }
        detail.appendChild(linkRow);
      }

      if (w.doi) {
        const btnRow = document.createElement("div");
        btnRow.style.marginTop = "10px";
        const btn = document.createElement("button");
        btn.className = "btn-ghost";
        btn.type = "button";
        btn.textContent = "Use DOI as seed";
        btn.addEventListener("click", () => {
          doiInput.value = w.doi;
          relatedDoiInput.value = w.doi;
          similarDoiInput.value = w.doi;
          recursiveDoiInput.value = w.doi;
        });
        btnRow.appendChild(btn);
        detail.appendChild(btnRow);
      }

      const incoming = (node.incoming || []).slice().sort((a, b) => (b.score || 0) - (a.score || 0));
      if (incoming.length) {
        const why = document.createElement("div");
        why.style.marginTop = "12px";
        why.style.fontWeight = "700";
        why.style.fontSize = "12px";
        why.style.letterSpacing = "0.12em";
        why.style.textTransform = "uppercase";
        why.style.color = "rgba(16, 42, 67, 0.62)";
        why.textContent = "Why selected";
        detail.appendChild(why);

        for (const e of incoming.slice(0, 6)) {
          const row = document.createElement("div");
          row.style.marginTop = "8px";
          row.style.paddingTop = "8px";
          row.style.borderTop = "1px solid rgba(16, 42, 67, 0.10)";
          const parentNode = recState.nodes[e.from] || null;
          const parentTitle = parentNode ? ((parentNode.work || {}).title || e.from) : (e.from || "-");
          const head = document.createElement("div");
          head.textContent = `score=${fmtScore(e.score)} • parent=${parentTitle}`;
          row.appendChild(head);
          const terms = (e.shared_terms || []).slice(0, 10).join(", ");
          if (terms) {
            const t = document.createElement("div");
            t.style.marginTop = "4px";
            t.style.color = "rgba(16, 42, 67, 0.72)";
            t.textContent = `shared_terms: ${terms}`;
            row.appendChild(t);
          }
          detail.appendChild(row);
        }
      }
    }

    function drawRecursiveEdges() {
      const svg = document.getElementById("recEdges");
      const inner = document.getElementById("recLoomInner");
      if (!svg || !inner || !recState) return;

      const nodeEls = {};
      for (const el of inner.querySelectorAll(".rec-node")) {
        const pid = el.dataset.paperId || "";
        if (pid) nodeEls[pid] = el;
      }

      const innerRect = inner.getBoundingClientRect();
      const w = Math.max(inner.scrollWidth, 200);
      const h = Math.max(inner.scrollHeight, 200);
      svg.setAttribute("width", String(w));
      svg.setAttribute("height", String(h));
      svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
      svg.innerHTML = "";

      function anchorLeft(el) {
        const r = el.getBoundingClientRect();
        return { x: (r.left - innerRect.left), y: (r.top - innerRect.top) + (r.height / 2) };
      }
      function anchorRight(el) {
        const r = el.getBoundingClientRect();
        return { x: (r.right - innerRect.left), y: (r.top - innerRect.top) + (r.height / 2) };
      }

      const ns = "http://www.w3.org/2000/svg";
      for (const e of recState.edges || []) {
        const fromEl = nodeEls[e.from || ""];
        const toEl = nodeEls[e.to || ""];
        if (!fromEl || !toEl) continue;
        const a = anchorRight(fromEl);
        const b = anchorLeft(toEl);
        const dx = Math.max(60, Math.min(150, Math.abs(b.x - a.x) * 0.45));
        const path = document.createElementNS(ns, "path");
        path.setAttribute("d", `M ${a.x} ${a.y} C ${a.x + dx} ${a.y}, ${b.x - dx} ${b.y}, ${b.x} ${b.y}`);
        path.setAttribute("class", "rec-edge");
        path.dataset.from = e.from || "";
        path.dataset.to = e.to || "";
        path.style.stroke = scoreStroke(e.score || 0);
        svg.appendChild(path);
      }
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

    async function expandRecursiveSimilarReferences() {
      const doi = recursiveDoiInput.value.trim();
      if (!doi) {
        renderRecursiveErrorStage("Please fill seed DOI.");
        return;
      }
      const depth = Math.max(1, Number(recursiveDepthInput.value || 3));
      const maxRefs = Math.max(1, Number(recursiveRefsInput.value || 10));
      const topK = Math.max(1, Number(recursiveTopKInput.value || 2));
      const minScore = Math.max(0, Number(recursiveMinScoreInput.value || 0.08));
      recursiveDepthInput.value = String(depth);
      recursiveRefsInput.value = String(maxRefs);
      recursiveTopKInput.value = String(topK);
      recursiveMinScoreInput.value = String(minScore);
      renderRecursiveLoadingStage(`seed=${doi} depth=${depth} refs=${maxRefs} top_k=${topK} min_score=${minScore}`);
      try {
        const res = await fetch("/api/works/recursive-similar-references", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            doi,
            depth,
            max_references: maxRefs,
            top_k: topK,
            min_score: minScore,
            save: Boolean(saveRecursiveData.checked),
          }),
        });
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.error || `HTTP ${res.status}`);
        renderRecursiveGraph(payload);
        await loadData();
      } catch (err) {
        renderRecursiveErrorStage(String(err));
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
    recursiveBtn.addEventListener("click", expandRecursiveSimilarReferences);
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
            if parsed.path == "/api/works/recursive-similar-references":
                self._handle_post_recursive_similar_references()
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

    def _work_text_for_similarity(self, work: dict[str, Any]) -> str:
        title = str(work.get("title") or work.get("display_name") or "").strip()
        abstract = reconstruct_abstract(work.get("abstract_inverted_index"))
        text = (title + "\n" + abstract).strip()
        return text or title

    def _concept_map(self, work: dict[str, Any]) -> dict[str, float]:
        concepts = work.get("concepts") or []
        if not isinstance(concepts, list):
            return {}
        out: dict[str, float] = {}
        for item in concepts:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("id") or "").strip()
            if not cid:
                continue
            try:
                score = float(item.get("score") or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            if score <= 0:
                continue
            out[cid] = score
        return out

    def _concept_overlap(self, seed_concepts: dict[str, float], cand_concepts: dict[str, float]) -> float:
        if not seed_concepts or not cand_concepts:
            return 0.0
        denom = sum(seed_concepts.values())
        if denom <= 0:
            return 0.0
        overlap = 0.0
        for cid, s in seed_concepts.items():
            c = cand_concepts.get(cid)
            if c is None:
                continue
            overlap += min(float(s), float(c))
        return max(0.0, min(1.0, overlap / denom))

    def _handle_post_recursive_similar_references(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        doi = self._normalize_doi(str(body.get("doi", "")))
        if not doi:
            self._send_json({"error": "Invalid DOI"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            depth = int(body.get("depth", 3))
            max_references = int(body.get("max_references", 10))
            top_k = int(body.get("top_k", 2))
            min_score = float(body.get("min_score", 0.08))
        except (TypeError, ValueError):
            self._send_json({"error": "depth/max_references/top_k must be integer; min_score must be number"}, HTTPStatus.BAD_REQUEST)
            return
        if depth <= 0 or depth > 5:
            self._send_json({"error": "depth must be 1-5"}, HTTPStatus.BAD_REQUEST)
            return
        if max_references <= 0 or max_references > 200:
            self._send_json({"error": "max_references must be 1-200"}, HTTPStatus.BAD_REQUEST)
            return
        if top_k <= 0 or top_k > 50:
            self._send_json({"error": "top_k must be 1-50"}, HTTPStatus.BAD_REQUEST)
            return
        if min_score < 0.0 or min_score > 1.0:
            self._send_json({"error": "min_score must be 0-1"}, HTTPStatus.BAD_REQUEST)
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

        LOGGER.info(
            "recursive-similar start seed=%s depth=%s max_references=%s top_k=%s min_score=%s save=%s",
            seed_id,
            depth,
            max_references,
            top_k,
            min_score,
            save,
        )
        t0 = time.time()
        seed_text = self._work_text_for_similarity(seed_work)
        seed_counts = token_counts_for_similarity(seed_text)
        if not seed_counts:
            self._send_json({"error": "Seed work has no usable abstract/title for similarity"}, HTTPStatus.BAD_REQUEST)
            return

        work_cache: dict[str, dict[str, Any]] = {seed_id: seed_work}
        count_cache: dict[str, dict[str, int]] = {seed_id: seed_counts}
        concept_cache: dict[str, dict[str, float]] = {seed_id: self._concept_map(seed_work)}
        frontier: list[str] = [seed_id]
        visited: set[str] = {seed_id}
        edges_selected: list[dict[str, Any]] = []
        layers: list[dict[str, Any]] = []
        fetch_count = 1

        select_fields = ",".join(
            [
                "id",
                "title",
                "display_name",
                "doi",
                "publication_date",
                "cited_by_count",
                "primary_location",
                "abstract_inverted_index",
                "referenced_works",
                "concepts",
            ]
        )

        for level in range(1, depth + 1):
            next_frontier: list[str] = []
            level_nodes: list[dict[str, Any]] = []
            for parent_id in frontier:
                parent_work = work_cache.get(parent_id)
                if not parent_work:
                    continue
                parent_text = self._work_text_for_similarity(parent_work)
                parent_counts = count_cache.get(parent_id)
                if parent_counts is None:
                    parent_counts = token_counts_for_similarity(parent_text)
                    count_cache[parent_id] = parent_counts
                parent_concepts = concept_cache.get(parent_id)
                if parent_concepts is None:
                    parent_concepts = self._concept_map(parent_work)
                    concept_cache[parent_id] = parent_concepts

                ref_list = parent_work.get("referenced_works") or []
                if not isinstance(ref_list, list):
                    continue

                ref_ids: list[str] = []
                considered = 0
                for raw in ref_list:
                    if considered >= max_references:
                        break
                    rid = canonical_work_id(str(raw))
                    if not rid:
                        continue
                    ref_ids.append(rid)
                    considered += 1

                missing = [rid for rid in ref_ids if rid not in work_cache]
                if missing:
                    try:
                        fetched = client.get_works_by_ids(missing, select=select_fields)
                        if fetched:
                            work_cache.update(fetched)
                            fetch_count += len(fetched)
                    except Exception as exc:
                        LOGGER.warning("recursive-similar bulk fetch failed count=%s error=%s", len(missing), exc)

                cand_counts_list: list[dict[str, int]] = []
                for rid in ref_ids:
                    work = work_cache.get(rid)
                    if not work:
                        continue
                    if rid not in count_cache:
                        count_cache[rid] = token_counts_for_similarity(self._work_text_for_similarity(work))
                    cand_counts_list.append(count_cache[rid])

                # Build IDF over seed + parent + candidates, then score candidates.
                idf = _build_idf([seed_counts, parent_counts] + cand_counts_list)
                seed_vec, seed_norm = _tfidf_vector(seed_counts, idf)
                parent_vec, parent_norm = _tfidf_vector(parent_counts, idf)
                seed_concepts = concept_cache.get(seed_id, {})

                scored: list[tuple[float, int, str, dict[str, Any], dict[str, float], float, float]] = []
                for rid in ref_ids:
                    work = work_cache.get(rid)
                    if not work:
                        continue
                    cand_counts = count_cache.get(rid, {})
                    cand_vec, cand_norm = _tfidf_vector(cand_counts, idf)
                    seed_sim = _cosine_similarity(seed_vec, seed_norm, cand_vec, cand_norm)
                    parent_sim = _cosine_similarity(parent_vec, parent_norm, cand_vec, cand_norm)
                    cand_concepts = concept_cache.get(rid)
                    if cand_concepts is None:
                        cand_concepts = self._concept_map(work)
                        concept_cache[rid] = cand_concepts
                    concept_sim = self._concept_overlap(seed_concepts, cand_concepts)
                    # Hybrid: keep global theme (seed) but allow local progression (parent).
                    text_sim = 0.7 * float(seed_sim) + 0.3 * float(parent_sim)
                    score = 0.85 * float(text_sim) + 0.15 * float(concept_sim)
                    cited = work.get("cited_by_count")
                    try:
                        cited_int = int(cited) if cited is not None else 0
                    except (TypeError, ValueError):
                        cited_int = 0
                    scored.append((float(score), cited_int, rid, work, cand_vec, float(seed_sim), float(parent_sim)))

                scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
                selected = [item for item in scored if item[0] >= min_score][:top_k]
                for score, _cited_int, rid, work, cand_vec, seed_sim, parent_sim in selected:
                    if rid not in visited:
                        visited.add(rid)
                        next_frontier.append(rid)
                    terms = _top_shared_terms_by_weight(seed_vec, cand_vec, limit=8)
                    edges_selected.append(
                        {
                            "level": level,
                            "parent_paper_id": parent_id,
                            "paper_id": rid,
                            "score": round(float(score), 6),
                            "seed_sim": round(float(seed_sim), 6),
                            "parent_sim": round(float(parent_sim), 6),
                            "shared_terms": terms,
                        }
                    )
                    level_nodes.append(
                        {
                            "level": level,
                            "parent_paper_id": parent_id,
                            "score": round(float(score), 4),
                            "shared_terms": terms,
                            "work": self._serialize_work(work),
                        }
                    )

            layers.append({"level": level, "nodes": level_nodes})
            frontier = next_frontier
            if not frontier:
                break

        if save:
            conn = connect(self.db_path)
            try:
                upsert_work(conn, seed_work, source="web-recursive-seed")
                add_watch_target(
                    conn,
                    target_type="paper",
                    target_value=seed_id,
                    enabled=1,
                    note=f"web recursive seed doi:{doi}",
                )
                saved_ids: set[str] = {seed_id}
                for edge in edges_selected:
                    saved_ids.add(str(edge["parent_paper_id"]))
                    saved_ids.add(str(edge["paper_id"]))
                for wid in saved_ids:
                    work = work_cache.get(wid)
                    if work:
                        upsert_work(conn, work, source="web-recursive-node")
                for edge in edges_selected:
                    parent_id = str(edge["parent_paper_id"])
                    rid = str(edge["paper_id"])
                    add_edge(conn, parent_id, rid, "references")
                    add_edge(conn, parent_id, rid, "ref_similar")
                conn.commit()
            finally:
                conn.close()

        elapsed_ms = int((time.time() - t0) * 1000)
        selected_total = 0
        for layer in layers:
            selected_total += len(layer.get("nodes") or [])
        LOGGER.info(
            "recursive-similar success seed=%s layers=%s selected=%s fetch_count=%s elapsed_ms=%s",
            seed_id,
            len(layers),
            selected_total,
            fetch_count,
            elapsed_ms,
        )
        self._send_json(
            {
                "ok": True,
                "seed": self._serialize_work(seed_work),
                "depth": depth,
                "max_references": max_references,
                "top_k": top_k,
                "min_score": min_score,
                "layers": layers,
                "save": save,
                "fetch_count": fetch_count,
                "elapsed_ms": elapsed_ms,
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
