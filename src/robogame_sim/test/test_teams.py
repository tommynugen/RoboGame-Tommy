"""Team sign-up from phones: the QR code, the sign-up server and the
Teams tab."""
import hashlib
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from robogame_sim import app_log, gamemaster, qr, teams


def call(url, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request) as reply:
            return reply.status, reply.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def test_qr_code_known_answer():
    # The same modules, mask and all, as the python qrcode package makes
    # at level M (checked 2026-11-06, and against opencv's decoder).
    rows = qr.matrix("http://192.168.1.20:8767/")
    assert len(rows) == 25  # version 2
    bits = "".join("1" if dark else "0" for row in rows for dark in row)
    assert hashlib.sha256(bits.encode()).hexdigest() == (
        "62bdd97ffff2d8726e394b8b1c9b0eee231c1aeb616db9699c2b22deb44c2950")
    # Longer links take bigger versions; too long is refused.
    assert len(qr.matrix("http://192.168.100.200:8767/" * 3)) > 21
    with pytest.raises(ValueError):
        qr.matrix("x" * 300)
    assert qr.svg("hi").startswith("<svg")


def test_qr_check_bytes():
    # The standard's worked example (ISO/IEC 18004 annex I): 01234567 at
    # version 1-M.
    data = [0x10, 0x20, 0x0C, 0x56, 0x61, 0x80, 0xEC, 0x11, 0xEC, 0x11,
            0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11]
    assert qr.check_bytes(data, 10) == [
        0xA5, 0x24, 0xD4, 0xC1, 0xED, 0x36, 0xC7, 0x87, 0x2C, 0x55]


def test_sign_up_rules(tmp_path):
    published = []
    book = app_log.LogBook()
    store = teams.Teams(book=book, path=str(tmp_path / "teams.json"))
    store.publish = published.append
    team = store.sign_up({"name": "  Bolt  ", "points": {
        "red": 5, "blue": "3", "yellow": "", "green": 9}})
    assert team["name"] == "Bolt"
    assert team["points"] == {"red": 5, "blue": 3, "yellow": 0}
    message = json.loads(published[-1])
    assert message["teams"][0]["points"]["red"] == 5
    # The same phone edits its team; another phone cannot take the name.
    store.sign_up({"id": team["id"], "name": "Bolt",
                   "points": {"red": 1}})
    assert len(store.teams) == 1
    for bad in ({"name": "bolt", "points": {}}, {"name": "", "points": {}},
                {"name": "A", "points": {"red": 101}},
                {"name": "A", "points": {"red": "lots"}},
                {"name": "A", "points": 5}, {"name": "x" * 30,
                                             "points": {}}):
        with pytest.raises(ValueError):
            store.sign_up(bad)
    store.set_tokens("Red, green")
    assert store.tokens == ["red", "green"]
    assert store.teams[team["id"]]["points"] == {"red": 1, "green": 0}
    with pytest.raises(ValueError):
        store.set_tokens("red; drop")
    # Kept on disk for the next start.
    again = teams.Teams(path=str(tmp_path / "teams.json"))
    assert again.tokens == ["red", "green"]
    assert list(again.teams) == [team["id"]]
    assert any("Bolt" in line["text"] for line in book.lines)


def test_phone_sign_up_reaches_the_teams_tab():
    store = teams.Teams(port=0, host="127.0.0.1")
    store.serve()
    phone = f"http://127.0.0.1:{store.server.server_port}"
    app = ThreadingHTTPServer(("127.0.0.1", 0), gamemaster.handler(
        book=app_log.LogBook(), teams=store))
    threading.Thread(target=app.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{app.server_port}"
    try:
        status, page = call(phone + "/")
        assert status == 200 and b"Points per token" in page
        status, body = call(phone + "/api/signup")
        assert json.loads(body)["tokens"] == teams.TOKENS
        status, body = call(phone + "/api/signup", {
            "name": "Spark", "points": {"red": 5, "blue": 3, "yellow": 1}})
        assert status == 200
        key = json.loads(body)["id"]
        status, body = call(phone + f"/api/signup?id={key}")
        assert json.loads(body)["team"]["name"] == "Spark"
        status, body = call(phone + "/api/signup", {"name": "", "points": {}})
        assert status == 400 and b"name" in body
        # The phones reach nothing else of the app.
        assert call(phone + "/api/teams")[0] == 404
        assert call(phone + "/api/teams/clear", {})[0] == 404

        status, body = call(base + "/api/teams")
        state = json.loads(body)
        assert state["serving"]
        assert state["url"].startswith("http://127.0.0.1:")
        assert [t["name"] for t in state["teams"]] == ["Spark"]
        assert state["teams"][0]["points"] == {"red": 5, "blue": 3,
                                               "yellow": 1}
        status, svg = call(base + "/api/teams/qr.svg")
        assert status == 200 and svg.startswith(b"<svg")
        status, page = call(base + "/teams")
        assert status == 200 and b"Scan to sign up" in page
        with urllib.request.urlopen(base + "/") as reply:
            assert b'data-tab="teams"' in reply.read()
        status, body = call(base + "/api/teams/remove", {"id": key})
        assert status == 200 and json.loads(body)["teams"] == []
        assert call(base + "/api/teams/remove", {"id": key})[0] == 400
    finally:
        app.shutdown()
        store.stop()


def test_v2_zone_ruleset_scores(tmp_path):
    store = teams.Teams(path=str(tmp_path / "teams.json"))
    published = []
    store.publish = published.append
    v1 = store.sign_up({"name": "Old", "points": {"red": 5}})
    store.set_rules({"ruleset": "V2", "multipliers": "-1x, 0x, 2x"})
    assert store.multipliers == [-1, 0, 2]  # the phones' display order
    one = store.sign_up({"name": "One", "picks": {
        "zones": [2, -1, 0], "decoy": "blue", "double": "red"}})
    store.sign_up({"name": "Two", "picks": {
        "zones": ["0x", "2x", "-1x"], "decoy": "red", "double": "green"}})
    for bad in ({"zones": [2, 2], "decoy": "red", "double": "blue"},
                {"zones": [3, 0, 0], "decoy": "red", "double": "blue"},
                {"zones": [2, 0, -1], "decoy": "red", "double": "red"},
                {"zones": [2, 2, -1], "decoy": "red", "double": "blue"},
                {"zones": [2, 0, -1], "decoy": "pink", "double": "red"}):
        with pytest.raises(ValueError):
            store.sign_up({"name": "Bad", "picks": bad})
    # 2 red in zone 1, 1 blue in zone 2, zone 3 empty.
    store.set_counts({"1": {"red": 2}, "2": {"blue": "1"}, "3": {}})
    scores = {t["name"]: t["score"] for t in store.state()["teams"]}
    # One: red is 2x in a 2x zone (2*2*2), blue decoy in a -1x zone
    # (1*-1*-1). Two: red decoy in a 0x zone, blue (worth 1) in a 2x zone.
    assert scores == {"Old": None, "One": 9, "Two": 2}
    assert json.loads(published[-1])["teams"][1]["score"] == 9
    with pytest.raises(ValueError):
        store.set_counts({"1": {"red": -1}})
    for bad in ("lots", "2x, 0x", "2, 2, 0"):
        with pytest.raises(ValueError):
            store.set_rules({"multipliers": bad})
    # V1 still scores by points per colour; V2 picks are kept.
    store.set_rules({"ruleset": "V1"})
    assert store.state()["teams"][0]["score"] == 10
    assert store.teams[one["id"]]["picks"]["double"] == "red"
    again = teams.Teams(path=str(tmp_path / "teams.json"))
    assert again.multipliers == [-1, 0, 2]
    assert again.counts == {"1": {"red": 2}, "2": {"blue": 1}}
    assert again.teams[v1["id"]]["points"]["red"] == 5


def test_public_link_needs_its_code(tmp_path):
    """The Public link runs cloudflared (a stand-in here) and the QR code
    shows its address; requests through it need the join code."""
    fake = tmp_path / "cloudflared"
    fake.write_text("#!/bin/sh\necho 'INF |  https://five-test-words."
                    "trycloudflare.com  |' >&2\nexec sleep 30\n")
    fake.chmod(0o755)
    store = teams.Teams(port=0, host="127.0.0.1")
    store.cloudflared = "no-such-cloudflared"
    store.start_public()
    assert store.tunnel is None and "not installed" in store.public_note
    store.serve()
    store.port = store.server.server_port
    phone = f"http://127.0.0.1:{store.port}"

    def ask(path, tunnel=True):
        headers = {"Cf-Connecting-Ip": "203.0.113.5"} if tunnel else {}
        request = urllib.request.Request(phone + path, headers=headers)
        try:
            with urllib.request.urlopen(request) as reply:
                return reply.status
        except urllib.error.HTTPError as error:
            return error.code

    try:
        store.cloudflared = str(fake)
        store.start_public()
        for _ in range(100):
            if store.public:
                break
            threading.Event().wait(0.05)
        state = store.state()
        assert state["public"] and state["public_up"]
        code = store.code
        assert state["url"] == ("https://five-test-words.trycloudflare.com"
                                f"/?code={code}")
        assert ask(f"/?code={code}") == 200
        assert ask(f"/api/signup?code={code}") == 200
        assert ask("/") == 403 and ask("/api/signup?code=nope") == 403
        assert ask("/api/signup", tunnel=False) == 200  # same Wi-Fi
        store.stop_public()
        assert store.state()["url"].startswith("http://127.0.0.1:")
        assert ask(f"/api/signup?code={code}") == 403
    finally:
        store.stop()
