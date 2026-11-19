"""The Gamemaster as one HTML file, to send to teammates.

    python3 -m robogame_sim.shared_gamemaster [OUT.html]

It opens in any browser with nothing installed. It has every tab the
Gamemaster has, in the same order, but only the Arena editor works: it is
the shared editor (shared_editor.py), with its saved layouts, parts and
Export. The other tabs need the Gamemaster app on the arena computer (ROS,
the sim, robots, the camera, the BLE bridge, controllers, jobs), so they
are shown as they look, greyed out, with a note at the top saying they do
not work here and why. Their requests to the app get a "not in the web
version" answer from a small script in the page.

The tabs are gamemaster.html's own links and pages, so a file built again
after the app changes has its new tabs and pages.
"""

import json
import re
import sys

from robogame_sim import shared_editor

FILE_NAME = "robogame-gamemaster.html"

# Each tab's page, and why it does not work here. The editor works.
PAGES = {
    "setup": ("setup.html", "The setup steps check and build things on the "
              "arena computer: ROS, the camera, the bridge and robots."),
    "editor": (None, None),
    "console": ("robot_console.html", "The robot console drives robots "
                "through ROS, the BLE bridge and the game controllers "
                "plugged into the arena computer."),
    "vision": ("vision.html", "Vision needs the arena camera and the "
               "AprilTag finder running on the arena computer."),
    "chase": ("chase.html", "Chase tests run chase_sim and Gazebo on the "
              "arena computer."),
    "logs": ("app_log.html", "Logs show what the Gamemaster app and the "
             "things it started print, so there is nothing to show here."),
    "upkeep": ("upkeep.html", "Upkeep jobs run on the arena computer's "
               "copy of the repo."),
    "teams": ("teams.html", "Team sign-up is served to phones from the "
              "arena computer, over its network."),
}

# Put first in an off tab's page: its calls to the app get an answer that
# says the web version cannot do that, so nothing hangs or retries fast.
OFF_SHIM = r"""<script>
(() => {
  const answer = () => Promise.resolve(new Response(
    JSON.stringify({ error: "not in the web version of the Gamemaster" }),
    { status: 503, headers: { "Content-Type": "application/json" } }));
  window.fetch = answer;
  window.EventSource = function () { this.close = () => {}; };
})();
</script>
<style>
  body > :not(.web-off) {
    pointer-events: none; user-select: none;
    opacity: .45; filter: grayscale(1);
  }
  .web-off {
    margin: 0; padding: 10px 16px; font: 14px/1.45 system-ui, sans-serif;
    background: #fff4e0; color: #5a3a00; border-bottom: 1px solid #e0b060;
  }
  @media (prefers-color-scheme: dark) {
    .web-off { background: #3a2a0c; color: #f3d9a4; border-color: #6b4e17; }
  }
</style>
"""

SHELL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RoboGame Gamemaster (web)</title>
<style>STYLE
  nav a.off span.off-tag {
    padding: 0 5px; border-radius: 8px; font-size: 10px;
    border: 1px solid var(--line); color: var(--muted);
  }
  nav a.off { opacity: .75; padding: 8px 8px; }
  #web-note {
    margin-left: auto; align-self: center; padding: 2px 10px;
    border: 1px solid var(--warn); border-radius: 12px;
    color: var(--warn); font-size: 12px; white-space: nowrap;
  }
  #intro {
    display: flex; gap: 12px; align-items: flex-start;
    padding: 8px 16px; background: var(--panel); color: var(--muted);
    border-bottom: 1px solid var(--line); font-size: 13px;
  }
  #intro[hidden] { display: none; }
  #intro p { margin: 0; flex: 1; }
  #intro button {
    font: inherit; padding: 2px 10px; border-radius: 6px; cursor: pointer;
    border: 1px solid var(--line); background: var(--bg); color: var(--ink);
  }
</style>
</head>
<body>
<nav>
  <strong>RoboGame Gamemaster</strong>
LINKS
  <span id="web-note" title="This file has no app behind it: only the
    Arena editor works.">Web version</span>
</nav>
<div id="intro">
  <p>This is the <b>web version</b> of the RoboGame Gamemaster, one file
  that opens in any browser. The <b>Arena editor</b> works: Save keeps
  layouts in this browser and Export downloads one to send back. Tabs
  marked <b>App only</b> are shown so you can see them, but they need the
  Gamemaster app on the arena computer
  (<code>ros2 run robogame_sim robogame_gamemaster</code>) and do nothing
  here.</p>
  <button id="intro-close" aria-label="Hide this note">Got it</button>
</div>
<script>
const PAGES = PAGE_DATA;
const links = [...document.querySelectorAll("nav a[data-tab]")];
// The wheel scrolls the tab bar sideways in a narrow window.
const bar = document.querySelector("nav");
bar.addEventListener("wheel", event => {
  if (bar.scrollWidth <= bar.clientWidth) return;
  if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
  bar.scrollLeft += event.deltaY * (event.deltaMode === 1 ? 16 : 1);
  event.preventDefault();
}, { passive: false });
const frames = {};
for (const link of links) {
  const frame = document.createElement("iframe");
  frame.title = link.textContent.trim();
  frame.hidden = true;
  document.body.appendChild(frame);
  frames[link.dataset.tab] = frame;
}
const remember = (key, value) => {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch (error) { return null; }
};
const intro = document.getElementById("intro");
intro.hidden = remember("gamemaster-web.intro") === "seen";
document.getElementById("intro-close").addEventListener("click", () => {
  intro.hidden = true;
  remember("gamemaster-web.intro", "seen");
});

// Each tab's page is put in its frame the first time it shows, and kept.
function show() {
  const asked = location.hash.slice(1);
  const last = remember("gamemaster-web.tab");
  const tab = asked in frames ? asked : last in frames ? last : "editor";
  const frame = frames[tab];
  if (!frame.srcdoc) frame.srcdoc = PAGES[tab];
  for (const [name, other] of Object.entries(frames)) {
    other.hidden = name !== tab;
  }
  for (const link of links) {
    if (link.dataset.tab === tab) {
      link.setAttribute("aria-current", "page");
      link.scrollIntoView({ block: "nearest", inline: "nearest" });
    } else link.removeAttribute("aria-current");
  }
  document.title = `${frame.title} · RoboGame Gamemaster (web)`;
  remember("gamemaster-web.tab", tab);
}
window.addEventListener("keydown", event => {
  if (!event.altKey || event.ctrlKey || event.metaKey) return;
  const link = links[Number(event.key) - 1];
  if (link) {
    event.preventDefault();
    location.hash = link.dataset.tab;
  }
});
window.addEventListener("hashchange", show);
show();
</script>
</body>
</html>
"""


def one(text, pattern, what):
    """The one match of pattern, so a change that moves it fails the
    build, not the page."""
    found = re.findall(pattern, text, re.S)
    if len(found) != 1:
        raise ValueError(f"{what}: {len(found)} matches of {pattern!r}")
    return found[0]


def inline_scripts(page):
    for name in ("dialogs.js", "arena_draw.js"):
        page = page.replace(
            f'<script src="{name}"></script>',
            f"<script>{shared_editor.inline_js(shared_editor.read(name))}"
            "</script>")
    return page


def off_page(name, why):
    """A tab's own page, greyed out, saying why it does not work here."""
    page = inline_scripts(shared_editor.read(name))
    head = one(page, r"<head(?:\s[^>]*)?>", name)
    page = page.replace(head, head + "\n" + OFF_SHIM, 1)
    body = one(page, r"<body(?:\s[^>]*)?>", name)
    note = (f'<p class="web-off" role="note"><b>Not usable in the web '
            f"version.</b> {why} Open this tab in the Gamemaster app to "
            "use it.</p>")
    return page.replace(body, body + "\n" + note, 1)


def build():
    """The shareable Gamemaster's HTML."""
    shell = shared_editor.read("gamemaster.html")
    style = one(shell, r"<style>(.*?)</style>", "gamemaster.html")
    links = re.findall(r'  <a href="#(\w+)" data-tab="\1".*?</a>\n', shell,
                       re.S)
    if set(links) != set(PAGES):
        raise ValueError(f"gamemaster.html's tabs {links} are not the ones "
                         f"this file knows: {sorted(PAGES)}")

    pages, nav = {}, []
    for tab in links:
        name, why = PAGES[tab]
        link = one(shell, rf'  <a href="#{tab}".*?</a>\n', tab)
        link = re.sub(r'\s*<span class="badge".*?</span>', "", link)
        if name is None:
            pages[tab] = shared_editor.build()
        else:
            pages[tab] = off_page(name, why)
            link = link.replace(
                'data-tab=', 'class="off" data-tab=', 1).replace(
                "</a>", ' <span class="off-tag">App only</span></a>', 1)
            link = re.sub(r'title="([^"]*)"',
                          lambda m: f'title="{m.group(1)}. Needs the '
                          'Gamemaster app."', link, count=1)
        nav.append(link)

    data = shared_editor.inline_js(json.dumps(pages, separators=(",", ":")))
    return (SHELL.replace("STYLE", style, 1)
            .replace("LINKS\n", "".join(nav), 1)
            .replace("PAGE_DATA", data, 1))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    out = argv[0] if argv else FILE_NAME
    with open(out, "w", encoding="utf-8") as file:
        file.write(build())
    print(f"Wrote {out}: send it to teammates, who open it in a browser. "
          f"Only its Arena editor works without the app.")


if __name__ == "__main__":
    main()
