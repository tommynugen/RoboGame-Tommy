"""The Gazebo tab: the running Gazebo world drawn in the app, live.

The app talks to Gazebo over gz-transport, as the Gazebo window does: it
asks the world for its scene (every model, link and visual with its
shape, colour and mesh) and listens to the poses Gazebo sends out up to
60 times a second. gazebo.html draws that scene with three.js and moves
each part as the poses come, so the sim shows in the app with no Gazebo
window. Gazebo itself still has to be installed: the page says so when
it is not, and how to get it.

Routes, served by gamemaster.py:

- GET /api/gazebo: {"installed", "bindings", "connected", "world",
  "paused", "sim_time" (s), "rtf" (real time factor), "scene" (a number
  that goes up each time the world gains or loses a model), "error",
  "sim" (sim_runner's status)}. installed is whether Gazebo is on this
  computer, bindings whether its Python bindings are, and connected
  whether a world answered
- GET /api/gazebo/scene: {"world", "nodes": [{"id", "parent", "name",
  "pose": [x, y, z, qx, qy, qz, qw], "visuals": [{"pose", "shape",
  "color": [r, g, b, a]}]}]}, models and links, each posed in its
  parent's frame as Gazebo poses them. shape is {"type": "box", "size":
  [x, y, z]}, "cylinder" (radius, length), "sphere" (radius), "plane"
  (size [x, y]), "capsule" (radius, length) or "mesh" (url, scale)
- GET /api/gazebo/poses: {"time" (s), "poses": {id: pose}}, the newest
  pose of each part that moves
- GET /api/gazebo/mesh?uri=URI: a mesh file the scene names (.stl,
  .dae or .obj only)
- POST /api/gazebo/control {"action": "play" | "pause" | "step" |
  "reset"}: runs, pauses or steps the world one physics step. reset
  puts each model that moves back where the tab first saw it, not
  Gazebo's own reset, which would take away the robots spawned after
  the world started
"""

import glob
import math
import os
import shutil
import threading
import time
from urllib.parse import parse_qs, quote, unquote, urlparse

HERE = os.path.dirname(os.path.realpath(__file__))
PAGE = os.path.join(HERE, "gazebo.html")
WORLD = "robogame_arena"  # sim.launch.py's world, if Gazebo names none
TIMEOUT = 2000  # ms for a service call
STALE = 3.0  # s with no stats from the world before it counts as gone
LOOK_AGAIN = 3.0  # s between asks for a world while there is none
MESHES = (".stl", ".dae", ".obj")
SHAPES = ("box", "cylinder", "sphere", "plane", "mesh", "capsule")
ACTIONS = ("play", "pause", "step", "reset")


def installed():
    """Whether Gazebo is on this computer: its gz tool or a gz-sim
    server on the PATH."""
    return any(shutil.which(name) for name in ("gz", "gz-sim", "gz-sim-main"))


def bindings():
    """Whether gz-transport's Python bindings can be imported."""
    try:
        import gz.transport  # noqa: F401
        import gz.msgs  # noqa: F401
    except ImportError:
        return False
    return True


def pose_list(pose):
    """A gz.msgs.Pose as [x, y, z, qx, qy, qz, qw]."""
    p, q = pose.position, pose.orientation
    w = q.w if (q.x or q.y or q.z or q.w) else 1.0
    return [p.x, p.y, p.z, q.x, q.y, q.z, w]


def colour(material):
    c = material.diffuse if material.HasField("diffuse") else \
        material.ambient
    if not (c.r or c.g or c.b or c.a):
        return [0.7, 0.7, 0.7, 1.0]
    return [c.r, c.g, c.b, c.a if c.a else 1.0]


def mesh_url(uri):
    return "/api/gazebo/mesh?uri=" + quote(uri, safe="")


def shape(geometry):
    """A gz.msgs.Geometry as the page draws it, or None for one it does
    not (a heightmap, a polyline)."""
    kind = type(geometry).Type.Name(geometry.type).lower()
    if kind not in SHAPES:
        return None
    if kind == "box":
        s = geometry.box.size
        return {"type": kind, "size": [s.x, s.y, s.z]}
    if kind == "cylinder":
        return {"type": kind, "radius": geometry.cylinder.radius,
                "length": geometry.cylinder.length}
    if kind == "sphere":
        return {"type": kind, "radius": geometry.sphere.radius}
    if kind == "capsule":
        return {"type": kind, "radius": geometry.capsule.radius,
                "length": geometry.capsule.length}
    if kind == "plane":
        s = geometry.plane.size
        return {"type": kind, "size": [s.x or 100.0, s.y or 100.0]}
    if kind == "mesh" and geometry.mesh.filename:
        s = geometry.mesh.scale
        scale = [s.x, s.y, s.z] if (s.x or s.y or s.z) else [1, 1, 1]
        return {"type": kind, "url": mesh_url(geometry.mesh.filename),
                "scale": scale}
    return None


def scene_nodes(scene):
    """A gz.msgs.Scene as a flat list of models and links, parents
    first."""
    nodes = []

    def visuals(link):
        out = []
        for visual in link.visual:
            drawn = shape(visual.geometry)
            if drawn is None:
                continue
            s = visual.scale
            if drawn["type"] == "mesh" and (s.x or s.y or s.z):
                drawn["scale"] = [a * b for a, b in
                                  zip(drawn["scale"], [s.x, s.y, s.z])]
            out.append({"pose": pose_list(visual.pose), "shape": drawn,
                        "color": colour(visual.material)})
        return out

    def model(message, parent):
        nodes.append({"id": message.id, "parent": parent,
                      "name": message.name,
                      "pose": pose_list(message.pose), "visuals": []})
        for link in message.link:
            nodes.append({"id": link.id, "parent": message.id,
                          "name": link.name, "pose": pose_list(link.pose),
                          "visuals": visuals(link)})
        for inner in message.model:
            model(inner, message.id)

    for top in scene.model:
        model(top, None)
    return nodes


def mesh_roots():
    """The folders a model:// or relative mesh URI may be in."""
    roots = []
    for name in ("GZ_SIM_RESOURCE_PATH", "GZ_FILE_PATH",
                 "IGN_GAZEBO_RESOURCE_PATH", "SDF_PATH"):
        roots += [p for p in os.environ.get(name, "").split(os.pathsep) if p]
    roots.append(os.path.join(os.path.dirname(HERE), "models"))
    try:
        from ament_index_python.packages import get_package_share_directory
        roots.append(os.path.join(
            get_package_share_directory("robogame_sim"), "models"))
    except Exception:  # no ROS, or the package is not installed
        pass
    return roots


def find_mesh(uri):
    """The file a mesh URI names, or None. Only mesh files."""
    uri = unquote(uri or "")
    if not uri.lower().endswith(MESHES):
        return None
    if uri.startswith("file://"):
        path = uri[len("file://"):]
    elif uri.startswith("model://"):
        rest = uri[len("model://"):]
        path = next((os.path.join(root, rest) for root in mesh_roots()
                     if os.path.isfile(os.path.join(root, rest))), None)
    elif os.path.isabs(uri):
        path = uri
    else:
        path = next((p for root in mesh_roots()
                     for p in glob.glob(os.path.join(root, "**", uri),
                                        recursive=True)), None)
    if path and os.path.isfile(path):
        return os.path.realpath(path)
    return None


class GazeboView:
    def __init__(self, sim=None, partition=None):
        self.sim = sim  # the SimRunner, for its status and partition
        self.partition = partition or (
            lambda: (sim.options.get("partition", "") if sim else ""))
        self.node = None
        self.node_partition = None
        self.world = None
        self.subscribed = None
        self.stats = None
        self.stats_seen = 0.0
        self.poses = {}
        self.pose_time = 0.0
        self.scene = None
        self.scene_version = 0
        self.scene_asked = 0.0
        self.first_poses = {}  # model name: pose when first seen, for reset
        self.world_asked = -LOOK_AGAIN
        self.error = ""
        self.lock = threading.RLock()

    # -- gz-transport --------------------------------------------------

    def connect(self):
        """A gz-transport node in the sim's partition, made again when the
        partition changes. False, with self.error, without bindings."""
        partition = self.partition() or ""
        if self.node is not None and partition == self.node_partition:
            return True
        try:
            from gz.transport import Node
        except ImportError as error:
            self.error = f"no gz-transport Python bindings ({error})"
            return False
        try:
            from gz.transport import NodeOptions
            options = NodeOptions()
            if partition:
                options.partition = partition
            node = Node(options)
        except (ImportError, TypeError, AttributeError):
            node = Node()
        self.node, self.node_partition = node, partition
        self.world = self.subscribed = None
        self.stats, self.scene, self.poses = None, None, {}
        self.error = ""
        return True

    def request(self, service, message, reply_type):
        result, reply = self.node.request(service, message, type(message),
                                          reply_type, TIMEOUT)
        return reply if result else None

    def find_world(self):
        """The world's name from Gazebo, or None when no world answers."""
        from gz.msgs.empty_pb2 import Empty
        from gz.msgs.stringmsg_v_pb2 import StringMsg_V
        result, reply = self.node.request("/gazebo/worlds", Empty(), Empty,
                                          StringMsg_V, 500)
        reply = reply if result else None
        if reply is not None and reply.data:
            return reply.data[0]
        return None

    def subscribe(self, world):
        from gz.msgs.pose_v_pb2 import Pose_V
        from gz.msgs.world_stats_pb2 import WorldStatistics
        from gz.msgs.scene_pb2 import Scene
        from gz.msgs.uint32_v_pb2 import UInt32_V
        base = f"/world/{world}"
        self.node.subscribe(WorldStatistics, f"{base}/stats", self.on_stats)
        self.node.subscribe(Pose_V, f"{base}/dynamic_pose/info",
                            self.on_poses)
        self.node.subscribe(Scene, f"{base}/scene/info", self.on_scene)
        self.node.subscribe(UInt32_V, f"{base}/scene/deletion",
                            self.on_deletion)
        self.subscribed = world

    def on_stats(self, message):
        with self.lock:
            t = message.sim_time
            self.stats = {"paused": message.paused,
                          "sim_time": t.sec + t.nsec * 1e-9,
                          "rtf": round(message.real_time_factor, 3)}
            self.stats_seen = time.monotonic()

    def on_poses(self, message):
        t = message.header.stamp
        poses = {pose.id: pose_list(pose) for pose in message.pose}
        with self.lock:
            self.poses.update(poses)
            self.pose_time = t.sec + t.nsec * 1e-9
            known = self.scene is not None and all(
                i in self.scene["ids"] for i in poses)
        if not known:
            # A part the scene does not have yet: a robot just spawned.
            self.scene_stale()

    def on_scene(self, message):
        # A model added: Gazebo sends just that one, so ask for all again.
        self.scene_stale()

    def on_deletion(self, message):
        self.scene_stale()

    def scene_stale(self):
        with self.lock:
            if self.scene is not None:
                self.scene["stale"] = True

    def load_scene(self):
        from gz.msgs.empty_pb2 import Empty
        from gz.msgs.scene_pb2 import Scene
        reply = self.request(f"/world/{self.world}/scene/info", Empty(),
                             Scene)
        if reply is None:
            return
        nodes = scene_nodes(reply)
        with self.lock:
            for model in reply.model:
                if not model.is_static:
                    self.first_poses.setdefault(model.name,
                                                pose_list(model.pose))
            self.scene = {"world": self.world, "nodes": nodes,
                          "ids": {node["id"] for node in nodes},
                          "stale": False}
            self.scene_version += 1

    def refresh(self):
        """Connects, finds the world and keeps the scene up to date.
        Called by each GET, so nothing runs while the tab is closed."""
        with self.lock:
            if not self.connect():
                return False
            now = time.monotonic()
            if self.world is None or now - self.stats_seen > STALE:
                if now - self.world_asked < LOOK_AGAIN:
                    return self.world is not None
                self.world_asked = now
                world = self.find_world()
                if world is None:
                    self.world, self.stats, self.scene = None, None, None
                    self.poses = {}
                    self.error = "no Gazebo world is running"
                    return False
                if world != self.world:
                    self.scene, self.first_poses = None, {}
                self.world, self.error = world, ""
                self.stats_seen = now
                if self.subscribed != world:
                    self.subscribe(world)
            stale = self.scene is None or self.scene["stale"]
            due = time.monotonic() - self.scene_asked > 1.0
            if stale and due:
                self.scene_asked = time.monotonic()
                self.load_scene()
            return True

    def control(self, action):
        if action not in ACTIONS:
            raise ValueError("action takes " + ", ".join(ACTIONS))
        with self.lock:
            if not self.refresh():
                raise ValueError(self.error or "no Gazebo world is running")
            from gz.msgs.boolean_pb2 import Boolean
            from gz.msgs.world_control_pb2 import WorldControl
            message = WorldControl()
            if action == "play":
                message.pause = False
            elif action == "pause":
                message.pause = True
            elif action == "step":
                message.pause = True
                message.multi_step = 1
            else:
                return self.put_back()
            reply = self.request(f"/world/{self.world}/control", message,
                                 Boolean)
            if reply is None or not reply.data:
                raise ValueError(f"Gazebo did not {action} the world")
        return self.status()

    def put_back(self):
        """Puts every model that moves back where the tab first saw it.
        Not Gazebo's own reset: that goes back to the world file and so
        takes away the robots spawned since."""
        from gz.msgs.boolean_pb2 import Boolean
        from gz.msgs.pose_v_pb2 import Pose_V
        message = Pose_V()
        for name, pose in self.first_poses.items():
            p = message.pose.add()
            p.name = name
            (p.position.x, p.position.y, p.position.z, p.orientation.x,
             p.orientation.y, p.orientation.z, p.orientation.w) = pose
        if not message.pose:
            raise ValueError("no models to put back")
        reply = self.request(f"/world/{self.world}/set_pose_vector",
                             message, Boolean)
        if reply is None or not reply.data:
            raise ValueError("Gazebo did not put the models back")
        return self.status()

    # -- the routes ----------------------------------------------------

    def status(self):
        reply = {"installed": installed(), "bindings": bindings(),
                 "connected": False, "world": None, "paused": None,
                 "sim_time": None, "rtf": None, "scene": 0, "error": "",
                 "sim": self.sim.status() if self.sim else None}
        if not reply["bindings"]:
            reply["error"] = "no gz-transport Python bindings"
            return reply
        with self.lock:
            connected = self.refresh()
            reply.update(connected=connected, world=self.world,
                         scene=self.scene_version, error=self.error)
            if self.stats:
                reply.update(self.stats)
        return reply

    def scene_reply(self):
        with self.lock:
            if not self.refresh() or self.scene is None:
                return 503, {"error": self.error or "no scene yet"}
            return 200, {"world": self.scene["world"],
                         "version": self.scene_version,
                         "nodes": self.scene["nodes"]}

    def poses_reply(self):
        with self.lock:
            return 200, {"time": self.pose_time,
                         "poses": {str(i): p for i, p in self.poses.items()
                                   if all(map(math.isfinite, p))}}

    def get(self, path):
        url = urlparse(path)
        if url.path == "/api/gazebo":
            return 200, self.status()
        if url.path == "/api/gazebo/scene":
            return self.scene_reply()
        if url.path == "/api/gazebo/poses":
            return self.poses_reply()
        if url.path == "/api/gazebo/mesh":
            uri = (parse_qs(url.query).get("uri") or [""])[0]
            found = find_mesh(uri)
            if found is None:
                return 404, {"error": f"no mesh {uri!r}"}
            with open(found, "rb") as file:
                return 200, file.read(), "application/octet-stream"
        return None

    def post(self, path, read_body):
        if path != "/api/gazebo/control":
            return None
        try:
            return 200, self.control(read_body().get("action"))
        except ValueError as error:
            return 400, {"error": str(error)}
