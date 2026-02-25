from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional

LOGGER = logging.getLogger(__name__)


def canonical_work_id(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if text.startswith("https://openalex.org/"):
        text = text.rsplit("/", 1)[-1]
    if text.startswith("W"):
        return text
    return None


def reconstruct_abstract(inverted_index: Optional[Dict[str, List[int]]]) -> str:
    if not inverted_index:
        return ""
    pos_to_word: Dict[int, str] = {}
    for word, positions in inverted_index.items():
        if not isinstance(positions, list):
            continue
        for pos in positions:
            if isinstance(pos, int):
                pos_to_word[pos] = word
    if not pos_to_word:
        return ""
    return " ".join(pos_to_word[i] for i in sorted(pos_to_word.keys())).strip()


class OpenAlexClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        mailto: str | None = None,
        base_url: str = "https://api.openalex.org",
        per_page: int = 200,
        sleep_s: float = 0.1,
        timeout_s: int = 30,
        max_retries: int = 3,
    ) -> None:
        self.api_key = (api_key or "").strip() or None
        self.mailto = (mailto or "").strip() or None
        self.base_url = base_url.rstrip("/")
        self.per_page = max(1, min(int(per_page), 200))
        self.sleep_s = max(0.0, float(sleep_s))
        self.timeout_s = int(timeout_s)
        self.max_retries = max(1, int(max_retries))

    def _request_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        merged_params: dict[str, Any] = {}
        if params:
            merged_params.update(params)
        if self.mailto and "mailto" not in merged_params:
            merged_params["mailto"] = self.mailto
        if self.api_key and "api_key" not in merged_params:
            merged_params["api_key"] = self.api_key

        query = urllib.parse.urlencode(merged_params, doseq=True)
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"

        headers = {
            "Accept": "application/json",
            "User-Agent": f"papermaps/0.2 (mailto:{self.mailto or 'unknown@example.com'})",
        }

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                req = urllib.request.Request(url=url, headers=headers, method="GET")
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    payload = resp.read().decode("utf-8")
                return json.loads(payload)
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                LOGGER.warning("openalex request failed attempt=%s url=%s error=%s", attempt, url, exc)
                if attempt < self.max_retries:
                    time.sleep(min(2.0, self.sleep_s + attempt * 0.2))
                else:
                    break
        raise RuntimeError(f"OpenAlex request failed: {url}") from last_error

    def get_work_by_id(self, work_id: str) -> dict[str, Any]:
        wid = canonical_work_id(work_id)
        if not wid:
            raise ValueError(f"Invalid OpenAlex work id: {work_id}")
        return self._request_json(f"/works/{wid}")

    def get_work_by_doi(self, doi: str) -> dict[str, Any] | None:
        clean_doi = doi.strip().lower()
        if clean_doi.startswith("https://doi.org/"):
            clean_doi = clean_doi[len("https://doi.org/") :]
        if clean_doi.startswith("doi:"):
            clean_doi = clean_doi[4:]
        filter_value = f"doi:https://doi.org/{clean_doi}"
        data = self._request_json("/works", {"filter": filter_value, "per-page": 1})
        results = data.get("results", []) or []
        return results[0] if results else None

    def iter_works(
        self,
        *,
        filter_str: str,
        search: str | None = None,
        sort: str | None = None,
        max_pages: int | None = None,
    ) -> Iterable[dict[str, Any]]:
        page = 0
        cursor = "*"
        while True:
            if max_pages is not None and page >= max_pages:
                return
            params: dict[str, Any] = {"filter": filter_str, "per-page": self.per_page, "cursor": cursor}
            if search:
                params["search"] = search
            if sort:
                params["sort"] = sort
            data = self._request_json("/works", params=params)
            results = data.get("results", []) or []
            if not results:
                return
            for work in results:
                yield work
            cursor = (data.get("meta", {}) or {}).get("next_cursor")
            page += 1
            if not cursor:
                return
            if self.sleep_s > 0:
                time.sleep(self.sleep_s)

    def iter_citing_works(
        self,
        target_work_id: str,
        *,
        from_publication_date: str | None = None,
        max_pages: int | None = None,
    ) -> Iterable[dict[str, Any]]:
        wid = canonical_work_id(target_work_id)
        if not wid:
            raise ValueError(f"Invalid target work id: {target_work_id}")
        filter_parts = [f"cites:{wid}"]
        if from_publication_date:
            filter_parts.append(f"from_publication_date:{from_publication_date}")
        filter_str = ",".join(filter_parts)
        return self.iter_works(filter_str=filter_str, max_pages=max_pages)
