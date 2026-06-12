"""Tests for the local UI (fresh-listings default view + arena tab + poll APIs)."""

import json
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer

import ui


class TestLoadFresh:
    def test_shape_and_age_cap(self):
        data = ui.load_fresh()
        assert "generated_at" in data and "rows" in data
        for r in data["rows"]:
            assert r["days_old"] is not None
            assert r["days_old"] <= ui.FRESH_MAX_AGE_DAYS

    def test_sorted_score_desc_nulls_last(self):
        rows = ui.load_fresh()["rows"]
        scores = [r["score_1000"] for r in rows]
        seen_null = False
        prev = None
        for s in scores:
            if s is None:
                seen_null = True
                continue
            assert not seen_null, "scored row after unscored row"
            if prev is not None:
                assert s <= prev
            prev = s

    def test_rows_have_links(self):
        for r in ui.load_fresh()["rows"][:20]:
            assert r["maps_url"].startswith("https://www.google.com/maps/search/")
            if r["url"]:
                assert r["url"].startswith("http")


class TestLoadRankings:
    def test_loads_latest_run_only(self):
        data = ui.load_rankings()
        assert "run_date" in data and "rows" in data
        if data["rows"]:
            # within each bracket, rows sorted by rank
            from collections import defaultdict
            by_bracket = defaultdict(list)
            for r in data["rows"]:
                by_bracket[r["bracket"]].append(r["rank"])
            for ranks in by_bracket.values():
                assert ranks == sorted(ranks)

    def test_legacy_csv_without_run_date_column(self, tmp_path, monkeypatch):
        # Older CSVs lack run_date — must not crash, just render empty state
        legacy = tmp_path / "arena_results.csv"
        legacy.write_text("rank,project_name\n1,Test Condo\n")
        monkeypatch.setattr(ui, "ARENA_CSV", str(legacy))
        data = ui.load_rankings()
        assert data == {"run_date": None, "rows": []}


class TestRenderIndex:
    def test_renders_both_views(self):
        page = ui.render_index()
        assert "Fresh Listings" in page
        assert "Arena rankings" in page
        assert "SCAN NOW" in page or "pollbar" in page

    def test_embeds_data(self):
        fresh = ui.load_fresh()
        page = ui.render_index()
        if fresh["rows"]:
            assert fresh["rows"][0]["project_name"] in page

    def test_escapes_html_in_names(self, monkeypatch):
        evil_fresh = {"generated_at": "2026-06-11", "max_age_days": 30, "rows": [{
            "id": "1", "project_name": "<script>alert(1)</script>", "beds": 2,
            "baths": 2, "sqft": 700, "price": 1000000, "psf": 1500.0,
            "district": "D14", "region": "", "tenure": "", "built_year": 2015,
            "floor_level": "", "mrt_info": "", "score_1000": 500,
            "agent_rating": "", "agent_eval_date": "", "scored_at": "",
            "first_seen": "2026-06-11", "days_old": 0, "price_trend": "new",
            "drop_pct": None, "times_seen": 1, "url": "https://x",
            "maps_url": "https://maps",
        }]}
        monkeypatch.setattr(ui, "load_fresh", lambda: evil_fresh)
        page = ui.render_index()
        # The critical inline-JSON vector: a literal "</script>" coming from
        # data must never appear unescaped — it would close the script block
        # and inject markup. The embed escapes "</" as "<\/".
        assert "alert(1)</script>" not in page   # unescaped form absent
        assert "alert(1)<\\/script>" in page     # escaped form present


class TestPollStatus:
    def test_shape(self):
        st = ui.poll_status()
        assert "running" in st and "auto" in st and "interval_mins" in st

    def test_idle_when_no_thread(self):
        assert ui.poll_status()["running"] is False


class TestHTTPServer:
    def setup_method(self):
        self.server = HTTPServer(("127.0.0.1", 0), ui.Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def teardown_method(self):
        self.server.shutdown()

    def _get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as resp:
            return resp.status, resp.read().decode()

    def _post(self, path):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_index_serves_html(self):
        status, body = self._get("/")
        assert status == 200
        assert "Fresh Listings" in body

    def test_api_fresh(self):
        status, body = self._get("/api/fresh")
        assert status == 200
        assert "rows" in json.loads(body)

    def test_api_rankings(self):
        status, body = self._get("/api/rankings")
        assert status == 200
        assert "rows" in json.loads(body)

    def test_api_poll_status(self):
        status, body = self._get("/api/poll-status")
        assert status == 200
        assert "running" in json.loads(body)

    def test_poll_busy_returns_409(self, monkeypatch):
        monkeypatch.setattr(ui, "trigger_poll", lambda: False)
        status, _ = self._post("/poll")
        assert status == 409

    def test_poll_accepted_returns_202(self, monkeypatch):
        monkeypatch.setattr(ui, "trigger_poll", lambda: True)  # no real scrape
        status, body = self._post("/poll")
        assert status == 202
        assert json.loads(body)["started"] is True

    def test_404(self):
        try:
            status, _ = self._get("/nope")
        except urllib.error.HTTPError as e:
            status = e.code
        assert status == 404
