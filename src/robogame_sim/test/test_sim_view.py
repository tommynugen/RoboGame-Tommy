"""The Gazebo tab's routes, with no Gazebo running."""

import os

import pytest

from robogame_sim import sim_view


def test_mesh_files_only(tmp_path, monkeypatch):
    mesh = tmp_path / "part.stl"
    mesh.write_bytes(b"solid x")
    (tmp_path / "secret.txt").write_text("no")
    monkeypatch.setenv("GZ_SIM_RESOURCE_PATH", str(tmp_path))
    assert sim_view.find_mesh(f"file://{mesh}") == str(mesh)
    assert sim_view.find_mesh(str(mesh)) == str(mesh)
    assert sim_view.find_mesh("model://part.stl") == str(mesh)
    assert sim_view.find_mesh(str(tmp_path / "secret.txt")) is None
    assert sim_view.find_mesh("/etc/passwd") is None
    assert sim_view.find_mesh("model://none.stl") is None


def test_arena_meshes_found():
    found = sim_view.find_mesh(
        "model://robogame_arena_cad/meshes/floor_module.stl")
    assert found and os.path.isfile(found)


def test_no_bindings_says_so(monkeypatch):
    monkeypatch.setattr(sim_view, "bindings", lambda: False)
    monkeypatch.setattr(sim_view, "installed", lambda: False)
    status, body = sim_view.GazeboView().get("/api/gazebo")
    assert status == 200
    assert body["installed"] is False and body["connected"] is False
    assert "bindings" in body["error"]


def test_routes():
    view = sim_view.GazeboView()
    assert view.get("/api/elsewhere") is None
    assert view.post("/api/elsewhere", dict) is None
    status, _ = view.get("/api/gazebo/mesh?uri=%2Fetc%2Fpasswd")
    assert status == 404
    status, body = view.post("/api/gazebo/control", lambda: {"action": "x"})
    assert status == 400 and "action takes" in body["error"]
    status, body = view.get("/api/gazebo/poses")
    assert status == 200 and body["poses"] == {}


def test_scene_from_gazebo_message():
    pytest.importorskip("gz.msgs")
    from gz.msgs.geometry_pb2 import Geometry
    from gz.msgs.scene_pb2 import Scene

    scene = Scene()
    model = scene.model.add(name="robot_1", id=10)
    model.pose.position.x = 0.5
    link = model.link.add(name="chassis", id=11)
    box = link.visual.add()
    box.geometry.type = Geometry.BOX
    box.geometry.box.size.x = 0.2
    box.material.diffuse.r = 1.0
    mesh = link.visual.add()
    mesh.geometry.type = Geometry.MESH
    mesh.geometry.mesh.filename = "model://robogame_arena_cad/a b.stl"
    wheel = link.visual.add()
    wheel.geometry.type = Geometry.CYLINDER
    wheel.geometry.cylinder.radius = 0.03

    nodes = sim_view.scene_nodes(scene)
    assert [(n["id"], n["parent"]) for n in nodes] == [(10, None), (11, 10)]
    assert nodes[0]["pose"] == [0.5, 0, 0, 0, 0, 0, 1.0]
    shapes = [v["shape"] for v in nodes[1]["visuals"]]
    assert [s["type"] for s in shapes] == ["box", "mesh", "cylinder"]
    assert shapes[0]["size"][0] == pytest.approx(0.2)
    assert shapes[1]["url"].endswith("a%20b.stl")
    assert nodes[1]["visuals"][0]["color"][0] == 1.0
