"""Team sign-up from phones, for the Gamemaster's Teams tab.

The Teams tab shows a QR code. A phone that scans it opens the sign-up
page, served on every network the computer is on (port 8767), where a
team picks its name and how many points each token colour is worth to
it, say 5 for red, 3 for blue and 1 for yellow. Each sign-up is saved,
shown on the Teams tab and published on /game/teams (std_msgs/String,
JSON, latched) for any ROS node that keeps score:

    {"tokens": ["red", "blue", "yellow"],
     "teams": [{"name": "Bolt", "points": {"red": 5, "blue": 3,
                "yellow": 1}, "updated": 1730900000.0}]}

That is ruleset V1. In ruleset V2 a team instead gives each of the three
zones a different multiplier (from the Gamemaster's choices, 2x, 0x and -1x at
first, shown in the order the Gamemaster sets), picks one colour as its
decoy (worth -1 a token) and another as its 2x token (worth 2); every
other colour is worth 1. The Gamemaster enters how many tokens of each
colour sit in each zone, and each team's score is worked out from that:

    score = sum over zones and colours of count x token worth x multiplier

In V1 the score is the tokens' count times the team's points per colour,
zones aside. Scores go out on /game/teams too, with the ruleset.

Only the sign-up page and its one API are open to the network; the rest
of the Gamemaster stays on this computer. A phone keeps its team's id,
so scanning again edits the same team.

Phones off this computer's Wi-Fi (on mobile data, say) sign up through a
public link: the tab's "Public link" switch starts a Cloudflare quick
tunnel (cloudflared, free, no account) to the sign-up server, and the QR
code then shows its https://....trycloudflare.com address with a join
code. Requests that come through the tunnel need that code, and each
start makes a new one, so an old link stops working.
"""

import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from robogame_sim import qr

HERE = os.path.dirname(os.path.realpath(__file__))
TAB_PAGE = os.path.join(HERE, "teams.html")
SIGNUP_PAGE = os.path.join(HERE, "signup.html")
FILE_NAME = "teams.json"
TOPIC = "/game/teams"
PORT = 8767
TOKENS = ["red", "blue", "yellow"]
MAX_TEAMS = 64
MAX_NAME = 24  # characters
MAX_POINTS = 100  # a token's worth, at most
RULESETS = ("V1", "V2")
V2_COLOURS = ["red", "green", "blue", "yellow"]
MULTIPLIERS = [2, 0, -1]  # V2's zone multiplier choices, in display order
ZONES = ["1", "2", "3"]
DECOY, DOUBLE = -1, 2  # V2 token worths; any other colour is worth 1
MAX_MULTIPLIER = 10  # a multiplier choice is -10 to 10
MAX_COUNT = 99  # tokens of one colour in one zone, at most
COLOUR = re.compile(r"^[a-z]{3,20}$")  # a CSS colour name
HTML = "text/html; charset=utf-8"
# The address cloudflared prints once its quick tunnel is up.
TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def parse_multipliers(given, strict=False):
    """V2's multiplier choices, in display order, from a list or text like
    "2x, 0x, -1x". Bad ones raise ValueError if strict, else the
    defaults come back."""
    try:
        if isinstance(given, str):
            given = [part for part in re.split(r"[\s,]+", given) if part]
        if not isinstance(given, list):
            raise ValueError("multipliers should be a list")
        clean = []
        for value in given:
            text = str(value).strip().rstrip("xX")
            try:
                value = int(text)
            except ValueError:
                raise ValueError(f"{text!r} is not a whole number")
            if abs(value) > MAX_MULTIPLIER:
                raise ValueError(f"keep multipliers to -{MAX_MULTIPLIER} "
                                 f"to {MAX_MULTIPLIER}")
            if value not in clean:
                clean.append(value)
        if len(clean) < len(ZONES):
            raise ValueError(f"give at least {len(ZONES)} multipliers, one "
                             "for each zone")
        return clean
    except ValueError:
        if strict:
            raise
        return list(MULTIPLIERS)


def parse_colours(given, strict=False):
    """V2's token colours, at least two (a decoy and a 2x token)."""
    try:
        if isinstance(given, str):
            given = re.split(r"[\s,]+", given)
        if not isinstance(given, list):
            raise ValueError("colours should be a list")
        clean = []
        for colour in given:
            colour = str(colour).strip().lower()
            if not colour:
                continue
            if not COLOUR.match(colour):
                raise ValueError(f"{colour!r} is not a colour name")
            if colour not in clean:
                clean.append(colour)
        if len(clean) < 2:
            raise ValueError("give at least two colours: a decoy and a "
                             "2x token")
        return clean
    except ValueError:
        if strict:
            raise
        return list(V2_COLOURS)


def parse_counts(given, colours, strict=False):
    """Tokens per zone and colour, {zone: {colour: count}}, keeping only
    known zones and colours and leaving out zeros."""
    if not isinstance(given, dict):
        if strict:
            raise ValueError("counts should be {zone: {colour: count}}")
        return {}
    clean = {}
    for zone, row in given.items():
        zone = str(zone)
        if zone not in ZONES or not isinstance(row, dict):
            continue
        for colour, count in row.items():
            if colour not in colours:
                continue
            if isinstance(count, str) and count.strip() == "":
                count = 0
            try:
                count = int(count)
            except (TypeError, ValueError):
                if strict:
                    raise ValueError(f"zone {zone} {colour}: give a "
                                     "whole number")
                continue
            if not 0 <= count <= MAX_COUNT:
                if strict:
                    raise ValueError(f"zone {zone} {colour}: 0 to "
                                     f"{MAX_COUNT} tokens")
                continue
            if count:
                clean.setdefault(zone, {})[colour] = count
    return clean


def teams_file():
    return os.path.join(os.path.expanduser("~/.local/share/robogame_sim"),
                        FILE_NAME)


def lan_address():
    """This computer's address on its network, the one a phone on the same
    Wi-Fi reaches it at. Sends nothing: connecting a UDP socket only picks
    the way out."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("10.255.255.255", 1))
            return probe.getsockname()[0]
        except OSError:
            return "127.0.0.1"


class Teams:
    """The teams signed up and the token colours they give points to.
    path is the JSON file they are kept in, or None to keep them in
    memory only."""

    def __init__(self, book=None, path=None, port=PORT, host=None):
        self.book = book
        self.path = path
        self.port = port
        self.host = host
        self.lock = threading.Lock()
        self.tokens = list(TOKENS)
        self.ruleset = "V1"
        self.multipliers = list(MULTIPLIERS)
        self.v2_colours = list(V2_COLOURS)
        self.counts = {}  # zone: {colour: tokens there}
        # id: {"name", "points" (V1), "picks" (V2), "updated"}
        self.teams = {}
        self.publish = None
        self.server = None
        self.error = None
        # The public link: cloudflared's process, its address, the join code
        self.cloudflared = "cloudflared"
        self.tunnel = None
        self.public = None
        self.code = None
        self.public_note = ""
        self.load()

    def say(self, text, level="info"):
        if self.book is not None:
            self.book.add("teams", text, level)

    # Saving

    def load(self):
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path) as file:
                saved = json.load(file)
            self.tokens = [t for t in saved.get("tokens", TOKENS)
                           if isinstance(t, str) and COLOUR.match(t)]
            if saved.get("ruleset") in RULESETS:
                self.ruleset = saved["ruleset"]
            self.multipliers = parse_multipliers(
                saved.get("multipliers", MULTIPLIERS))
            self.v2_colours = parse_colours(
                saved.get("v2_colours", V2_COLOURS))
            self.counts = parse_counts(saved.get("counts", {}),
                                       self.all_colours())
            self.teams = {key: team for key, team in
                          saved.get("teams", {}).items()
                          if isinstance(team, dict) and "name" in team}
        except (OSError, ValueError, AttributeError) as error:
            self.say(f"Could not read {self.path}: {error}", "warn")

    def save(self):
        """Saves and publishes. Called with the lock held."""
        if self.path:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w") as file:
                json.dump({"tokens": self.tokens, "ruleset": self.ruleset,
                           "multipliers": self.multipliers,
                           "v2_colours": self.v2_colours,
                           "counts": self.counts, "teams": self.teams},
                          file, indent=1)
        if self.publish is not None:
            self.publish(json.dumps(self.message()))

    def message(self):
        return {"tokens": list(self.tokens), "ruleset": self.ruleset,
                "colours": self.colours(), "zones": list(ZONES),
                "multipliers": list(self.multipliers),
                "counts": self.counts,
                "teams": [{"name": team["name"],
                           "points": dict(team.get("points", {})),
                           "picks": team.get("picks"),
                           "score": self.score(team),
                           "updated": team["updated"]}
                          for team in self.listed()]}

    # Rules and scores

    def colours(self):
        """The token colours of the ruleset in use."""
        return list(self.tokens if self.ruleset == "V1" else self.v2_colours)

    def all_colours(self):
        return self.tokens + [c for c in self.v2_colours
                              if c not in self.tokens]

    def score(self, team):
        """The team's score from the tokens in the zones, by the ruleset
        in use; None for a team with no V2 picks under V2."""
        total = 0
        if self.ruleset == "V1":
            points = team.get("points", {})
            for zone in ZONES:
                for colour, count in self.counts.get(zone, {}).items():
                    total += count * points.get(colour, 0)
            return total
        picks = team.get("picks")
        if not picks:
            return None
        for index, zone in enumerate(ZONES):
            multiplier = picks["zones"][index]
            for colour, count in self.counts.get(zone, {}).items():
                if colour not in self.v2_colours:
                    continue
                worth = DECOY if colour == picks["decoy"] else \
                    DOUBLE if colour == picks["double"] else 1
                total += count * worth * multiplier
        return total

    def check_picks(self, given):
        """A V2 team's picks from a phone: {"zones": [three multipliers],
        "decoy": colour, "double": colour}."""
        if not isinstance(given, dict):
            raise ValueError("pick the zone multipliers and tokens")
        zones = given.get("zones")
        if not isinstance(zones, list) or len(zones) != len(ZONES):
            raise ValueError(f"give all {len(ZONES)} zones a multiplier")
        clean = []
        for number, value in zip(ZONES, zones):
            try:
                value = int(str(value).strip().rstrip("xX"))
            except ValueError:
                raise ValueError(f"pick a multiplier for zone {number}")
            if value not in self.multipliers:
                raise ValueError(f"zone {number}: pick one of " + ", ".join(
                    f"{m}x" for m in self.multipliers))
            if value in clean:
                raise ValueError(f"zone {number}: {value}x is already used; "
                                 "give each zone a different multiplier")
            clean.append(value)
        decoy, double = given.get("decoy"), given.get("double")
        for colour, what in ((decoy, "decoy"), (double, "2x token")):
            if colour not in self.v2_colours:
                raise ValueError(f"pick a colour for the {what}")
        if decoy == double:
            raise ValueError("the decoy and the 2x token must be "
                             "different colours")
        return {"zones": clean, "decoy": decoy, "double": double}

    def set_rules(self, body):
        """The Gamemaster's rules: {"ruleset", "multipliers" (V2's choices,
        in display order), "colours" (V2's token colours)}, each
        optional."""
        with self.lock:
            ruleset = body.get("ruleset", self.ruleset)
            if ruleset not in RULESETS:
                raise ValueError("ruleset should be V1 or V2")
            multipliers = self.multipliers
            if "multipliers" in body:
                multipliers = parse_multipliers(body["multipliers"],
                                                strict=True)
            colours = self.v2_colours
            if "colours" in body:
                colours = parse_colours(body["colours"], strict=True)
            self.ruleset = ruleset
            self.multipliers = multipliers
            self.v2_colours = colours
            for team in self.teams.values():
                picks = team.get("picks")
                if picks and (picks["decoy"] not in colours
                              or picks["double"] not in colours):
                    team["picks"] = None
            self.save()
        self.say(f"Ruleset {ruleset}: multipliers " + ", ".join(
            f"{m}x" for m in multipliers) + "; V2 colours " +
            ", ".join(colours))

    def set_counts(self, counts):
        """The tokens in each zone, from the score calculator: {zone:
        {colour: count}}."""
        with self.lock:
            self.counts = parse_counts(counts, self.all_colours(),
                                       strict=True)
            self.save()

    def listed(self):
        return sorted(self.teams.values(), key=lambda t: t["updated"])

    # ROS side

    def attach(self, node):
        """Publishes /game/teams on node, latched, now and on each
        change."""
        from rclpy.qos import DurabilityPolicy, QoSProfile
        from std_msgs.msg import String
        publisher = node.create_publisher(String, TOPIC, QoSProfile(
            depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL))
        self.publish = lambda data: publisher.publish(String(data=data))
        with self.lock:
            self.publish(json.dumps(self.message()))

    # Changes

    def sign_up(self, body):
        """A team from a phone: {"id" (to edit it, or none for a new
        team), "name", and under V1 "points": {colour: points}, under V2
        "picks" (see check_picks)}. Returns the team with its id."""
        name = " ".join(str(body.get("name", "")).split())
        if not name:
            raise ValueError("give the team a name")
        if len(name) > MAX_NAME:
            raise ValueError(f"keep the name to {MAX_NAME} letters")
        with self.lock:
            key = body.get("id")
            if key not in self.teams:
                key = None
            old = self.teams.get(key, {})
            points = dict(old.get("points", {}))
            picks = old.get("picks")
            if self.ruleset == "V2":
                picks = self.check_picks(body.get("picks"))
            else:
                points = self.check_points(body.get("points"))
            for other_key, other in self.teams.items():
                if other_key != key and \
                        other["name"].casefold() == name.casefold():
                    raise ValueError(f"{other['name']} is taken; "
                                     "pick another name")
            if key is None:
                if len(self.teams) >= MAX_TEAMS:
                    raise ValueError("the game is full")
                key = secrets.token_hex(4)
                verb = "signed up"
            else:
                verb = "changed"
            team = {"name": name, "points": points, "picks": picks,
                    "updated": time.time()}
            self.teams[key] = team
            self.save()
        if self.ruleset == "V2":
            self.say(f"Team {name} {verb}: zones " + ", ".join(
                f"{m}x" for m in picks["zones"]) +
                f"; 2x {picks['double']}, decoy {picks['decoy']}")
        else:
            self.say(f"Team {name} {verb}: " + ", ".join(
                f"{colour} {value}" for colour, value in points.items()))
        return {"id": key, **team}

    def check_points(self, given):
        """A V1 team's points per token colour."""
        if not isinstance(given, dict):
            raise ValueError("points should be one number per colour")
        points = {}
        for colour in self.tokens:
            value = given.get(colour, 0)
            if isinstance(value, str) and value.strip() == "":
                value = 0
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise ValueError(f"{colour} should be a whole number")
            if not 0 <= value <= MAX_POINTS:
                raise ValueError(
                    f"{colour} should be 0 to {MAX_POINTS} points")
            points[colour] = value
        return points

    def set_tokens(self, tokens):
        if isinstance(tokens, str):
            tokens = re.split(r"[\s,]+", tokens)
        if not isinstance(tokens, list):
            raise ValueError("tokens should be a list of colours")
        clean = []
        for token in tokens:
            token = str(token).strip().lower()
            if not token:
                continue
            if not COLOUR.match(token):
                raise ValueError(f"{token!r} is not a colour name")
            if token not in clean:
                clean.append(token)
        if not clean:
            raise ValueError("give at least one token colour")
        with self.lock:
            self.tokens = clean
            for team in self.teams.values():
                old = team.get("points", {})
                team["points"] = {c: old.get(c, 0) for c in clean}
            self.save()
        self.say("Token colours: " + ", ".join(clean))

    def remove(self, key):
        with self.lock:
            team = self.teams.pop(key, None)
            if team is None:
                raise ValueError("no such team")
            self.save()
        self.say(f"Team {team['name']} removed.")

    def clear(self):
        with self.lock:
            self.teams.clear()
            self.save()
        self.say("All teams removed.")

    # The Gamemaster's routes, for the Teams tab

    def lan_url(self):
        return f"http://{self.host or lan_address()}:{self.port}/"

    def url(self):
        """The QR code's address: the public link while it is up."""
        if self.public:
            return f"{self.public}/?code={self.code}"
        return self.lan_url()

    # The public link, for phones on any network

    def start_public(self):
        if self.tunnel is not None:
            return
        program = shutil.which(self.cloudflared)
        if program is None:
            self.public_note = ("cloudflared is not installed; see the "
                                "README's Teams section")
            self.say(f"Public link is off: {self.public_note}.", "error")
            return
        self.code = secrets.token_hex(3)
        self.public = None
        self.public_note = "Starting…"
        self.tunnel = subprocess.Popen(
            [program, "tunnel", "--no-autoupdate", "--url",
             f"http://localhost:{self.port}"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, text=True)
        threading.Thread(target=self.watch_tunnel, args=(self.tunnel,),
                         daemon=True).start()

    def watch_tunnel(self, tunnel):
        """Reads cloudflared's log for its address, then waits it out."""
        last = ""
        for line in tunnel.stderr:
            found = TUNNEL_URL.search(line)
            if found and self.tunnel is tunnel and not self.public:
                self.public = found.group(0)
                self.public_note = ""
                self.say(f"Public sign-up link on {self.url()}")
            elif line.strip():
                last = line.strip()[-160:]
        tunnel.wait()
        if self.tunnel is tunnel:  # it ended by itself
            self.tunnel = self.public = None
            self.public_note = f"cloudflared stopped: {last or 'no reason'}"
            self.say(f"Public link is off: {self.public_note}", "error")

    def stop_public(self):
        tunnel, self.tunnel = self.tunnel, None
        self.public = None
        self.public_note = ""
        if tunnel is not None:
            tunnel.terminate()
            try:
                tunnel.wait(timeout=5)
            except subprocess.TimeoutExpired:
                tunnel.kill()
            self.say("Public link off; phones must be on this Wi-Fi.")

    def allows(self, headers, query):
        """Whether a phone's request may go ahead. One that came through
        the tunnel (cloudflared adds Cf-Connecting-Ip) needs the code."""
        if headers.get("Cf-Connecting-Ip") is None:
            return True
        given = parse_qs(query).get("code", [""])[0]
        code = self.code if self.tunnel is not None else None
        return bool(code) and secrets.compare_digest(given, code)

    def state(self):
        with self.lock:
            return {"url": self.url(), "lan_url": self.lan_url(),
                    "public": self.tunnel is not None,
                    "public_up": bool(self.public),
                    "public_note": self.public_note,
                    "tokens": list(self.tokens),
                    "ruleset": self.ruleset, "rulesets": list(RULESETS),
                    "multipliers": list(self.multipliers),
                    "v2_colours": list(self.v2_colours),
                    "colours": self.colours(), "zones": list(ZONES),
                    "counts": self.counts, "max_count": MAX_COUNT,
                    "teams": [{"id": key, **team,
                               "score": self.score(team)}
                              for key, team in
                              sorted(self.teams.items(),
                                     key=lambda kv: kv[1]["updated"])],
                    "serving": self.server is not None, "error": self.error,
                    "topic": TOPIC}

    def get(self, path):
        if path == "/api/teams":
            return 200, self.state()
        if path == "/api/teams/qr.svg":
            return 200, qr.svg(self.url()).encode(), "image/svg+xml"
        return None

    def post(self, path, read_body):
        try:
            if path == "/api/teams/tokens":
                self.set_tokens(read_body().get("tokens"))
                return 200, self.state()
            if path == "/api/teams/rules":
                self.set_rules(read_body())
                return 200, self.state()
            if path == "/api/teams/counts":
                self.set_counts(read_body().get("counts", {}))
                return 200, self.state()
            if path == "/api/teams/remove":
                self.remove(read_body().get("id"))
                return 200, self.state()
            if path == "/api/teams/public":
                if read_body().get("on"):
                    self.start_public()
                else:
                    self.stop_public()
                return 200, self.state()
            if path == "/api/teams/clear":
                self.clear()
                return 200, self.state()
        except ValueError as error:
            return 400, {"error": str(error)}
        return None

    # The sign-up server, open to the network

    def serve(self):
        """Starts the sign-up page on every network, port self.port."""
        try:
            self.server = ThreadingHTTPServer(("0.0.0.0", self.port),
                                              signup_handler(self))
        except OSError as error:
            self.error = f"port {self.port} is busy: {error.strerror}"
            self.say(f"Phone sign-up is off: {self.error}", "error")
            return
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever,
                         daemon=True).start()
        self.say(f"Phone sign-up on {self.url()}")

    def stop(self):
        self.stop_public()
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None


DENIED = (b"<!doctype html><meta name=viewport content='width=device-width'>"
          b"<p style='font:18px sans-serif;margin:2em'>This sign-up link has "
          b"run out. Scan the QR code on the Gamemaster again.</p>")


def signup_handler(teams):
    """The phones' side: the sign-up page and /api/signup, nothing else."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "robogame_signup"

        def log_message(self, format, *args):
            pass

        def reply(self, status, body, kind="application/json"):
            data = body if isinstance(body, bytes) else \
                json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urlsplit(self.path)
            if not teams.allows(self.headers, url.query):
                if url.path == "/api/signup":
                    return self.reply(403, {"error": "this link has run "
                                            "out; scan the QR code again"})
                return self.reply(403, DENIED, HTML)
            if url.path in ("/", "/index.html"):
                with open(SIGNUP_PAGE, "rb") as file:
                    return self.reply(200, file.read(), HTML)
            if url.path == "/api/signup":
                key = parse_qs(url.query).get("id", [None])[0]
                with teams.lock:
                    team = teams.teams.get(key)
                    return self.reply(200, {
                        "tokens": list(teams.tokens),
                        "ruleset": teams.ruleset,
                        "colours": teams.colours(),
                        "zones": list(ZONES),
                        "multipliers": list(teams.multipliers),
                        "max_points": MAX_POINTS, "max_name": MAX_NAME,
                        "team": {"id": key, **team} if team else None})
            self.reply(404, {"error": "not found"})

        def do_POST(self):
            url = urlsplit(self.path)
            if url.path != "/api/signup":
                return self.reply(404, {"error": "not found"})
            if not teams.allows(self.headers, url.query):
                return self.reply(403, {"error": "this link has run out; "
                                        "scan the QR code again"})
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length > 4096:
                    raise ValueError("that is too long")
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("send a JSON object")
                self.reply(200, teams.sign_up(body))
            except ValueError as error:
                self.reply(400, {"error": str(error)})

    return Handler
