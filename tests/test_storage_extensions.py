from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.db_init import init_db
from src.storage import (
    add_alert,
    add_saved_search,
    connect,
    get_app_setting,
    list_alerts,
    list_notification_targets,
    list_saved_searches,
    mark_alert_pushed,
    set_app_setting,
    upsert_notification_target,
)


class TestStorageExtensions(unittest.TestCase):
    def test_settings_saved_search_and_notification_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "ext.db"
            init_db(db_path)

            conn = connect(db_path)
            try:
                set_app_setting(conn, "openalex.api_key", "demo-key")
                self.assertEqual(get_app_setting(conn, "openalex.api_key"), "demo-key")

                search_id = add_saved_search(
                    conn,
                    ["10.1000/abc", "10.1000/def"],
                    {"works": [{"id": "W1"}, {"id": "W2"}]},
                )
                self.assertGreater(search_id, 0)
                rows = list_saved_searches(conn, limit=5)
                self.assertEqual(len(rows), 1)
                self.assertEqual(json.loads(rows[0]["doi_list"]), ["10.1000/abc", "10.1000/def"])

                nid = upsert_notification_target(
                    conn,
                    target_type="webhook",
                    target_value="http://127.0.0.1:9000/hook",
                    enabled=1,
                )
                self.assertGreater(nid, 0)
                targets = list_notification_targets(conn, target_type="webhook", include_disabled=True, limit=5)
                self.assertEqual(len(targets), 1)
                self.assertEqual(targets[0]["target_value"], "http://127.0.0.1:9000/hook")
            finally:
                conn.close()

    def test_alert_dedup_and_push_mark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "alerts.db"
            init_db(db_path)

            conn = connect(db_path)
            try:
                first_id = add_alert(
                    conn,
                    watch_target_id=1,
                    paper_id="W123",
                    alert_type="new_citation",
                    payload_json='{"title":"abc"}',
                )
                second_id = add_alert(
                    conn,
                    watch_target_id=1,
                    paper_id="W123",
                    alert_type="new_citation",
                    payload_json='{"title":"abc"}',
                )
                self.assertGreater(first_id, 0)
                self.assertEqual(first_id, second_id)

                rows = list_alerts(conn, status="new", limit=10)
                self.assertEqual(len(rows), 1)
                changed = mark_alert_pushed(conn, first_id)
                self.assertEqual(changed, 1)
                pushed = list_alerts(conn, status="pushed", limit=10)
                self.assertEqual(len(pushed), 1)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
