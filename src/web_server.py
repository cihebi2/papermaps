from __future__ import annotations

import json
import logging
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

try:
    from .db_init import init_db
    from .storage import connect, get_app_setting, list_watch_targets, set_app_setting
except ImportError:
    from db_init import init_db
    from storage import connect, get_app_setting, list_watch_targets, set_app_setting

LOGGER = logging.getLogger(__name__)

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
    let timer = null;

    function esc(v) {
      if (v === null || v === undefined) return "-";
      return String(v).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
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
        .map(([key, label]) => `<div class="card"><div class="label">${label}</div><div class="value">${esc(counts[key] ?? 0)}</div></div>`)
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

    function startTimer() {
      if (timer) clearInterval(timer);
      if (autoRefresh.checked) {
        timer = setInterval(loadData, 15000);
      }
    }

    refreshBtn.addEventListener("click", loadData);
    saveSettingsBtn.addEventListener("click", saveOpenAlexSettings);
    autoRefresh.addEventListener("change", startTimer);
    recentRunsInput.addEventListener("change", loadData);
    loadOpenAlexSettings();
    loadData();
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


def create_http_server(
    db_path: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    default_recent_runs: int = 20,
) -> ThreadingHTTPServer:
    init_db(db_path)
    handler = type(
        "PapermapDashboardHandler",
        (DashboardHandler,),
        {"db_path": db_path, "default_recent_runs": int(default_recent_runs)},
    )
    return ThreadingHTTPServer((host, int(port)), handler)


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
