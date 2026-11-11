"""Live scenarios of the ``model`` tool (design § 14.2); collected into ``model.LIVE_SCENARIOS``."""
import json

from .live_common import _MM, _lc, _near


# --- helpers ---------------------------------------------------------------------------------


async def _call(session, tool, act, params):
    return await _lc().call(session, tool, act, params, units=_MM)


async def _do(session, tool, act, params):
    env = await _call(session, tool, act, params)
    assert env["ok"], "%s.%s: %s" % (tool, act, json.dumps(env.get("error"))[:400])
    return env["result"]


async def _m(session, act, params):
    return await _do(session, "model", act, params)


async def _fails(session, act, params, code, tool="model"):
    env = await _call(session, tool, act, params)
    assert not env["ok"] and env["error"]["code"] == code, "%s.%s expected %s, got %s" % (
        tool, act, code, json.dumps(env)[:300])
    return env["error"]


async def _ok_or_unsupported(session, act, params):
    """The envelope of a call that must succeed or fail with UNSUPPORTED."""
    env = await _call(session, "model", act, params)
    assert env["ok"] or env["error"]["code"] == "UNSUPPORTED", "%s: %s" % (act, json.dumps(env)[:400])
    return env


def _prof(sketch, index=0):
    return {"sketch": sketch, "index": index}


async def _sk(session, plane="XY"):
    return (await _do(session, "sketch", "create", {"plane": plane}))["sketch"]


async def _rect(session, p1=(0, 0), p2=(20, 10), plane="XY"):
    """A rectangle sketch; returns (sketch ref, the four curve refs)."""
    ref = await _sk(session, plane)
    r = await _do(session, "sketch", "rectangle", {"sketch": ref, "mode": "two_point", "p1": list(p1), "p2": list(p2)})
    return ref, [c["ref"] for c in r["curves"]]


async def _circle(session, r=5, center=(0, 0), plane="XY"):
    ref = await _sk(session, plane)
    await _do(session, "sketch", "circle", {"sketch": ref, "mode": "center_radius", "center": list(center), "radius": r})
    return ref


async def _line(session, p1, p2, plane="XY"):
    """A one-line sketch; returns (sketch ref, line ref)."""
    ref = await _sk(session, plane)
    r = await _do(session, "sketch", "line", {"sketch": ref, "points": [list(p1), list(p2)]})
    return ref, r["curves"][0]["ref"]


async def _points(session, pts, plane="XY"):
    ref = await _sk(session, plane)
    r = await _do(session, "sketch", "point", {"sketch": ref, "points": [list(p) for p in pts]})
    return ref, [p["ref"] for p in r["points"]]


async def _offset_plane(session, offset, plane="XY"):
    return (await _m(session, "construction_plane", {"method": "offset", "plane": plane, "offset": offset}))["plane"]


async def _solid(session, p1=(0, 0), p2=(20, 20), h=10, plane="XY"):
    """A box-like extrusion of a rectangle as a new body; returns (body ref, feature result)."""
    sk, _ = await _rect(session, p1, p2, plane)
    feat = await _m(session, "extrude", {"profile": _prof(sk), "distance": h})
    return feat["bodies"][0], feat


async def _faces(session, body):
    return (await _do(session, "inspect", "list_faces", {"body": body, "limit": 200}))["items"]


async def _edges(session, body):
    return (await _do(session, "inspect", "list_edges", {"body": body, "limit": 200}))["items"]


async def _face(session, body, kind="plane", normal=None, z=None):
    """Face ref of ``body``: the first of surface type ``kind`` with the given normal and centroid z."""
    for f in await _faces(session, body):
        if f["surface_type"] != kind:
            continue
        if normal is not None and max(abs(a - b) for a, b in zip(f["normal"], normal)) > 1e-6:
            continue
        if z is not None and abs(f["centroid"][2] - z) > 1e-3:
            continue
        return f["ref"]
    raise AssertionError("no %s face normal=%s z=%s" % (kind, normal, z))


async def _vertical_edge(session, body):
    for e in await _edges(session, body):
        if e["curve_type"] == "line" and abs(e["start"][2] - e["end"][2]) > 1e-3 \
                and abs(e["start"][0] - e["end"][0]) < 1e-6 and abs(e["start"][1] - e["end"][1]) < 1e-6:
            return e["ref"]
    raise AssertionError("no vertical edge")


async def _bounds(session, ref):
    return await _do(session, "inspect", "bounds", {"entity": ref})


async def _nfaces(session, body):
    return len(await _faces(session, body))


def _delta(env):
    """Change in the number of bodies over a write call (``changes`` omits an unchanged count)."""
    before, after = env["changes"].get("bodies", (0, 0))
    return after - before


async def _w(session, act, params):
    """A model write call that must succeed; returns the whole envelope."""
    env = await _call(session, "model", act, params)
    assert env["ok"], "%s: %s" % (act, json.dumps(env.get("error"))[:500])
    return env


# --- extrude ---------------------------------------------------------------------------------


async def _extrude_distance(session):
    sk, _ = await _rect(session, (0, 0), (20, 10))
    env = await _w(session, "extrude", {"profile": _prof(sk), "distance": 5, "name": "LiveExtrude"})
    r = env["result"]
    assert len(r["bodies"]) == 1 and r["feature"]["name"] == "LiveExtrude", r
    assert _delta(env) == 1, env["changes"]
    b = await _bounds(session, r["bodies"][0])
    _near(b["min"], [0, 0, 0])
    _near(b["max"], [20, 10, 5])
    await _fails(session, "extrude", {"profile": _prof(sk)}, "INVALID_PARAMS")
    await _fails(session, "extrude", {"profile": _prof(sk), "distance": 5, "participant_bodies": [r["bodies"][0]]},
                 "INVALID_PARAMS")


async def _extrude_symmetric(session):
    sk = await _circle(session, 5)
    r = await _m(session, "extrude", {"profile": _prof(sk), "extent": "symmetric", "distance": 10})
    b = await _bounds(session, r["bodies"][0])
    _near(b["min"], [-5, -5, -5])
    _near(b["max"], [5, 5, 5])


async def _extrude_two_sided(session):
    sk, _ = await _rect(session, (0, 0), (10, 10))
    r = await _m(session, "extrude", {"profile": _prof(sk), "extent": "two_sided", "distance": 10, "distance_two": 4})
    b = await _bounds(session, r["bodies"][0])
    _near(b["size"], [10, 10, 14])
    await _fails(session, "extrude", {"profile": _prof(sk), "extent": "two_sided", "distance": 10}, "INVALID_PARAMS")


async def _extrude_to_object(session):
    plane = await _offset_plane(session, 30)
    sk, _ = await _rect(session, (0, 0), (10, 10))
    r = await _m(session, "extrude", {"profile": _prof(sk), "extent": "to_object", "to_entity": plane})
    b = await _bounds(session, r["bodies"][0])
    _near(b["min"], [0, 0, 0])
    _near(b["max"], [10, 10, 30])


async def _extrude_taper_cut(session):
    body, _ = await _solid(session, (0, 0), (30, 30), 10)
    sk, _ = await _rect(session, (5, 5), (25, 25))
    env = await _w(session, "extrude", {"profile": _prof(sk), "distance": 5, "taper_angle": 5, "operation": "cut",
                                        "participant_bodies": [body]})
    assert _delta(env) == 0, env["changes"]
    faces = await _faces(session, body)
    assert any(f["surface_type"] == "plane" and abs(f["centroid"][2] - 5) < 1e-3 for f in faces), faces
    assert len(faces) == 11, len(faces)  # 6 + 4 pocket walls + floor


# --- revolve, sweep, loft --------------------------------------------------------------------


async def _revolve(session):
    sk, _ = await _rect(session, (10, 0), (20, 10))
    env = await _w(session, "revolve", {"profile": _prof(sk), "axis": "Y"})
    b = await _bounds(session, env["result"]["bodies"][0])
    _near(b["size"], [40, 10, 40])
    half = await _m(session, "revolve", {"profile": _prof((await _rect(session, (10, 0), (20, 10)))[0]), "axis": "Y",
                                         "extent": "angle", "angle": 180})
    assert len(half["bodies"]) == 1


async def _sweep(session):
    prof = await _circle(session, 3)
    path, line = await _line(session, (0, 0), (0, -30), "XZ")  # sketch +Y of XZ is model -Z
    r = await _m(session, "sweep", {"profile": _prof(prof), "path": [line]})
    b = await _bounds(session, r["bodies"][0])
    _near(b["size"], [6, 6, 30])


async def _sweep_guide_rail_twist(session):
    prof = await _circle(session, 3)
    path, line = await _line(session, (0, 0), (0, -30), "XZ")
    rail, rail_line = await _line(session, (3, 0), (3, -30), "XZ")
    r = await _m(session, "sweep", {"profile": _prof(prof), "path": [line], "guide_rail": [rail_line],
                                    "twist_angle": 30})
    assert len(r["bodies"]) == 1, r


async def _loft(session):
    a = await _circle(session, 5)
    b = await _circle(session, 5, plane=await _offset_plane(session, 20))
    r = await _m(session, "loft", {"sections": [_prof(a), _prof(b)]})
    _near((await _bounds(session, r["bodies"][0]))["size"], [10, 10, 20])
    await _fails(session, "loft", {"sections": [_prof(a)]}, "INVALID_PARAMS")


async def _loft_rails(session):
    a = await _circle(session, 5)
    b = await _circle(session, 5, plane=await _offset_plane(session, 20))
    _, rail = await _line(session, (5, 0), (5, -20), "XZ")
    r = await _m(session, "loft", {"sections": [_prof(a), _prof(b)], "rails": [rail]})
    assert len(r["bodies"]) == 1, r


async def _loft_centerline(session):
    a = await _circle(session, 5)
    b = await _circle(session, 5, plane=await _offset_plane(session, 20))
    _, center = await _line(session, (0, 0), (0, -20), "XZ")
    r = await _m(session, "loft", {"sections": [_prof(a), _prof(b)], "centerline": center})
    assert len(r["bodies"]) == 1, r
    _, rail = await _line(session, (5, 0), (5, -20), "XZ")
    await _fails(session, "loft", {"sections": [_prof(a), _prof(b)], "rails": [rail], "centerline": center},
                 "INVALID_PARAMS")


# --- fillet, chamfer, shell ------------------------------------------------------------------


async def _edge_feature(session, act, params):
    body, _ = await _solid(session, (0, 0), (20, 20), 10)
    edge = await _vertical_edge(session, body)
    env = await _w(session, act, dict(params, edges=[edge]))
    assert _delta(env) == 0, env["changes"]
    assert await _nfaces(session, body) == 7, await _nfaces(session, body)
    return body


async def _fillet(session):
    body = await _edge_feature(session, "fillet", {"radius": 2})
    assert any(f["surface_type"] == "cylinder" for f in await _faces(session, body))


async def _chamfer_equal(session):
    await _edge_feature(session, "chamfer", {"chamfer_type": "equal_distance", "distance": 2})


async def _chamfer_two(session):
    await _edge_feature(session, "chamfer", {"chamfer_type": "two_distances", "distance": 2, "distance_two": 3})


async def _chamfer_angle(session):
    await _edge_feature(session, "chamfer", {"chamfer_type": "distance_angle", "distance": 2, "angle": 45})


async def _shell(session):
    body, _ = await _solid(session, (0, 0), (20, 20), 10)
    top = await _face(session, body, normal=[0, 0, 1], z=10)
    await _w(session, "shell", {"faces": [top], "thickness": 2})
    assert await _nfaces(session, body) > 6
    await _fails(session, "shell", {"thickness": 2}, "INVALID_PARAMS")


# --- mirror, hole, patterns ------------------------------------------------------------------


async def _mirror_bodies(session):
    body, _ = await _solid(session, (10, 0), (20, 10), 5)
    env = await _w(session, "mirror", {"entities": [body], "plane": "YZ"})
    assert _delta(env) == 1, env["changes"]
    b = await _bounds(session, env["result"]["bodies"][0])
    _near(b["min"], [-20, 0, 0])


async def _mirror_features(session):
    _, feat = await _solid(session, (10, 0), (20, 10), 5)
    env = await _w(session, "mirror", {"entities": [feat["feature"]], "plane": "YZ"})
    assert _delta(env) == 1, env["changes"]
    b = await _bounds(session, env["result"]["bodies"][0])
    _near(b["min"], [-20, 0, 0])


async def _plate(session):
    body, _ = await _solid(session, (0, 0), (40, 40), 10)
    return body, await _face(session, body, normal=[0, 0, 1], z=10)


async def _hole_simple(session):
    body, top = await _plate(session)
    await _w(session, "hole", {"face": top, "point": [20, 20, 10], "diameter": 5, "depth": 6})
    assert await _nfaces(session, body) == 8
    await _fails(session, "hole", {"face": top, "point": [20, 20, 10], "diameter": 5}, "INVALID_PARAMS")


async def _hole_counterbore(session):
    body, top = await _plate(session)
    await _w(session, "hole", {"hole_type": "counterbore", "face": top, "point": [20, 20, 10], "diameter": 5,
                               "counterbore_diameter": 10, "counterbore_depth": 2, "depth": 8})
    assert await _nfaces(session, body) > 8


async def _hole_countersink(session):
    body, top = await _plate(session)
    await _w(session, "hole", {"hole_type": "countersink", "face": top, "point": [20, 20, 10], "diameter": 5,
                               "countersink_diameter": 10, "through_all": True})
    assert any(f["surface_type"] == "cone" for f in await _faces(session, body))


async def _hole_sketch_points(session):
    body, top = await _plate(session)
    sk, pts = await _points(session, [(10, 10), (30, 30)], plane=top)
    await _w(session, "hole", {"sketch_points": pts, "diameter": 4, "depth": 5})
    assert await _nfaces(session, body) == 10


async def _rect_pattern(session):
    body, _ = await _solid(session, (0, 0), (5, 5), 5)
    env = await _w(session, "rect_pattern", {"entities": [body], "direction_one": "X", "quantity_one": 3,
                                             "distance_one": 10, "direction_two": "Y", "quantity_two": 2,
                                             "distance_two": 10})
    assert _delta(env) == 5, env["changes"]
    assert env["result"].get("pattern_count", 6) == 6, env["result"]
    await _fails(session, "rect_pattern", {"entities": [body], "direction_one": "X", "quantity_one": 600,
                                           "distance_one": 1}, "INVALID_PARAMS")


async def _circ_pattern_faces(session):
    body, _ = await _solid(session, (-30, -30), (30, 30), 5)
    sk = await _circle(session, 3, (20, 0))
    await _m(session, "extrude", {"profile": _prof(sk), "distance": 5, "operation": "cut",
                                  "participant_bodies": [body]})
    hole = await _face(session, body, kind="cylinder")
    n = await _nfaces(session, body)
    await _w(session, "circ_pattern", {"entities": [hole], "axis": "Z", "quantity": 4})
    assert await _nfaces(session, body) == n + 3, (n, await _nfaces(session, body))


# --- threads ---------------------------------------------------------------------------------


async def _thread_query(session):
    types = await _m(session, "thread_query", {})
    assert types["level"] == "types" and types["total"] > 0, types
    t = "ISO Metric profile"
    assert t in types["items"], types["items"][:20]
    sizes = await _m(session, "thread_query", {"thread_type": t})
    assert sizes["level"] == "sizes" and sizes["total"] > 0, sizes
    size = "6.0" if "6.0" in sizes["items"] else sizes["items"][0]
    desigs = await _m(session, "thread_query", {"thread_type": t, "size": size})
    assert desigs["level"] == "designations" and desigs["total"] > 0, desigs
    classes = await _m(session, "thread_query", {"thread_type": t, "size": size, "designation": desigs["items"][0]})
    assert classes["level"] == "classes" and classes["total"] > 0, classes
    await _fails(session, "thread_query", {"thread_type": "No such thread type"}, "INVALID_PARAMS")


async def _threaded_cylinder(session, modeled, diameter=10):
    r = await _m(session, "cylinder", {"diameter": diameter, "height": 20})
    body = r["bodies"][0]
    face = await _face(session, body, kind="cylinder")
    env = await _w(session, "thread", {"faces": [face], "modeled": modeled})
    res = env["result"]
    assert res["designation"] and res["thread_class"] and res["internal"] is False, res
    return res


async def _thread_modeled(session):
    await _threaded_cylinder(session, True)


async def _thread_cosmetic(session):
    await _threaded_cylinder(session, False, 8)


async def _list_threads(session):
    res = await _threaded_cylinder(session, False, 6)
    r = await _m(session, "list_threads", {})
    mine = [i for i in r["items"] if i["feature"]["token"] == res["feature"]["token"]]
    assert len(mine) == 1, r
    assert mine[0]["modeled"] is False and mine[0]["face_count"] == 1 and mine[0]["designation"] == res["designation"], mine


# --- coil ------------------------------------------------------------------------------------

_COIL = {"diameter": 20, "revolutions": 3, "height": 15, "section_size": 2}


async def _request(session, feature):
    r = await _m(session, "find_attributes", {"group": "defusion", "attribute": "request"})
    mine = [i for i in r["items"] if i["parent"] and i["parent"]["token"] == feature["token"]]
    assert len(mine) == 1, r
    return json.loads(mine[0]["value"])


async def _coil_ok(session, **kw):
    params = dict(_COIL, **kw)
    for k, v in list(params.items()):
        if v is None:
            del params[k]
    env = await _w(session, "coil", params)
    r = env["result"]
    if params.get("operation", "new_body") == "new_body":
        assert _delta(env) == 1, env["changes"]
        assert len(r["bodies"]) == 1, r
    await _do(session, "sketch", "info", {"sketch": r["sketch"]})
    assert r["parameters"], r
    req = await _request(session, r["feature"])
    assert req["params"]["coil_type"] == params.get("coil_type", "revolution_height"), req
    assert req["params"]["section"] == params.get("section", "circular"), req
    return env


async def _coil_revolution_height(session):
    await _coil_ok(session)


async def _coil_revolution_pitch(session):
    await _coil_ok(session, coil_type="revolution_pitch", height=None, pitch=5)


async def _coil_height_pitch(session):
    await _coil_ok(session, coil_type="height_pitch", revolutions=None, pitch=5)


async def _coil_spiral(session):
    await _coil_ok(session, coil_type="spiral", height=None, pitch=5)
    await _fails(session, "coil", dict(_COIL, coil_type="spiral", height=None, pitch=5, taper_angle=5), "INVALID_PARAMS")


async def _coil_section(session, section):
    await _coil_ok(session, section=section)


async def _coil_circular(session):
    await _coil_section(session, "circular")


async def _coil_square(session):
    await _coil_section(session, "square")


async def _coil_tri_external(session):
    await _coil_section(session, "triangular_external")


async def _coil_tri_internal(session):
    await _coil_section(session, "triangular_internal")


async def _coil_join_participant(session):
    far = dict(_COIL, center=[1000, 0])  # away from the bodies of other scenarios, which a join would also merge
    body, _ = await _solid(session, (1000, -20), (1020, 20), 10)
    await _fails(session, "coil", dict(far, participant_bodies=[body]), "INVALID_PARAMS")
    # this Fusion build has no CoilFeature.participantBodies: UNSUPPORTED (design § 5.3), else the join succeeds
    env = await _call(session, "model", "coil", dict(far, operation="join", participant_bodies=[body]))
    if env["ok"]:
        assert _delta(env) == 0, env["changes"]
        return
    assert env["error"]["code"] == "UNSUPPORTED" and "participantBodies" in env["error"]["message"], env
    env = await _w(session, "coil", dict(far, operation="join"))
    assert _delta(env) == 0, env["changes"]


async def _coil_invalid_section_size(session):
    before = (await _do(session, "sketch", "list", {}))["total"]
    await _fails(session, "coil", dict(_COIL, coil_type="revolution_pitch", height=None, pitch=5, section_size=6),
                 "INVALID_PARAMS")
    assert (await _do(session, "sketch", "list", {}))["total"] == before


async def _coil_edit_parameter(session):
    env = await _coil_ok(session)
    feat = env["result"]["feature"]
    params = (await _m(session, "parameter_list", {"created_by": feat}))["items"]
    assert params == [{**p} for p in params] and len(params) >= len(env["result"]["parameters"]), params
    height = [p for p in params if abs(p["value"] - 15) < 1e-6 and p["unit"] == "mm"]
    assert height, params
    mod = await _w(session, "parameter_modify", {"edits": [{"name": height[0]["name"], "expression": "30 mm"}]})
    after = mod["changes"]["bbox"]
    assert after[0] != after[1], after


async def _coil_request_attribute(session):
    env = await _coil_ok(session, coil_type="height_pitch", revolutions=None, pitch=5, section="square")
    req = await _request(session, env["result"]["feature"])
    assert req["format"] == "defusion.request/1" and req["tool"] == "model" and req["action"] == "coil", req
    assert req["params"]["coil_type"] == "height_pitch" and req["params"]["section"] == "square", req
    assert req["params"]["plane"] == "XY" and req["params"]["diameter"] == "20 mm", req


# --- draft, split, offset, scale, move, combine, rename --------------------------------------


async def _draft(session):
    body, _ = await _solid(session, (0, 0), (20, 20), 20)
    side = await _face(session, body, normal=[1, 0, 0])
    env = await _w(session, "draft", {"faces": [side], "pull_plane": "XY", "angle": 5})
    assert _delta(env) == 0, env["changes"]


async def _split_body(session):
    body, _ = await _solid(session, (0, 0), (20, 20), 20)
    env = await _w(session, "split_body", {"bodies": [body], "tool": await _offset_plane(session, 10)})
    assert _delta(env) == 1, env["changes"]


async def _split_face(session):
    body, _ = await _solid(session, (0, 0), (20, 20), 20)
    top = await _face(session, body, normal=[0, 0, 1], z=20)
    n = await _nfaces(session, body)
    await _w(session, "split_face", {"faces": [top], "tool": await _offset_plane(session, 10, "YZ")})
    assert await _nfaces(session, body) == n + 1


async def _offset_faces(session):
    body, _ = await _solid(session, (0, 0), (20, 20), 10)
    top = await _face(session, body, normal=[0, 0, 1], z=10)
    await _w(session, "offset_faces", {"faces": [top], "distance": 2})
    _near((await _bounds(session, body))["max"], [20, 20, 12])


async def _scale(session):
    body, _ = await _solid(session, (0, 0), (10, 10), 10)
    await _w(session, "scale", {"entities": [body], "factor": 2})
    _near((await _bounds(session, body))["size"], [20, 20, 20])
    await _fails(session, "scale", {"entities": [body], "factor": 0}, "INVALID_PARAMS")


async def _move_translate(session):
    body, _ = await _solid(session, (0, 0), (10, 10), 10)
    await _w(session, "move", {"bodies": [body], "translate": [30, 0, 5]})
    _near((await _bounds(session, body))["min"], [30, 0, 5])
    await _fails(session, "move", {"bodies": [body]}, "INVALID_PARAMS")


async def _move_rotate(session):
    body, _ = await _solid(session, (0, 0), (10, 10), 10)
    await _w(session, "move", {"bodies": [body], "rotate_axis": "Z", "angle": 90})
    _near((await _bounds(session, body))["min"], [-10, 0, 0])


async def _combine(session):
    a, _ = await _solid(session, (0, 0), (20, 20), 10)
    b, _ = await _solid(session, (10, 10), (30, 30), 10)
    env = await _w(session, "combine", {"target": a, "tools": [b], "operation": "join"})
    assert _delta(env) == -1, env["changes"]
    await _fails(session, "combine", {"target": a, "tools": [a]}, "INVALID_PARAMS")


async def _rename(session):
    body, _ = await _solid(session, (0, 0), (10, 10), 10)
    r = await _m(session, "rename", {"target": body, "name": "LiveBody"})
    assert r["name"] == "LiveBody" and r["target"]["name"] == "LiveBody", r
    await _fails(session, "rename", {"target": body, "name": "  "}, "INVALID_PARAMS")


# --- primitives ------------------------------------------------------------------------------


async def _cylinder(session):
    r = await _m(session, "cylinder", {"diameter": 10, "height": 20, "center": [5, 5], "name": "LiveCyl"})
    assert r["sketch"] and len(r["bodies"]) == 1 and r["bodies"][0]["name"] == "LiveCyl", r
    b = await _bounds(session, r["bodies"][0])
    _near(b["min"], [0, 0, 0])
    _near(b["max"], [10, 10, 20])


async def _sphere(session):
    r = await _m(session, "sphere", {"diameter": 10})
    _near((await _bounds(session, r["bodies"][0]))["size"], [10, 10, 10])


async def _torus(session):
    r = await _m(session, "torus", {"major_diameter": 30, "minor_diameter": 6})
    _near((await _bounds(session, r["bodies"][0]))["size"], [36, 6, 36])
    await _fails(session, "torus", {"major_diameter": 6, "minor_diameter": 6}, "INVALID_PARAMS")


# --- surfaces --------------------------------------------------------------------------------


async def _patch_of(session, p1, p2, plane="XY"):
    sk, curves = await _rect(session, p1, p2, plane)
    return (await _m(session, "surface_patch", {"boundary": curves}))["bodies"][0]


async def _surface_patch(session):
    body = await _patch_of(session, (0, 0), (20, 20))
    assert len(await _faces(session, body)) == 1


async def _surface_stitch(session):
    a = await _patch_of(session, (0, 0), (10, 10))
    b = await _patch_of(session, (0, 0), (10, -10), "XZ")  # shares the x axis edge with the first patch
    env = await _w(session, "surface_stitch", {"bodies": [a, b]})
    assert _delta(env) == -1, env["changes"]


async def _surface_thicken(session):
    body = await _patch_of(session, (0, 0), (20, 20))
    face = (await _faces(session, body))[0]["ref"]
    env = await _w(session, "surface_thicken", {"faces": [face], "thickness": 2})
    assert _delta(env) == 1, env["changes"]


async def _surface_ruled(session):
    body = await _patch_of(session, (0, 0), (20, 20))
    edge = (await _edges(session, body))[0]["ref"]
    env = await _ok_or_unsupported(session, "surface_ruled", {"edges": [edge], "distance": 5})
    if env["ok"]:
        assert _delta(env) == 1, env["changes"]


async def _surface_trim(session):
    body = await _patch_of(session, (0, 0), (20, 20))
    tool = await _offset_plane(session, 10, "YZ")
    err = await _fails(session, "surface_trim", {"tool": tool, "remove_cells": [99]}, "INVALID_PARAMS")
    assert any("cell" in h for h in err["hints"]), err
    await _w(session, "surface_trim", {"tool": tool, "remove_cells": [0]})


# --- sheet metal -----------------------------------------------------------------------------


async def _sheet_chain(session):
    done = []
    sk, _ = await _rect(session, (0, 0), (50, 30))
    env = await _ok_or_unsupported(session, "sheet_flange", {"profile": _prof(sk), "rule": "Steel (mm)"})
    if not env["ok"]:
        return _skipped(env, done)
    body = env["result"]["bodies"][0]
    done.append("base")
    top = await _top_face(session, body)
    edge = None
    for e in await _edges(session, body):
        if e["curve_type"] == "line" and abs(e["length"] - 30) < 1e-3 and abs(e["start"][0] - 50) < 1e-6 \
                and e["start"][2] > 0 and e["end"][2] > 0:
            edge = e["ref"]
            break
    assert edge, "no flange edge"
    env = await _ok_or_unsupported(session, "sheet_flange", {"edges": [edge], "height": 10})
    if not env["ok"]:
        return _skipped(env, done)
    done.append("edge")
    top = await _top_face(session, body)
    _, line = await _line(session, (25, -5), (25, 35), plane=top)
    env = await _ok_or_unsupported(session, "sheet_bend", {"stationary_face": top, "lines": [line]})
    if not env["ok"]:
        return _skipped(env, done)
    done.append("bend")
    top = await _top_face(session, body)
    env = await _ok_or_unsupported(session, "sheet_unfold", {"stationary_face": top})
    if not env["ok"]:
        return _skipped(env, done)
    done.append("unfold")
    env = await _ok_or_unsupported(session, "sheet_flat_pattern", {"stationary_face": await _top_face(session, body)})
    if not env["ok"]:
        return _skipped(env, done)
    done.append("flat_pattern")
    print("     sheet_chain steps: %s" % ", ".join(done))


def _skipped(env, done):
    print("     sheet_chain stopped after [%s]: %s" % (", ".join(done), env["error"]["message"]))


async def _top_face(session, body):
    best = None
    for f in await _faces(session, body):
        if f["surface_type"] == "plane" and abs(f["normal"][2] - 1) < 1e-6 and (best is None or f["area"] > best["area"]):
            best = f
    assert best, "no top face"
    return best["ref"]


async def _sheet_not_sheet_metal(session):
    body, _ = await _solid(session, (0, 0), (20, 20), 10)
    edge = (await _edges(session, body))[0]["ref"]
    await _fails(session, "sheet_flange", {"edges": [edge], "height": 5}, "INVALID_PARAMS")


# --- construction geometry -------------------------------------------------------------------


async def _construction_plane(session):
    off = (await _m(session, "construction_plane", {"method": "offset", "plane": "XY", "offset": 10}))["plane"]
    for params in (
        {"method": "angle", "axis": "X", "angle": 30, "plane": "XY"},
        {"method": "midplane", "plane": "XY", "plane_two": off},
    ):
        assert (await _m(session, "construction_plane", params))["plane"]["type"] == "ConstructionPlane", params
    _, pts = await _points(session, [(0, 0), (10, 0), (0, 10)])
    assert (await _m(session, "construction_plane", {"method": "three_points", "points": pts}))["plane"]
    cyl = (await _m(session, "cylinder", {"diameter": 10, "height": 20}))["bodies"][0]
    face = await _face(session, cyl, kind="cylinder")
    assert (await _m(session, "construction_plane", {"method": "tangent", "face": face, "angle": 0,
                                                     "plane": "XZ"}))["plane"]
    _, line = await _line(session, (0, 0), (0, -30), "XZ")
    assert (await _m(session, "construction_plane", {"method": "along_path", "path": line, "position": 0.5}))["plane"]
    await _fails(session, "construction_plane", {"method": "offset"}, "INVALID_PARAMS")


async def _construction_axis(session):
    off = await _offset_plane(session, 10, "YZ")
    assert (await _m(session, "construction_axis", {"method": "two_planes", "plane": "XY", "plane_two": off}))["axis"]
    _, pts = await _points(session, [(0, 0), (10, 10)])
    assert (await _m(session, "construction_axis", {"method": "two_points", "points": pts}))["axis"]
    body, _ = await _solid(session, (0, 0), (20, 20), 10)
    edge = await _vertical_edge(session, body)
    assert (await _m(session, "construction_axis", {"method": "edge", "edge": edge}))["axis"]
    cyl = (await _m(session, "cylinder", {"diameter": 10, "height": 20}))["bodies"][0]
    face = await _face(session, cyl, kind="cylinder")
    assert (await _m(session, "construction_axis", {"method": "circular_face", "face": face}))["axis"]
    top = await _face(session, body, normal=[0, 0, 1], z=10)
    _, (pt,) = await _points(session, [(5, 5)], plane=top)
    assert (await _m(session, "construction_axis", {"method": "normal_to_face", "face": top, "point": pt}))["axis"]


async def _ucs(session):
    _, (pt,) = await _points(session, [(5, 5)])
    env = await _ok_or_unsupported(session, "ucs", {"origin": pt, "angle_z": 30})
    if env["ok"]:
        assert env["result"]["ucs"], env


# --- parameters ------------------------------------------------------------------------------


async def _parameters(session):
    a = (await _m(session, "parameter_create", {"name": "live_len", "expression": "12 mm", "comment": "c"}))["parameter"]
    assert a["name"] == "live_len" and a["kind"] == "user" and abs(a["value"] - 12) < 1e-9, a
    await _m(session, "parameter_create", {"name": "live_ang", "expression": "30 deg", "unit": "deg"})
    await _fails(session, "parameter_create", {"name": "live_len", "expression": "1 mm"}, "INVALID_PARAMS")
    listed = (await _m(session, "parameter_list", {"kind": "user", "name": "live_*"}))["items"]
    assert sorted(p["name"] for p in listed) == ["live_ang", "live_len"], listed
    mod = await _m(session, "parameter_modify", {"edits": [{"name": "live_len", "expression": "15 mm"},
                                                          {"name": "live_ang", "expression": "45 deg"}]})
    assert [p["name"] for p in mod["parameters"]] == ["live_len", "live_ang"], mod
    assert abs(mod["parameters"][0]["value"] - 15) < 1e-9, mod
    await _fails(session, "parameter_modify", {"edits": [{"name": "live_len", "expression": "garbage ("}]},
                 "INVALID_PARAMS")
    sk, _ = await _rect(session, (0, 0), (10, 10))
    await _m(session, "extrude", {"profile": _prof(sk), "distance": "live_len"})
    await _fails(session, "parameter_delete", {"names": ["live_len"]}, "CONFIRM_REQUIRED")
    gone = await _m(session, "parameter_delete", {"names": ["live_len", "live_ang"], "confirm": True})
    assert gone["deleted"] == ["live_ang"] and [f["name"] for f in gone["failed"]] == ["live_len"], gone


async def _materials(session):
    body, _ = await _solid(session, (0, 0), (10, 10), 10)
    lst = await _m(session, "material_list", {"query": "steel"})
    assert lst["total"] > 0, lst
    name = lst["items"][0]["name"]
    r = await _m(session, "material_assign", {"bodies": [body], "material": name, "library": lst["items"][0]["library"]})
    assert r["material"] == name, r
    assert (await _m(session, "material_list", {"source": "design"}))["total"] >= 1
    await _fails(session, "material_assign", {"bodies": [body], "material": "No such material"}, "NOT_FOUND")


async def _appearance_set(session):
    body, _ = await _solid(session, (0, 0), (10, 10), 10)
    r = await _m(session, "appearance_set", {"targets": [body], "appearance": "ABS (White)"})
    assert r["appearance"] == "ABS (White)", r
    face = (await _faces(session, body))[0]["ref"]
    await _m(session, "appearance_set", {"targets": [face], "appearance": "ABS (White)"})
    await _fails(session, "appearance_set", {"targets": [body], "appearance": "No such appearance"}, "NOT_FOUND")


async def _color_set(session):
    body, _ = await _solid(session, (0, 0), (10, 10), 10)
    r = await _m(session, "color_set", {"targets": [body], "color": "#ff8000"})
    assert r["color"] == "#FF8000FF" and r["appearance"] == "De-Fusion color FF8000FF", r
    again = await _m(session, "color_set", {"targets": [body], "color": "#FF8000"})
    assert again["appearance"] == r["appearance"], again
    await _fails(session, "color_set", {"targets": [body], "color": "orange"}, "INVALID_PARAMS")


# --- design settings -------------------------------------------------------------------------


async def _units(session):
    before = await _m(session, "units_get", {})
    assert before["default_length_units"] and before["output_units"] == {"length": "mm", "angle": "deg"}, before
    try:
        r = await _m(session, "units_set", {"length": "in"})
        assert r["display_length_units"] == "in", r
        assert (await _m(session, "units_get", {}))["display_length_units"] == "in"
    finally:
        await _m(session, "units_set", {"length": before["display_length_units"]})
    assert (await _m(session, "units_get", {}))["display_length_units"] == before["display_length_units"]


async def _find_attributes(session):
    r = await _m(session, "find_attributes", {"group": "live_no_such_group"})
    assert r["total"] == 0 and r["items"] == [], r
    r = await _m(session, "find_attributes", {})
    assert r["total"] >= 0 and "items" in r, r


async def _find_by_token(session):
    body, _ = await _solid(session, (0, 0), (10, 10), 10)
    r = await _m(session, "find_by_token", {"token": body["token"]})
    assert [e["token"] for e in r["entities"]] == [body["token"]], r
    assert (await _m(session, "find_by_token", {"token": "live-no-such-token"}))["entities"] == []


async def _visibility(session):
    body, _ = await _solid(session, (0, 0), (10, 10), 10)
    got = await _m(session, "visibility_get", {"targets": [body]})
    assert got["entities"][0]["light_bulb"] is True, got
    r = await _m(session, "visibility_set", {"targets": [body], "visible": False})
    assert r["entities"][0]["light_bulb"] is False, r
    r = await _m(session, "visibility_set", {"targets": [body], "visible": True})
    assert r["entities"][0]["light_bulb"] is True, r
    flags = (await _m(session, "visibility_get", {}))["flags"]
    assert flags, flags
    name, value = sorted(flags.items())[0]
    try:
        r = await _m(session, "visibility_set", {"flag": name, "value": not value})
        assert r["flags"][name] is (not value), r
    finally:
        await _m(session, "visibility_set", {"flag": name, "value": value})
    await _fails(session, "visibility_set", {"flag": "isNoSuchFlag", "value": True}, "INVALID_PARAMS")


async def _selection_sets(session):
    body, _ = await _solid(session, (0, 0), (10, 10), 10)
    env = await _ok_or_unsupported(session, "selection_set_create", {"name": "LiveSet", "entities": [body]})
    if not env["ok"]:
        return
    assert env["result"] == {"name": "LiveSet", "count": 1}, env["result"]
    sets = await _m(session, "selection_sets_list", {})
    assert any(s["name"] == "LiveSet" and s["count"] == 1 for s in sets["items"]), sets


async def _set_design_intent(session):
    env = await _ok_or_unsupported(session, "set_design_intent", {"intent": "part"})
    if env["ok"]:
        assert env["result"]["design_intent"] == "part", env


async def _set_modeling_in_assembly(session):
    # Fusion only accepts the setting in an assembly design
    env = await _ok_or_unsupported(session, "set_design_intent", {"intent": "assembly"})
    if not env["ok"]:
        return
    try:
        for enabled in (True, False):
            env = await _ok_or_unsupported(session, "set_modeling_in_assembly", {"enabled": enabled})
            if not env["ok"]:
                return
            assert env["result"] == {"enabled": enabled}, env
    finally:
        await _m(session, "set_design_intent", {"intent": "part"})


async def _activate_root(session):
    r = await _m(session, "activate_root", {})
    assert r["active_component"]["type"] == "Component", r


async def _compute_all(session):
    await _solid(session, (0, 0), (10, 10), 10)
    r = await _m(session, "compute_all", {})
    assert isinstance(r["timeline_health"], list), r


# --- destructive -----------------------------------------------------------------------------


async def _remove_body(session):
    body, _ = await _solid(session, (0, 0), (10, 10), 10)
    await _fails(session, "remove_body", {"body": body}, "CONFIRM_REQUIRED")
    env = await _w(session, "remove_body", {"body": body, "confirm": True})
    assert env["result"]["removed"]["token"] == body["token"] and env["result"]["feature"], env["result"]
    assert _delta(env) == -1, env["changes"]


async def _delete_entities(session):
    a, _ = await _rect(session, (0, 0), (10, 10))
    b, _ = await _rect(session, (20, 0), (30, 10))
    await _fails(session, "delete_entities", {"targets": [a]}, "CONFIRM_REQUIRED")
    r = await _m(session, "delete_entities", {"targets": [a, b], "confirm": True})
    assert [t["token"] for t in r["deleted"]] == [a["token"], b["token"]] and r["failed"] == [], r


# --- last: design type and configurations ----------------------------------------------------


async def _set_design_type(session):
    await _fails(session, "set_design_type", {"design_type": "direct"}, "CONFIRM_REQUIRED")
    try:
        r = await _m(session, "set_design_type", {"design_type": "direct", "confirm": True})
        assert r == {"design_type": "direct", "changed": True}, r
    finally:
        back = await _m(session, "set_design_type", {"design_type": "parametric", "confirm": True})
    assert back == {"design_type": "parametric", "changed": True}, back


async def _coil_direct_rejected(session):
    await _m(session, "set_design_type", {"design_type": "direct", "confirm": True})
    try:
        await _fails(session, "coil", dict(_COIL), "WRONG_DESIGN_TYPE")
    finally:
        await _m(session, "set_design_type", {"design_type": "parametric", "confirm": True})


async def _config(session):
    made = await _ok_or_unsupported(session, "create_configured_design", {})
    insp = await _ok_or_unsupported(session, "config_inspect", {})
    assert made["ok"] == insp["ok"], (made, insp)
    if made["ok"]:
        assert made["result"]["configured"] is True and insp["result"]["configured"] is True, insp["result"]


_ORDER = (
    ("extrude.distance", _extrude_distance), ("extrude.symmetric", _extrude_symmetric),
    ("extrude.two_sided", _extrude_two_sided), ("extrude.to_object", _extrude_to_object),
    ("extrude.taper_cut", _extrude_taper_cut),
    ("revolve", _revolve), ("sweep", _sweep), ("sweep.guide_rail_twist", _sweep_guide_rail_twist),
    ("loft", _loft), ("loft.rails", _loft_rails), ("loft.centerline", _loft_centerline),
    ("fillet", _fillet), ("chamfer.equal_distance", _chamfer_equal), ("chamfer.two_distances", _chamfer_two),
    ("chamfer.distance_angle", _chamfer_angle), ("shell", _shell),
    ("mirror.bodies", _mirror_bodies), ("mirror.features", _mirror_features),
    ("hole.simple", _hole_simple), ("hole.counterbore", _hole_counterbore), ("hole.countersink", _hole_countersink),
    ("hole.sketch_points", _hole_sketch_points), ("rect_pattern", _rect_pattern),
    ("circ_pattern.faces", _circ_pattern_faces),
    ("thread_query", _thread_query), ("thread.modeled", _thread_modeled), ("thread.cosmetic", _thread_cosmetic),
    ("list_threads", _list_threads),
    ("coil.revolution_height", _coil_revolution_height), ("coil.revolution_pitch", _coil_revolution_pitch),
    ("coil.height_pitch", _coil_height_pitch), ("coil.spiral", _coil_spiral),
    ("coil.section_circular", _coil_circular), ("coil.section_square", _coil_square),
    ("coil.section_triangular_external", _coil_tri_external),
    ("coil.section_triangular_internal", _coil_tri_internal),
    ("coil.join_participant", _coil_join_participant), ("coil.invalid_section_size", _coil_invalid_section_size),
    ("coil.edit_parameter", _coil_edit_parameter), ("coil.request_attribute", _coil_request_attribute),
    ("draft", _draft), ("split_body", _split_body), ("split_face", _split_face), ("offset_faces", _offset_faces),
    ("scale", _scale), ("move.translate", _move_translate), ("move.rotate", _move_rotate),
    ("combine", _combine), ("rename", _rename),
    ("cylinder", _cylinder), ("sphere", _sphere), ("torus", _torus),
    ("surface_patch", _surface_patch), ("surface_stitch", _surface_stitch), ("surface_thicken", _surface_thicken),
    ("surface_ruled", _surface_ruled), ("surface_trim", _surface_trim),
    ("sheet_chain", _sheet_chain, "model.sheet_flange"), ("sheet_flange.not_sheet_metal", _sheet_not_sheet_metal),
    ("construction_plane", _construction_plane), ("construction_axis", _construction_axis), ("ucs", _ucs),
    ("parameters", _parameters, "model.parameter_create"),
    ("materials", _materials, "model.material_assign"), ("appearance_set", _appearance_set),
    ("color_set", _color_set),
    ("units", _units, "model.units_set"), ("find_attributes", _find_attributes),
    ("find_by_token", _find_by_token), ("visibility", _visibility, "model.visibility_set"),
    ("selection_sets", _selection_sets, "model.selection_set_create"),
    ("set_design_intent", _set_design_intent), ("set_modeling_in_assembly", _set_modeling_in_assembly),
    ("activate_root", _activate_root), ("compute_all", _compute_all),
    ("remove_body", _remove_body), ("delete_entities", _delete_entities),
    ("set_design_type", _set_design_type), ("coil.direct_rejected", _coil_direct_rejected),
    ("config", _config, "model.create_configured_design"),
)

_STALE = ("deleted Object", "no entity with token")


def _retry_stale(fn):
    """Fusion intermittently invalidates a just-created entity between two calls; each scenario builds its own
    geometry, so a scenario that fails only with a stale-handle error is rerun once from scratch."""
    async def run(session):
        try:
            await fn(session)
        except Exception as exc:
            if not any(m in str(exc) for m in _STALE):
                raise
            print("     %s: stale entity (%s); rerunning once" % (fn.__name__, str(exc)[:80]))
            await fn(session)
    run.__name__ = fn.__name__
    return run


SCENARIOS = [(e[0], e[2] if len(e) > 2 else "model." + e[0].split(".")[0], _retry_stale(e[1])) for e in _ORDER]
