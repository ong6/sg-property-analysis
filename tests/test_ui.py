"""Tests for the local rankings UI."""

import json
import threading
import urllib.request
from http.server import HTTPServer

import ui


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

    def test_rows_have_links(self):
        data = ui.load_rankings()
        for r in data["rows"][:20]:
            assert r["maps_url"].startswith("https://www.google.com/maps/search/")
            assert "%20" in r["maps_url"] or "+" in r["maps_url"]  # query encoded
            if r["url"]:
                assert r["url"].startswith("http")


class TestRenderIndex:
    def test_renders_rows(self):
        data = ui.load_rankings()
        page = ui.render_index(data)
        assert "Condo Arena Rankings" in page
        if data["rows"]:
            assert data["run_date"] in page
            # data embedded as JSON for client-side render
            assert data["rows"][0]["project_name"] in page

    def test_renders_empty_state(self):
        page = ui.render_index({"run_date": None, "rows": []})
        assert "No arena results yet" in page

    def test_escapes_html_in_names(self):
        evil = {"run_date": "2026-06-07", "rows": [{
            "bracket": "2BR",
            "rank": 1, "project_name": "<script>alert(1)</script>", "beds": 2,
            "elo": 1500, "record": "1-0-0", "win_rate_pct": 100, "on_frontier": False,
            "mmr": 1500.0, "score_1000": 500, "price": 1000000, "psf": 1500.0,
            "district": "D14", "sqft": 700, "built_year": 2015, "tenure": "",
            "mrt_info": "", "url": "https://x", "maps_url": "https://maps",
        }]}
        page = ui.render_index(evil)
        # The critical inline-JSON vector: a literal "</script>" coming from
        # data must never appear unescaped — it would close the script block
        # and inject markup. The embed escapes "</" as "<\/".
        assert "alert(1)</script>" not in page   # unescaped form absent
        assert "alert(1)<\\/script>" in page     # escaped form present

    def test_legacy_csv_without_run_date_column(self, tmp_path, monkeypatch):
        # Older CSVs lack run_date — must not crash, just render empty state
        legacy = tmp_path / "arena_results.csv"
        legacy.write_text("rank,project_name\n1,Test Condo\n")
        monkeypatch.setattr(ui, "ARENA_CSV", str(legacy))
        data = ui.load_rankings()
        assert data == {"run_date": None, "rows": []}


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

    def test_index_serves_html(self):
        status, body = self._get("/")
        assert status == 200
        assert "Condo Arena Rankings" in body

    def test_api_serves_json(self):
        status, body = self._get("/api/rankings")
        assert status == 200
        data = json.loads(body)
        assert "rows" in data

    def test_404(self):
        try:
            status, _ = self._get("/nope")
        except urllib.error.HTTPError as e:
            status = e.code
        assert status == 404
