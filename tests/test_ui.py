"""Tests for the local UI (fresh-listings default view + arena tab + poll APIs)."""

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from http.server import HTTPServer

import config
import dashboard
import ui


def _rec(rid, days_ago=0, **kw):
    """Minimal listings_db-shaped record, fresh by default."""
    d = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    rec = {
        "id": rid, "project_name": "Merge Test Condo", "beds": 2, "baths": 2,
        "sqft": 700, "price": 1500000, "psf": 2143.0, "district": "D14",
        "status": "active", "first_seen": d, "url": f"https://pg/{rid}",
        "score_1000": 600, "score_version": config.score_version(),
    }
    rec.update(kw)
    rec.setdefault("price_history",
                   [{"date": d, "price": rec["price"], "psf": rec.get("psf")}])
    return rec


def _patch_db(monkeypatch, recs):
    monkeypatch.setattr(ui.listings_db, "load_db",
                        lambda: {"listings": {r["id"]: r for r in recs}})
    monkeypatch.setattr(ui, "_eval_lookup", lambda: {})


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


class TestDedup:
    """v3.9 ×N dedup, hardened: price out of the key, floor in, honest age."""

    def test_same_unit_two_agents_merges_lowest_ask_oldest_seen(self, monkeypatch):
        _patch_db(monkeypatch, [
            _rec("a", days_ago=10, price=1520000),
            _rec("b", days_ago=0, price=1500000, sqft=705),  # ±1% sqft band
        ])
        rows = ui.load_fresh()["rows"]
        assert len(rows) == 1
        r = rows[0]
        assert r["dup_count"] == 2
        assert r["price"] == 1500000              # lowest current ask shown
        assert r["ask_min"] == 1500000 and r["ask_max"] == 1520000
        # min(first_seen): must NOT look permanently NEW / pass the 3-day filter
        assert r["days_old"] == 10
        assert r["first_seen"] == (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")

    def test_distinct_known_floors_do_not_merge(self, monkeypatch):
        _patch_db(monkeypatch, [
            _rec("hi", floor_level="High Floor"),
            _rec("lo", floor_level="Low Floor"),
            _rec("uk", floor_level=""),  # unknown floor merges into a cluster
        ])
        rows = ui.load_fresh()["rows"]
        assert len(rows) == 2
        assert sorted(r["dup_count"] for r in rows) == [1, 2]

    def test_cross_agent_drop_badge(self, monkeypatch):
        # Agent A asked 1.6M for 20 days; agent B lists the same unit at 1.45M.
        # Per-copy histories are flat — only the union shows the drop.
        _patch_db(monkeypatch, [
            _rec("a", days_ago=20, price=1600000),
            _rec("b", days_ago=2, price=1450000),
        ])
        rows = ui.load_fresh()["rows"]
        assert len(rows) == 1
        r = rows[0]
        assert r["price"] == 1450000
        assert r["drop_pct"] == -9.4              # vs the union peak ask
        assert r["price_trend"] == "dropped"

    def test_unit_group_key_preferred_when_present(self, monkeypatch):
        # Ingestion-stamped identity wins over the computed key: these two
        # would never band-merge on sqft (700 vs 900).
        _patch_db(monkeypatch, [
            _rec("a", unit_group="g1", sqft=700),
            _rec("b", unit_group="g1", sqft=900),
        ])
        assert len(ui.load_fresh()["rows"]) == 1

    def test_v_shaped_single_listing_exports_peak_drop(self, monkeypatch):
        # first-vs-last flat, peak-vs-now a real drop: the data contract the
        # client-side hasDrop() badge relies on (drop_pct present, trend flat).
        d = lambda n: (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")
        _patch_db(monkeypatch, [_rec("v", days_ago=9, price=1500000, price_history=[
            {"date": d(9), "price": 1500000}, {"date": d(5), "price": 1700000},
            {"date": d(1), "price": 1500000}])])
        r = ui.load_fresh()["rows"][0]
        assert r["price_trend"] == "flat"
        assert r["drop_pct"] == -11.8


class TestLivabilityGuardInUI:
    def test_one_malformed_record_does_not_500_the_view(self, monkeypatch):
        def boom(rec):
            raise ValueError("malformed mrt_info")
        monkeypatch.setattr(ui, "score_livability", boom)
        _patch_db(monkeypatch, [_rec("a")])
        rows = ui.load_fresh()["rows"]
        assert len(rows) == 1
        assert rows[0]["livability"] is None
        assert rows[0]["liv_why"] == "no signals"


class TestScoreVintage:
    def test_stale_vintage_flagged(self, monkeypatch):
        _patch_db(monkeypatch, [
            _rec("old", score_version="0.0+dead", sqft=700),
            _rec("cur", project_name="Other Condo", sqft=900),
            _rec("unscored", project_name="Third Condo", sqft=1100,
                 score_1000=None, score_version=None),
        ])
        by_id = {r["id"]: r for r in ui.load_fresh()["rows"]}
        assert by_id["old"]["score_stale"] is True
        assert by_id["cur"]["score_stale"] is False
        assert by_id["unscored"]["score_stale"] is False  # unscored ≠ stale-scored

    def test_poll_status_score_version_histogram(self, monkeypatch):
        _patch_db(monkeypatch, [
            _rec("a", score_version="0.0+dead"),
            _rec("b", project_name="Other", sqft=900),
            _rec("c", project_name="Pre", sqft=1100, score_version=None),
        ])
        monkeypatch.setattr(ui, "_SV_HIST_CACHE", {"at": 0.0, "hist": None})
        st = ui.poll_status()
        assert st["current_score_version"] == config.score_version()
        assert st["score_versions"] == {
            "0.0+dead": 1, config.score_version(): 1, "pre-stamp": 1}
        assert st["process_age_s"] >= 0

    def test_poll_status_survives_null_last_run(self, monkeypatch):
        monkeypatch.setattr(ui.poller, "load_poll_state", lambda: {"last_run": None})
        st = ui.poll_status()  # used to raise TypeError in _last_run_age_s
        assert st["last_age_s"] is None


class TestEscaping:
    def test_client_esc_escapes_quotes(self):
        # esc() output lands inside double-quoted HTML attributes — the old
        # textContent/innerHTML div trick left quotes alone.
        assert 'replace(/"/g, "&quot;")' in ui._PAGE
        assert "&#39;" in ui._PAGE
        assert 'document.createElement("div")' not in ui._PAGE

    def test_dashboard_escaper_covers_quotes(self):
        assert dashboard._e('a"b\'c<d') == "a&quot;b&#x27;c&lt;d"


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

    def test_footer_discloses_heuristic_and_config_version(self):
        page = ui.render_index()
        assert config.score_version() in page          # score-vintage visible
        assert "own-stay <b>heuristic</b>" in page     # not hover-only anymore
        assert "never folded into the backtested MMR" in page

    def test_score_vintage_chip_wired(self):
        # the v! chip renderer and its tooltip ship with the page
        assert "scored under an older config" in ui._PAGE
        assert "vChip(r.score_stale)" in ui._PAGE

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

    def _post(self, path, headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     method="POST", headers=headers or {})
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
        status, _ = self._post("/poll", headers={"X-Csrf-Token": ui.CSRF_TOKEN})
        assert status == 409

    def test_poll_accepted_returns_202(self, monkeypatch):
        monkeypatch.setattr(ui, "trigger_poll", lambda: True)  # no real scrape
        status, body = self._post("/poll", headers={"X-Csrf-Token": ui.CSRF_TOKEN})
        assert status == 202
        assert json.loads(body)["started"] is True

    # --- CSRF trust boundary (audit #13) ---
    def test_poll_without_token_403(self, monkeypatch):
        monkeypatch.setattr(ui, "trigger_poll", lambda: True)
        status, _ = self._post("/poll")
        assert status == 403

    def test_poll_with_wrong_token_403(self, monkeypatch):
        monkeypatch.setattr(ui, "trigger_poll", lambda: True)
        status, _ = self._post("/poll", headers={"X-Csrf-Token": "f" * 32})
        assert status == 403

    def test_poll_cross_origin_403(self, monkeypatch):
        # Right token but a foreign Origin (e.g. a leaked token replayed
        # cross-site) is still rejected.
        monkeypatch.setattr(ui, "trigger_poll", lambda: True)
        status, _ = self._post("/poll", headers={
            "X-Csrf-Token": ui.CSRF_TOKEN, "Origin": "http://evil.example"})
        assert status == 403

    def test_poll_same_origin_with_token_ok(self, monkeypatch):
        monkeypatch.setattr(ui, "trigger_poll", lambda: True)
        status, _ = self._post("/poll", headers={
            "X-Csrf-Token": ui.CSRF_TOKEN,
            "Origin": f"http://127.0.0.1:{self.port}"})
        assert status == 202

    def test_page_embeds_csrf_token(self):
        status, body = self._get("/")
        assert status == 200
        assert ui.CSRF_TOKEN in body

    def test_404(self):
        try:
            status, _ = self._get("/nope")
        except urllib.error.HTTPError as e:
            status = e.code
        assert status == 404


class TestDashboardServer:
    """Dashboard trust boundary: POST /analyze spawns an agent — token-gated."""

    def setup_method(self):
        self.server = HTTPServer(("127.0.0.1", 0), dashboard.Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def teardown_method(self):
        self.server.shutdown()

    def _post(self, path, headers=None, data=b""):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     method="POST", headers=headers or {}, data=data)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_analyze_without_token_403(self, monkeypatch):
        monkeypatch.setattr(dashboard, "start_claude_analysis",
                            lambda name: (True, {"slug": "x", "log": "y"}))
        status, _ = self._post("/analyze", data=b"name=Test+Condo")
        assert status == 403

    def test_analyze_cross_origin_403(self, monkeypatch):
        monkeypatch.setattr(dashboard, "start_claude_analysis",
                            lambda name: (True, {"slug": "x", "log": "y"}))
        status, _ = self._post("/analyze", data=b"name=Test+Condo", headers={
            "X-Csrf-Token": dashboard.CSRF_TOKEN, "Origin": "http://evil.example"})
        assert status == 403

    def test_analyze_with_token_ok(self, monkeypatch):
        monkeypatch.setattr(dashboard, "start_claude_analysis",
                            lambda name: (True, {"slug": "x", "log": "y"}))
        status, body = self._post("/analyze", data=b"name=Test+Condo",
                                  headers={"X-Csrf-Token": dashboard.CSRF_TOKEN})
        assert status == 200
        assert json.loads(body)["slug"] == "x"

    def test_spawn_cmd_is_scoped_not_bypass(self):
        # The claude invocation must not use --permission-mode bypassPermissions
        # (audit #13) — scoped --allowedTools instead.
        import inspect
        src = inspect.getsource(dashboard.start_claude_analysis)
        assert '"--permission-mode"' not in src
        assert '"--allowedTools"' in src

    def test_weights_read_from_config(self):
        rows = {n: w for n, w, _ in dashboard.config_weights()}
        assert f"{config.MMR_APPRECIATION_SLOPE:g} pts/pp" in rows["appreciation"]
        assert f"±{config.MMR_RELVALUE_CAP:g}" in rows["age_value (cheap-for-age vs district)"]
        assert f"{config.MMR_TXN_VOLUME_WEIGHT:g}·tanh" in rows["txn_volume (liquidity)"]
