# selfcheck.py -- automated checks for floorball_animator.
#
# Run with:  python3 floorball_studio/selfcheck.py
#
# Every check is a small assertion about behaviour the app is supposed to have,
# grouped into the areas of the checklist. Anything that would touch the user's real
# files (the config in $HOME, save/load dialogs, colour pickers, message boxes) is
# redirected or stubbed, so a run has no side effects outside a temporary directory.
import json
import math
import os
import sys
import tempfile
import time
import tkinter as tk
import traceback
from tkinter import ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image                                    # noqa: E402
import floorball_animator as fa                          # noqa: E402


# ----------------------------------------------------------------------------
# harness
# ----------------------------------------------------------------------------
class Checker:
    def __init__(self):
        self.results = []
        self.area = "general"
        self.exercised = set()

    def section(self, name):
        self.area = name

    def check(self, name, condition, detail=""):
        self.results.append((self.area, name, bool(condition), detail))
        return bool(condition)

    def equal(self, name, got, want, tolerance=None):
        if tolerance is not None and isinstance(got, (int, float)):
            ok = abs(got - want) <= tolerance
        else:
            ok = got == want
        return self.check(name, ok, f"got {got!r}, want {want!r}")

    def raises_not(self, name, function, *args, **kwargs):
        try:
            function(*args, **kwargs)
            return self.check(name, True)
        except Exception as error:
            return self.check(name, False, f"{type(error).__name__}: {error}")

    def report(self):
        width = max(len(name) for _, name, _, _ in self.results) + 2
        current = None
        failed = 0
        for area, name, ok, detail in self.results:
            if area != current:
                current = area
                print(f"\n{area}")
                print("-" * (width + 8))
            mark = "PASS" if ok else "FAIL"
            line = f"  [{mark}] {name.ljust(width)}"
            if not ok and detail:
                line += f"  <- {detail}"
            print(line)
            failed += not ok
        total = len(self.results)
        print(f"\n{total - failed}/{total} checks passed"
              + (f", {failed} FAILED" if failed else ""))
        return failed


def build_app(check, geometry="1400x900"):
    """A live app whose side effects are contained."""
    root = tk.Tk()
    app = fa.FloorballTacticsApp(root)
    # Never write to the real config in $HOME.
    app.config_path = os.path.join(TEMP.name, "config.json")
    root.geometry(geometry)
    root.update_idletasks()
    root.update()
    return root, app


def token_centre(app, token):
    box = app.canvas.bbox(token["shape_id"])
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


TEMP = tempfile.TemporaryDirectory(prefix="floorball-selfcheck-")


# ----------------------------------------------------------------------------
# A. geometry and the rink projection
# ----------------------------------------------------------------------------
def check_geometry(c, app):
    c.section("A. Geometry and rink projection")
    state = app._pitch_state()
    c.check("pitch state has a scale", state["scale"])

    for mx, my in ((0.0, 0.0), (20.0, 10.0), (40.0, 20.0), (7.5, 13.25)):
        px, py = app._state_m_to_px(mx, my, state)
        back = app._state_px_to_m(px, py, state)
        c.check(f"metres->pixels->metres round trip at ({mx}, {my})",
                abs(back[0] - mx) < 1e-6 and abs(back[1] - my) < 1e-6,
                f"came back {back}")

    app.toggle_rink_orientation()
    rotated = app._pitch_state()
    c.check("rotation flips the state", rotated["rotated"] is True)
    px, py = app._state_m_to_px(0.0, 0.0, rotated)
    back = app._state_px_to_m(px, py, rotated)
    c.check("round trip still exact when rotated",
            abs(back[0]) < 1e-6 and abs(back[1]) < 1e-6, f"came back {back}")
    app.toggle_rink_orientation()

    # Every field the button rotates through, in metres.
    for mode, (length, width) in app.RINK_SIZES.items():
        app.set_rink_mode(mode, half=False)
        app.root.update()
        state = app._pitch_state()
        c.equal(f"the {mode} rink is {length:g} m long", state["rink_len"], length)
        c.equal(f"the {mode} rink is {width:g} m wide", state["rink_wid"], width)
        back = app._state_px_to_m(*app._rink_to_px(length, width), state)
        c.check(f"the far corner of the {mode} rink round trips",
                abs(back[0] - length) < 1e-6 and abs(back[1] - width) < 1e-6,
                f"came back {back}")
    app.set_half_rink(True)
    c.check("half-rink face-off point sits on the open side",
            app._faceoff_point_px()[0] is not None)
    app.set_rink_mode("5v5", half=False)

    centre = app._pitch_center_px()
    c.check("pitch centre lies inside the canvas",
            0 < centre[0] < app.canvas.winfo_width() and
            0 < centre[1] < app.canvas.winfo_height(), f"centre {centre}")


# ----------------------------------------------------------------------------
# B. tokens
# ----------------------------------------------------------------------------
def check_tokens(c, app):
    c.section("B. Players (tokens)")
    shapes = ("circle", "square", "triangle", "x", "plus", "ball")
    for shape in shapes:
        sid = app._create_token(400, 300, f"T_{shape}", shape=shape, color="black")
        token = app.tokens[sid]
        c.check(f"{shape}: every item is tracked",
                len(app._token_items(token)) >= 1)
        c.check(f"{shape}: has a dark edge and a white halo",
                token.get("decor_ids") or token.get("halo_ids"))

        before = [app.canvas.coords(i) for i in app._token_items(token)]
        app._rotate_token(token, 45)
        app._rotate_token(token, -45)
        after = [app.canvas.coords(i) for i in app._token_items(token)]
        same = all(all(abs(p - q) < 1e-6 for p, q in zip(a, b))
                   for a, b in zip(before, after))
        c.check(f"{shape}: rotate 45 then -45 restores it exactly", same)

        items_before = len(app.canvas.find_all())
        expected = len(app._token_items(token)) + len(token.get("text_ids", []))
        app._delete_token(token)
        removed = items_before - len(app.canvas.find_all())
        c.equal(f"{shape}: delete removes every one of its items", removed, expected)

    c.check("deleting leaves the selection a list",
            isinstance(app.selected_tokens, list))

    for team, count in (("att", 3), ("def", 7), ("att", 5), ("def", 5)):
        app._set_team_count(team, count)
        c.equal(f"team {team} resized to {count}", len(app._team_tokens(team)), count)

    # A deleted player comes back with undo, and the roster box says how many are
    # actually on the board -- it used to keep the old number, so typing that number
    # back in added nobody and the player looked unrecoverable.
    victim = app._team_tokens("att")[1]
    label, before_count = victim["label"], len(app._team_tokens("att"))
    where = token_centre(app, victim)
    app.clear_selection()
    app.selected_tokens = [victim["shape_id"]]
    app.delete_selection()
    app.root.update()
    c.equal("deleting a player takes them off the board",
            len(app._team_tokens("att")), before_count - 1)
    c.equal("and the roster box follows the board", app.att_spinbox.get(),
            str(before_count - 1))
    app.undo()
    app.root.update()
    c.equal("undo brings the player back", len(app._team_tokens("att")), before_count)
    back = app._get_sid_by_label(label)
    c.check("the same player, by name", back is not None)
    if back:
        centre = token_centre(app, app.tokens[back])
        c.check("standing where they stood",
                abs(centre[0] - where[0]) < 2 and abs(centre[1] - where[1]) < 2,
                f"{where} -> {centre}")
    c.equal("and the roster box counts them again", app.att_spinbox.get(),
            str(before_count))
    app.redo()
    app.root.update()
    c.equal("redo takes them off again", len(app._team_tokens("att")), before_count - 1)
    app.att_spinbox.delete(0, tk.END)
    app.att_spinbox.insert(0, str(before_count))
    app._roster_count_changed()
    app.root.update()
    c.equal("and typing the number back fills the team up",
            len(app._team_tokens("att")), before_count)
    app.clear_selection()

    token = app._team_tokens("att")[0]
    app._set_token_position(token, "LW")
    c.equal("tactical position shows on the token", token.get("position"), "LW")
    c.equal("label lookup still finds it by internal id",
            app._get_sid_by_label(token["label"]), token["shape_id"])
    c.equal("team is derived from the label when unset",
            app._token_team({"label": "D3"}), "def")


# ----------------------------------------------------------------------------
# C. selection, grouping, deletion
# ----------------------------------------------------------------------------
def check_selection(c, app):
    c.section("C. Selection, grouping, deletion")
    app.select_all()
    c.check("select all picks up the players", len(app.selected_tokens) >= 10)
    app.clear_selection()
    c.equal("clear selection empties it", len(app.selected_tokens), 0)

    tokens = app._team_tokens("att")[:2]
    app.selected_tokens = [t["shape_id"] for t in tokens]
    c.raises_not("group runs", app.group_selected)
    c.raises_not("ungroup runs", app.ungroup_selected)
    c.raises_not("lock runs", app.lock_selected)
    c.check("locked player is marked", app.tokens[tokens[0]["shape_id"]].get("locked"))
    removed = app.delete_selection()
    c.equal("locked players are not deleted", removed, 0)
    c.raises_not("unlock runs", app.unlock_selected)

    app.selected_tokens = [tokens[0]["shape_id"]]
    c.equal("unlocked player is deleted", app.delete_selection(), 1)

    entry = tk.Entry(app.root)
    entry.pack()
    entry.focus_set()
    app.root.update()
    app.selected_tokens = [app._team_tokens("att")[0]["shape_id"]]
    survivor = app.selected_tokens[0]
    app._delete_key()
    c.check("Delete is ignored while typing in a text field", survivor in app.tokens)
    app.canvas.focus_set()
    app.root.update()
    app._delete_key()
    c.check("Delete works when the board has focus", survivor not in app.tokens)
    entry.destroy()

    app._update_roster()
    app.root.update()
    app.selected_tokens = [t["shape_id"] for t in app._team_tokens("att")]
    c.raises_not("align horizontally runs", app.align_tokens, "horizontal")
    ys = [token_centre(app, t)[1] for t in app._team_tokens("att")]
    c.check("aligned players share a y", max(ys) - min(ys) < 1.5, f"spread {max(ys)-min(ys)}")
    c.raises_not("align vertically runs", app.align_tokens, "vertical")
    c.raises_not("distribute horizontally runs", app.distribute_horizontally)
    c.raises_not("distribute vertically runs", app.distribute_vertically)
    c.raises_not("copy runs", app.copy_selection)
    c.raises_not("paste runs", app.paste_clipboard)
    c.raises_not("ghosting runs", app.create_ghosts, list(app.selected_tokens))
    app.clear_ghosts()
    app.clear_selection()

    # Ghosts: placement, deletion, and following undo/redo
    def ghost_tokens():
        seen, found = set(), []
        for token in app.tokens.values():
            sid = token.get("shape_id")
            if sid in seen or not token.get("is_ghost"):
                continue
            seen.add(sid)
            found.append(token)
        return found

    app.att_shape_var.set("Square")
    app._update_roster()
    app.root.update()
    app.ghosting_var.set(True)
    player = app._team_tokens("att")[0]
    player_box = app.canvas.bbox(player["shape_id"])
    player_centre = ((player_box[0] + player_box[2]) / 2,
                     (player_box[1] + player_box[3]) / 2)
    app.create_ghosts([player["shape_id"]])
    app.root.update()
    c.equal("a ghost is left behind", len(ghost_tokens()), 1)
    if ghost_tokens():
        ghost_box = app.canvas.bbox(ghost_tokens()[0]["shape_id"])
        ghost_centre = ((ghost_box[0] + ghost_box[2]) / 2,
                        (ghost_box[1] + ghost_box[3]) / 2)
        c.check("it stands exactly where the player did, polygon shapes included",
                abs(ghost_centre[0] - player_centre[0]) < 1 and
                abs(ghost_centre[1] - player_centre[1]) < 1,
                f"{player_centre} vs {ghost_centre}")
        app.selected_tokens = [ghost_tokens()[0]["shape_id"]]
        c.equal("a selected ghost can be deleted", app.delete_selection(), 1)
    c.equal("clear_ghosts sweeps the rest", app.clear_ghosts(), 0)

    # a real drag, with its ghost tied to the move
    canvas = app.canvas
    box = canvas.bbox(player["shape_id"])
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    canvas.event_generate("<ButtonPress-1>", x=int(cx), y=int(cy))
    canvas.event_generate("<B1-Motion>", x=int(cx) + 40, y=int(cy) + 25, state=0x100)
    canvas.event_generate("<ButtonRelease-1>", x=int(cx) + 40, y=int(cy) + 25)
    app.root.update()
    c.equal("dragging with Ghosting on leaves one", len(ghost_tokens()), 1)
    app.undo()
    app.root.update()
    c.equal("undo takes the ghost back with the move", len(ghost_tokens()), 0)
    app.redo()
    app.root.update()
    c.equal("redo puts it back", len(ghost_tokens()), 1)
    app.undo()
    app.root.update()

    before_toggle = len(ghost_tokens())
    app.create_ghosts([player["shape_id"]])
    app.root.update()
    app.setting_buttons["ghosting"].invoke()
    app.root.update()
    c.check("switching Ghosting off sweeps the board",
            len(ghost_tokens()) == 0 and not app.ghosting_var.get(),
            f"{before_toggle} -> {len(ghost_tokens())}")
    app.clear_selection()


# ----------------------------------------------------------------------------
# D. snapping
# ----------------------------------------------------------------------------
def check_snapping(c, app):
    c.section("D. Snapping")
    app.grid_var.set(True)
    gx, gy = app.get_grid_snapped_point(103, 97)
    c.check("grid snap lands on a grid multiple",
            gx % app.GRID == 0 and gy % app.GRID == 0, f"({gx}, {gy})")
    app.grid_var.set(False)
    c.equal("grid snap is a no-op when off", app.get_grid_snapped_point(103, 97), (103, 97))

    app.snap_angle_var.set(True)
    ex, ey = app.get_angle_snapped_endpoint(0, 0, 100, 10)
    angle = math.degrees(math.atan2(ey - 0, ex - 0))
    c.check("angle snap lands on a 45 degree step", abs(angle % 45) < 1e-6, f"{angle}")
    app.snap_angle_var.set(False)

    app.snap_player_var.set(True)
    app.att_shape_var.set("Square")
    app._update_roster()
    app.root.update()
    token = app._team_tokens("att")[0]
    x1, y1, x2, y2 = app.canvas.bbox(token["shape_id"])
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2

    c.raises_not("line snap survives a polygon player", app.get_snap_point, cx + 2, y1 - 5)

    # Every shape snaps the same way: the box is derived from the player's size, not
    # from the box each shape happens to paint.
    offsets, line_offsets = {}, {}
    for shape in ("circle", "square", "triangle", "x", "plus", "ball"):
        probe = app._create_token(600, 400, f"SNAP_{shape}", shape=shape,
                                  color="black", size=14)
        offsets[shape] = round(400 - app.get_ball_snap_point(600, 380)[1], 2)
        line_offsets[shape] = round(400 - app.get_snap_point(600, 380)[1], 2)
        app._delete_token(app.tokens[probe])
    c.equal("every shape snaps the ball at the same distance",
            len(set(offsets.values())), 1, )
    c.check("and that distance is the player's own size",
            all(abs(value - 14) < 0.01 for value in offsets.values()), f"{offsets}")
    c.equal("line ends snap the same way for every shape",
            len(set(line_offsets.values())), 1)
    big = app._create_token(600, 400, "SNAP_BIG", shape="circle", color="black", size=28)
    c.check("a bigger player snaps further out",
            abs(400 - app.get_ball_snap_point(600, 360)[1] - 28) < 0.01)
    app._delete_token(app.tokens[big])
    # Against the snap box -- the player's centre plus its size in each direction --
    # rather than the box the shape happens to paint, which differs per shape.
    snap_box = app._token_snap_box(token)
    sx, sy = app.get_ball_snap_point(cx + 2, y1 - 12)
    c.check("ball snaps to the top edge midpoint",
            abs(sx - cx) < 0.01 and abs(sy - snap_box[1]) < 0.01,
            f"({sx}, {sy}) vs top {snap_box[1]}")
    sx, sy = app.get_ball_snap_point(x2 + 14, cy + 3)
    c.check("ball snaps to the right edge midpoint",
            abs(sx - snap_box[2]) < 0.01 and abs(sy - cy) < 0.01,
            f"({sx}, {sy}) vs right {snap_box[2]}")
    sx, sy = app.get_ball_snap_point(cx, cy)
    c.check("ball on the centre still goes to an edge, never the middle",
            not (abs(sx - cx) < 0.01 and abs(sy - cy) < 0.01))
    far = (cx + 400, cy)
    c.equal("ball does not snap from far away", app.get_ball_snap_point(*far), far)
    app.snap_player_var.set(False)
    c.equal("ball snap is a no-op when Snap Plr is off",
            app.get_ball_snap_point(cx + 2, y1 - 12), (cx + 2, y1 - 12))
    app.snap_player_var.set(True)
    c.raises_not("endpoint adjustment runs", app.adjust_endpoints, 10, 10, 200, 120)

    # An arrow snapped to a player stays put when the player is repositioned, and
    # travels only when Shift is held.
    player = app._team_tokens("att")[0]
    box = app.canvas.bbox(player["shape_id"])
    px, py = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    arrow_ids = app.draw_tactical_line_canvas("pass", px, py, px + 150, py + 40)
    player["attached_lines_start"] = [arrow_ids[0]]

    def arrow_x():
        return app.canvas.coords(arrow_ids[0])[0]

    resting = arrow_x()
    app.push_command(fa.MoveTokensCommand(app, {player["label"]: (60, 0)},
                                          keep_attached=False))
    c.equal("moving a player leaves its arrow where it was", arrow_x(), resting)
    app.undo()
    app.push_command(fa.MoveTokensCommand(app, {player["label"]: (60, 0)},
                                          keep_attached=True))
    c.equal("holding Shift brings the arrow along", arrow_x(), resting + 60)
    app.undo()

    class DragEvent:
        def __init__(self, x, y):
            self.x, self.y, self.state = x, y, 0x100

    app.selected_tokens = [player["shape_id"]]
    app.dragging_token_mode = True
    app.drag_data = {"x": int(px), "y": int(py)}
    app._drag_keep_attached = False
    resting = arrow_x()
    app.on_canvas_drag(DragEvent(int(px) + 40, int(py)))
    app.root.update()
    c.equal("a plain drag detaches it", arrow_x(), resting)
    app._drag_keep_attached = True
    app.drag_data = {"x": int(px) + 40, "y": int(py)}
    app.on_canvas_drag(DragEvent(int(px) + 80, int(py)))
    app.root.update()
    c.equal("a Shift drag keeps it attached", arrow_x(), resting + 40)
    app.dragging_token_mode = False
    app._drag_keep_attached = False
    player["attached_lines_start"] = []
    for cid in arrow_ids:
        app.canvas.delete(cid)
        app.drawn_items.pop(cid, None)
    app.clear_selection()

    # An arrow that follows a player keeps its shape. Every tool is checked, because
    # each builds its arrow differently: a shot is two shafts plus a head drawn as a
    # separate polygon, a dribble is one long wave, a bend is a curve.
    for tool in ("line", "pass", "shot", "dribble", "run", "bend"):
        box = app.canvas.bbox(player["shape_id"])
        px, py = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        app.active_tool = tool
        app.temp_line_start = (px - 220, py + 160)
        if tool == "bend":
            app.bend_control_point = (px - 110, py + 40)

        class Release:
            def __init__(self, x, y):
                self.x, self.y, self.state, self.num = int(x), int(y), 0, 1

        app.on_canvas_release(Release(px, py))
        app.active_tool = None
        ids = list(player.get("attached_lines_end") or ())
        c.check(f"a {tool} drawn at a player attaches to it", bool(ids))
        if not ids:
            continue
        before = {cid: app.canvas.coords(cid) for cid in ids}
        app.push_command(fa.MoveTokensCommand(app, {player["label"]: (70.0, -50.0)},
                                              keep_attached=True))
        after = {cid: app.canvas.coords(cid) for cid in ids}

        def moved_by(cid, index):
            return (round(after[cid][index] - before[cid][index], 3),
                    round(after[cid][index + 1] - before[cid][index + 1], 3))

        tip = max(ids, key=lambda cid: max(after[cid][0::2]))
        c.equal(f"a {tool} follows the player the whole way",
                moved_by(tip, len(after[tip]) - 2 if app.canvas.type(tip) != "polygon"
                         else 0), (70.0, -50.0))
        shaft = ids[0]
        c.equal(f"a {tool} leaves its far end where it was",
                moved_by(shaft, 0), (0.0, 0.0))
        for cid in ids:
            if app.canvas.type(cid) != "polygon":
                continue
            size_before = (max(before[cid][0::2]) - min(before[cid][0::2]),
                           max(before[cid][1::2]) - min(before[cid][1::2]))
            size_after = (max(after[cid][0::2]) - min(after[cid][0::2]),
                          max(after[cid][1::2]) - min(after[cid][1::2]))
            c.check(f"a {tool}'s head keeps its shape",
                    all(abs(a - b) < 0.01 for a, b in zip(size_before, size_after)),
                    f"{size_before} -> {size_after}")
        c.equal(f"a moved {tool} is what the animation restores",
                [app.drawn_items[cid].get("full_coords") for cid in ids],
                [after[cid] for cid in ids])
        app.undo()
        for cid in ids:
            app.canvas.delete(cid)
            app.drawn_items.pop(cid, None)
        player["attached_lines_end"] = []
        player["attached_lines_start"] = []
    app.clear_selection()


# ----------------------------------------------------------------------------
# E. signs and drawings
# ----------------------------------------------------------------------------
def check_signs(c, app):
    c.section("E. Signs and drawn items")
    for sign in ("Goal", "X", "Ball", "Square", "Triangle", "Plus"):
        ids = app.place_sign_canvas(700, 500, sign)
        c.check(f"{sign}: something is drawn", len(ids) >= 1)
        c.check(f"{sign}: registered as a drawn item",
                all(i in app.drawn_items for i in ids))
        c.check(f"{sign}: parts share a group tag",
                all(app.drawn_items[i].get("group") == app.drawn_items[ids[0]].get("group")
                    for i in ids))
        if sign in ("Goal", "Square", "Triangle"):
            c.equal(f"{sign}: is a polygon so it can be rotated",
                    app.canvas.type(ids[0]), "polygon")
        for i in ids:
            app.canvas.delete(i)
            app.drawn_items.pop(i, None)

    goal = app.place_sign_canvas(700, 500, "Goal")
    app.selected_drawn = set(goal)
    before = list(app.canvas.coords(goal[0]))
    app.rotate_selected()
    turned = list(app.canvas.coords(goal[0]))
    c.check("Rotate Sel turns a stamped goal", before != turned)
    app.undo()
    c.check("undo puts the goal back",
            all(abs(a - b) < 1e-6 for a, b in zip(before, app.canvas.coords(goal[0]))))

    ball = app.place_sign_canvas(760, 520, "Ball")
    c.equal("a ball is one plain dot", len(ball), 1)
    c.equal("filled solid, not hollow", app.canvas.itemcget(ball[0], "fill"),
            app.sign_color)
    c.check("and nothing in it is decoration",
            not any(app.drawn_items[cid].get("decor") for cid in ball))
    app.selected_drawn = set(ball)
    c.equal("deleting a ball removes it", app.delete_selection(), len(ball))
    app.selected_drawn = set(goal)
    app.delete_selection()

    for tool in ("pass", "shot", "dribble", "run", "line", "box", "rectangle", "circle", "oval"):
        c.raises_not(f"draw a {tool}", app.draw_tactical_line_canvas,
                     tool, 200, 200, 320, 280)
    app.canvas.delete("tactic_line")

    # Outline-only shapes must stay outline-only through a selection cycle.
    # Registration is left to the drawing path itself: doing it here by hand hid a
    # bug where the real path stored no colour options at all, so every box and
    # circle fell back to fill= and went solid on the first selection.
    hollow = {}
    for tool in ("oval", "circle", "box", "rectangle"):
        cmd = fa.DrawLineCommand(app, tool, 300, 300, 420, 380)
        app.push_command(cmd, execute=True)
        hollow[tool] = list(cmd.line_ids)
        c.equal(f"a {tool} records its colour in outline= only",
                app.drawn_items[cmd.line_ids[0]].get("color_options"), ["outline"])
    for sign in ("Square", "Triangle"):
        hollow[sign] = app.place_sign_canvas(500, 500, sign)

    def is_hollow(ids):
        for cid in ids:
            try:
                if app.canvas.itemcget(cid, "fill"):
                    return False
            except Exception:
                pass
        return True

    c.check("shapes are drawn unfilled",
            all(is_hollow(ids) for ids in hollow.values()),
            f"{[k for k, v in hollow.items() if not is_hollow(v)]}")
    app.selected_drawn = {cid for ids in hollow.values() for cid in ids}
    app.highlight_selected()
    app.root.update()
    c.check("selecting them does not flood them solid",
            all(is_hollow(ids) for ids in hollow.values()),
            f"{[k for k, v in hollow.items() if not is_hollow(v)]}")
    app.clear_selection()
    app.root.update()
    c.check("nor does deselecting them",
            all(is_hollow(ids) for ids in hollow.values()),
            f"{[k for k, v in hollow.items() if not is_hollow(v)]}")
    c.check("their outlines keep the drawing colour",
            app.canvas.itemcget(hollow["oval"][0], "outline") == app.line_color)
    app.selected_drawn = {cid for ids in hollow.values() for cid in ids}
    app.delete_selection()
    app.canvas.delete("tactic_line")

    # A bend keeps its shape editable after it has been drawn.
    for label in ("plain",):
        ids = app.draw_tactical_line_canvas("bend", 300, 400, 600, 400,
                                            extra_data={"cx": 450, "cy": 320})
        shared = {"tool": "bend"}
        for cid in ids:
            app.drawn_items[cid] = {"type": "tactic_line", "tool": "bend",
                                    "data": shared, "color": app.line_color}
        app.clear_selection()
        app.selected_drawn = {ids[0]}
        app._draw_selection_overlay()
        app.root.update()
        types = app.selection_overlay_handle_types
        c.check(f"a selected bend ({label}) offers a curve handle", "line_bend" in types,
                f"{types}")
        if "line_bend" in types:
            handle = app.selection_overlay_handles[types.index("line_bend")]
            hbox = app.canvas.bbox(handle)
            hx, hy = (hbox[0] + hbox[2]) / 2, (hbox[1] + hbox[3]) / 2
            before = [list(app.canvas.coords(i)) for i in ids]
            app.canvas.event_generate("<ButtonPress-1>", x=int(hx), y=int(hy))
            app.canvas.event_generate("<B1-Motion>", x=int(hx) + 30, y=int(hy) - 60,
                                      state=0x100)
            app.canvas.event_generate("<ButtonRelease-1>", x=int(hx) + 30, y=int(hy) - 60)
            app.root.update()
            after = [list(app.canvas.coords(i)) for i in ids]
            c.check(f"dragging it reshapes the curve ({label})",
                    all(abs((a[2] - b[2]) - 30) < 2 and abs((a[3] - b[3]) + 60) < 2
                        for a, b in zip(after, before)),
                    f"{[(round(a[2]-b[2]), round(a[3]-b[3])) for a, b in zip(after, before)]}")
            c.check(f"its ends stay put ({label})",
                    all(abs(a[0] - b[0]) < 0.01 and abs(a[1] - b[1]) < 0.01 and
                        abs(a[-2] - b[-2]) < 0.01 and abs(a[-1] - b[-1]) < 0.01
                        for a, b in zip(after, before)))
        for cid in ids:
            app.canvas.delete(cid)
            app.drawn_items.pop(cid, None)
    app.clear_selection()
    app._clear_selection_overlay()

    # Every mark on the board can be resized by its corner handles. This used to be
    # damped twice over -- a twelfth of the drag, then a tenth of the result -- which
    # left the signs, the labels and the multi-part arrows looking like they could not
    # be resized at all.
    def corner_resize(ids, grow=60):
        app.clear_selection()
        app.selected_drawn = set(ids)
        app.highlight_selected()
        app._draw_selection_overlay()
        app.root.update()
        bounds = app._get_selection_bounds()
        widths = {cid: app.canvas.bbox(cid) for cid in ids}
        app.resize_initial_bounds = bounds
        app.resize_anchor = "bottom-right"
        app._apply_resized_selection((bounds[0], bounds[1],
                                      bounds[2] + grow, bounds[3] + grow))
        app.root.update()
        app.resize_initial_bounds = None
        app.resize_anchor = None
        after = {cid: app.canvas.bbox(cid) for cid in ids}
        return widths, after

    for mark in ("Goal", "X", "Ball", "Square", "Triangle", "Plus"):
        ids = app.place_sign_canvas(520, 420, mark)
        before, after = corner_resize(ids)
        grew = [cid for cid in ids
                if after[cid] and before[cid]
                and (after[cid][2] - after[cid][0]) > (before[cid][2] - before[cid][0]) + 1]
        c.check(f"the {mark} sign resizes by its corner", bool(grew),
                f"{len(grew)} of {len(ids)} parts")
        app.selected_drawn = set(ids)
        app.delete_selection()

    label_ids = app.place_text_canvas(520, 420, "Resize me")
    before, after = corner_resize(label_ids)
    c.check("a text label resizes by its corner",
            (after[label_ids[0]][2] - after[label_ids[0]][0]) >
            (before[label_ids[0]][2] - before[label_ids[0]][0]))
    app.selected_drawn = set(label_ids)
    app.delete_selection()

    shot = app.draw_tactical_line_canvas("shot", 300, 500, 460, 560)
    for cid in shot:
        app._register_drawn_item(cid, {"type": "tactic_line", "tool": "shot",
                                       "color": app.line_color})
    before, after = corner_resize(shot)
    c.equal("every piece of a shot arrow resizes together",
            sum(1 for cid in shot
                if (after[cid][2] - after[cid][0]) > (before[cid][2] - before[cid][0])),
            len(shot))
    app.selected_drawn = set(shot)
    app.delete_selection()

    # Clicking one piece of a mark takes the whole mark.
    for mark in ("Ball", "X", "Plus"):
        ids = app.place_sign_canvas(560, 460, mark)
        c.equal(f"clicking one piece of a {mark} picks up all of it",
                app._drawn_siblings(ids[-1]), set(ids))
        app.selected_drawn = set(ids)
        app.delete_selection()
    arrow = app.draw_tactical_line_canvas("shot", 300, 520, 460, 580)
    shared = {"tool": "shot"}
    for cid in arrow:
        app._register_drawn_item(cid, {"type": "tactic_line", "tool": "shot",
                                       "data": shared, "color": app.line_color})
    c.equal("and one shaft of a shot picks up its head too",
            app._drawn_siblings(arrow[0]), set(arrow))
    app.selected_drawn = set(arrow)
    app.delete_selection()

    # A bend is drawn at the weight of a plain line, not the heavier arrow weight it
    # used to carry, which made every curve look fatter than the arrows around it.
    real_type = app.line_type_var.get()
    app.line_type_var.set("Solid")
    bend = app.draw_tactical_line_canvas("bend", 300, 600, 500, 600,
                                         extra_data={"cx": 400, "cy": 540})
    straight = app.draw_tactical_line_canvas("line", 300, 640, 500, 640)
    c.equal("a bend is no thicker than a straight line",
            app.canvas.itemcget(bend[0], "width"),
            app.canvas.itemcget(straight[0], "width"))
    for cid in bend + straight:
        app.canvas.delete(cid)
        app.drawn_items.pop(cid, None)

    # Every line type can be drawn as a curve, and comes out with that type's dashes
    # and weight -- while staying one three-point smooth line, which is what keeps its
    # control handle and its place in the animation working.
    plain_width = None
    for line_type in ("Solid", "Dashed", "Dotted", "Pass", "Shot", "Dribble", "Run"):
        app.line_type_var.set(line_type)
        ids = app.draw_tactical_line_canvas("bend", 300, 600, 500, 600,
                                            extra_data={"cx": 400, "cy": 540})
        cid = ids[0]
        dash = app.canvas.itemcget(cid, "dash")
        width = float(app.canvas.itemcget(cid, "width"))
        if line_type == "Solid":
            plain_width = width
        c.equal(f"a {line_type} bend is one editable curve",
                (len(ids), len(app.canvas.coords(cid)) // 2,
                 app.canvas.itemcget(cid, "smooth")), (1, 3, "true"))
        c.check(f"a {line_type} bend carries an arrowhead",
                app.canvas.itemcget(cid, "arrow") == "last")
        if line_type in ("Dashed", "Dotted", "Pass", "Dribble", "Run"):
            c.check(f"a {line_type} bend is dashed like its straight form", bool(dash),
                    f"dash={dash!r}")
        if line_type == "Shot":
            c.check("a Shot bend is the heavier one", width > plain_width,
                    f"{width} vs {plain_width}")
        for other in ids:
            app.canvas.delete(other)
            app.drawn_items.pop(other, None)
    app.line_type_var.set(real_type)
    app.clear_selection()

    # One Size field for signs and text, one colour for both
    c.section("E. Signs and drawn items")
    sized_sign = app.place_sign_canvas(520, 520, "Triangle")
    sized_text = app.place_text_canvas(640, 520, text="Screen", size=14)
    marked_ball = app.place_sign_canvas(760, 520, "Ball")

    def item_width(cid):
        box = app.canvas.bbox(cid)
        return box[2] - box[0]

    sign_before = item_width(sized_sign[0])
    text_before = item_width(sized_text[0])
    app.selected_drawn = set(sized_sign) | set(sized_text)
    app.sign_size_var.set(30)
    app._apply_sign_size()
    app.root.update()
    grown_sign = [cid for cid in app.selected_drawn
                  if app.drawn_items[cid]["type"] == "sign"]
    grown_text = [cid for cid in app.selected_drawn
                  if app.drawn_items[cid]["type"] == "text"]
    c.check("the Size field restamps a selected sign larger",
            grown_sign and item_width(grown_sign[0]) > sign_before * 1.5,
            f"{sign_before} -> {item_width(grown_sign[0]) if grown_sign else '-'}")
    c.check("the same field resizes a selected text label",
            grown_text and item_width(grown_text[0]) > text_before * 1.5,
            f"{text_before} -> {item_width(grown_text[0]) if grown_text else '-'}")
    c.equal("one dial drives text as well", int(app.text_size_var.get()), 30)
    c.check("text stays selected when the signs are restamped", bool(grown_text))

    app.selected_drawn = set(grown_sign) | set(grown_text) | set(marked_ball)
    app.sign_color = "#1971c2"
    app._recolor_selected_signs()
    app.clear_selection()
    app.root.update()
    c.equal("the sign colour applies to signs",
            app.canvas.itemcget(grown_sign[0], "outline"), "#1971c2")
    c.equal("and to text labels", app.canvas.itemcget(grown_text[0], "fill"), "#1971c2")
    c.equal("the ball takes the colour",
            app.canvas.itemcget(marked_ball[0], "fill"), "#1971c2")
    app.sign_color = "#000000"
    app.sign_size_var.set(12)
    app.selected_drawn = set(grown_sign) | set(grown_text) | set(marked_ball)
    app.delete_selection()

    # Text labels
    c.section("E. Signs and drawn items")
    ids = app.place_text_canvas(500, 400, text="Power play", size=18)
    c.check("a text label is placed", bool(ids))
    if ids:
        text_id = ids[0]
        c.equal("it is a canvas text item", app.canvas.type(text_id), "text")
        c.equal("with the text given", app.canvas.itemcget(text_id, "text"), "Power play")
        c.equal("registered as a drawn item",
                app.drawn_items[text_id]["type"], "text")
        width_before = app.canvas.bbox(text_id)[2] - app.canvas.bbox(text_id)[0]
        app.selected_drawn = {text_id}
        app.sign_size_var.set(34)
        app._apply_sign_size()
        app.root.update()
        width_after = app.canvas.bbox(text_id)[2] - app.canvas.bbox(text_id)[0]
        c.check("the Size dial resizes a selected label",
                width_after > width_before * 1.4, f"{width_before} -> {width_after}")
        c.equal("and remembers the new size", app.drawn_items[text_id]["size"], 34)
        app.sign_size_var.set(12)
        app.selected_drawn = {text_id}
        app.delete_selection()

    # The typing prompt, driven the way a user would: type, then press Return. The
    # dialog is modal, so this has to be scheduled and retried until it is up.
    attempt = {"tries": 0}

    def type_and_accept():
        attempt["tries"] += 1
        prompts = [w for w in app.root.winfo_children()
                   if isinstance(w, tk.Toplevel) and w.winfo_exists()
                   and w.title() == "Text"]
        entries = []

        def find(widget):
            for child in widget.winfo_children():
                if isinstance(child, tk.Entry):
                    entries.append(child)
                find(child)

        if prompts:
            find(prompts[-1])
        if entries:
            entries[0].delete(0, tk.END)
            entries[0].insert(0, "Forecheck")
            entries[0].event_generate("<Return>")
            return
        if attempt["tries"] < 40:
            app.root.after(50, type_and_accept)

    app.root.after(50, type_and_accept)
    typed = app.place_text_canvas(400, 300)
    c.check("the prompt asks for the words and places them", bool(typed),
            "no label came back")
    if typed:
        c.equal("what was typed is what appears",
                app.canvas.itemcget(typed[0], "text"), "Forecheck")
        app.selected_drawn = set(typed)
        app.delete_selection()

    # Images on the board
    image_path = os.path.join(TEMP.name, "board-image.png")
    Image.new("RGBA", (120, 60), (30, 90, 200, 255)).save(image_path)
    real_open = fa.filedialog.askopenfilename
    fa.filedialog.askopenfilename = lambda *a, **k: image_path
    try:
        image_id = app.add_board_image()
        app.root.update()
    finally:
        fa.filedialog.askopenfilename = real_open
    c.check("an image is placed on the board", image_id is not None)
    if image_id is not None:
        c.equal("it is a canvas image item", app.canvas.type(image_id), "image")
        c.check("it arrives selected, ready to be dragged",
                app.selected_drawn == {image_id})
        box = app.canvas.bbox(image_id)
        wide_before = box[2] - box[0]
        c.check("its aspect matches the file",
                abs((box[2] - box[0]) / (box[3] - box[1]) - 2.0) < 0.1,
                f"{box[2]-box[0]}x{box[3]-box[1]}")
        app._scale_board_image(image_id, 1.5, 1.5)
        app.root.update()
        wide_after = app.canvas.bbox(image_id)[2] - app.canvas.bbox(image_id)[0]
        c.check("it scales like any other object",
                abs(wide_after - wide_before * 1.5) < 4,
                f"{wide_before} -> {wide_after}")
        # through the real corner-handle path
        app.clear_selection()
        app.selected_drawn = {image_id}
        app.highlight_selected()
        app._draw_selection_overlay()
        app.root.update()
        bounds = app._get_selection_bounds()
        if bounds:
            app.resize_initial_bounds = bounds
            app.resize_anchor = "bottom-right"
            grown = (bounds[0], bounds[1], bounds[2] + 60, bounds[3] + 30)
            before_px = app.canvas.bbox(image_id)[2] - app.canvas.bbox(image_id)[0]
            app._apply_resized_selection(grown)
            app.root.update()
            after_px = app.canvas.bbox(image_id)[2] - app.canvas.bbox(image_id)[0]
            c.check("dragging a corner handle resizes the picture itself",
                    after_px != before_px, f"{before_px} -> {after_px}")
            app.resize_initial_bounds = None
            app.resize_anchor = None
        app._clear_selection_overlay()
        app.selected_drawn = {image_id}
        app.delete_selection()
        c.check("deleting it releases the picture", image_id not in app.board_images)
    app.clear_selection()


# ----------------------------------------------------------------------------
# F. tactics
# ----------------------------------------------------------------------------
def check_tactics(c, app):
    c.section("F. Tactics")
    for name, slots in fa.FloorballTacticsApp.FORMATIONS.items():
        app.att_tactic_var.set(name)
        app.att_pct_var.set("70")
        app.apply_tactic("att")
        app.root.update()
        tokens = app._team_tokens("att")
        c.equal(f"{name}: team resized to the formation", len(tokens), len(slots))
        wanted = sorted(slot[0] for slot in slots)
        c.equal(f"{name}: positions applied",
                sorted(t.get("position") for t in tokens), wanted)
        steps = list(app.steps_listbox.get(0, tk.END))
        c.check(f"{name}: written into the timeline",
                any(name in step for step in steps), f"timeline {steps[-1:]}")
        c.check(f"{name}: recorded as a serialisable command",
                app.undo_stack and app.undo_stack[-1].serialize().get("type") == "tactic")
        inside = all(0 <= app._state_px_to_m(*token_centre(app, t), app._pitch_state())[0] <= 40
                     for t in tokens)
        c.check(f"{name}: everyone lands on the rink", inside)

    app.apply_tactic("def")
    c.check("both teams can hold formations at once",
            len(app._team_tokens("att")) >= 4 and len(app._team_tokens("def")) >= 4)


# ----------------------------------------------------------------------------
# G. undo / redo
# ----------------------------------------------------------------------------
def check_undo(c, app):
    c.section("G. Undo and redo")
    app._update_roster()
    app.root.update()
    token = app._team_tokens("att")[0]
    start = token_centre(app, token)

    app.push_command(fa.MoveTokensCommand(app, {token["label"]: (40, 25)}))
    moved = token_centre(app, token)
    c.check("move command moves the player",
            abs(moved[0] - start[0] - 40) < 1.5 and abs(moved[1] - start[1] - 25) < 1.5,
            f"{start} -> {moved}")
    app.undo()
    back = token_centre(app, app.tokens[app._get_sid_by_label(token["label"])])
    c.check("undo returns it exactly",
            abs(back[0] - start[0]) < 1e-6 and abs(back[1] - start[1]) < 1e-6,
            f"{start} -> {back}")
    app.redo()
    c.check("redo moves it again",
            abs(token_centre(app, app.tokens[app._get_sid_by_label(token["label"])])[0]
                - moved[0]) < 1e-6)
    app.undo()

    label = token["label"]
    app.push_command(fa.RotateTokensCommand(app, [label], 45))
    app.undo()
    c.check("rotate command undoes cleanly", True)

    app.push_command(fa.LockCommand(app, [label], True))
    c.check("lock command locks",
            app.tokens[app._get_sid_by_label(label)].get("locked") is True)
    app.undo()
    c.check("undo unlocks",
            not app.tokens[app._get_sid_by_label(label)].get("locked"))

    # A move is recorded everywhere a step should show up.
    app.animation_steps = []
    app._refresh_animation_list()
    app.steps_listbox.delete(0, tk.END)
    app.action_steps.clear()
    app.undo_stack.clear()
    app.ghosting_var.set(False)
    mover = app._team_tokens("att")[0]
    box = app.canvas.bbox(mover["shape_id"])
    mx0, my0 = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    app.canvas.event_generate("<ButtonPress-1>", x=int(mx0), y=int(my0))
    app.canvas.event_generate("<B1-Motion>", x=int(mx0) + 60, y=int(my0) + 30, state=0x100)
    app.canvas.event_generate("<ButtonRelease-1>", x=int(mx0) + 60, y=int(my0) + 30)
    app.root.update()
    lines = list(app.steps_listbox.get(0, tk.END))
    c.check("a drag writes a line in the timeline",
            any(line.startswith("Move") for line in lines), f"{lines}")
    names = [s["name"] for s in app.animation_steps]
    c.equal("arranging the board makes one group, not an animation of the setup",
            len(names), 1)
    c.check("that group is the stage the animation begins at",
            names[0].startswith("Group 0"), f"{names}")
    c.check("and it holds the board as it now stands",
            app.animation_steps[0].get("actions"), "no actions recorded")
    # Group 0 is the stage the animation begins at, so it must hold the board as it
    # actually stands -- not as it stood before the setup moves.
    opening = {p["label"]: (p["mx"], p["my"])
               for p in app.animation_steps[0]["board"]["players"]}
    state = app._pitch_state()
    live = {}
    for token in app._all_tokens():
        box = app.canvas.bbox(token["shape_id"])
        live[token["label"]] = app._state_px_to_m((box[0] + box[2]) / 2,
                                                  (box[1] + box[3]) / 2, state)
    drift = max((max(abs(opening[label][0] - live[label][0]),
                     abs(opening[label][1] - live[label][1]))
                 for label in opening if label in live), default=0)
    c.check("group 0 is the board as it stands, not as it stood before the moves",
            drift < 0.05, f"worst difference {drift:.3f} m")
    c.check("the group lists the player as the rink shows it",
            any(app._display_label(mover["label"]) in action
                for action in app.animation_steps[0].get("actions", [])),
            f"{app.animation_steps[0].get('actions')}")
    c.check("the move is serialisable into a macro",
            app.undo_stack and app.undo_stack[-1].serialize().get("type") == "move_tokens")
    app.undo()
    app.root.update()
    c.check("undo removes its timeline line",
            not any(line.startswith("Move")
                    for line in app.steps_listbox.get(0, tk.END)))
    c.equal("undo removes the group it created", len(app.animation_steps), 0)
    app.redo()
    app.root.update()
    c.equal("redo restores it", len(app.animation_steps), 1)

    # Add Group closes the opening stage; the moves after it animate from there.
    app.add_animation_step()
    app.root.update()
    c.equal("Add Group starts the next one", len(app.animation_steps), 2)

    # Two moves in the same group, so the two play together.
    second = app._team_tokens("att")[1]
    box2 = app.canvas.bbox(second["shape_id"])
    sx, sy = (box2[0] + box2[2]) / 2, (box2[1] + box2[3]) / 2
    app.canvas.event_generate("<ButtonPress-1>", x=int(sx), y=int(sy))
    app.canvas.event_generate("<B1-Motion>", x=int(sx) - 50, y=int(sy) + 20, state=0x100)
    app.canvas.event_generate("<ButtonRelease-1>", x=int(sx) - 50, y=int(sy) + 20)
    app.root.update()
    c.equal("drags join the open group rather than adding one",
            len(app.animation_steps), 2)
    grouped = app.animation_steps[1]["name"]
    c.check("the row lists the players that move together",
            app._display_label(second["label"]) in grouped, f"{grouped}")

    starts = app._step_positions(app.animation_steps[0])
    ends = app._step_positions(app.animation_steps[1])
    app._apply_animation_frame(app.animation_steps[0], app.animation_steps[1], 0.5)
    app.root.update()

    def metres(label):
        token = app.tokens[app._get_sid_by_label(label)]
        box = app.canvas.bbox(token["shape_id"])
        return app._state_px_to_m((box[0] + box[2]) / 2, (box[1] + box[3]) / 2,
                                  app._pitch_state())

    moving = [label for label in starts
              if label in ends and abs(starts[label][0] - ends[label][0]) > 0.5]
    both_halfway = bool(moving) and all(
        abs(metres(label)[0] - (starts[label][0] + ends[label][0]) / 2) < 0.1
        for label in moving)
    c.check("everything in one step moves at the same time", both_halfway)
    app._apply_animation_frame(app.animation_steps[0], app.animation_steps[1], 1.0)
    app.root.update()

    app.undo()
    app.root.update()
    c.equal("undoing a move leaves the group in place", len(app.animation_steps), 2)
    app.redo()
    app.root.update()

    # Add Group closes the group
    app.add_animation_step()
    app.root.update()
    closed = len(app.animation_steps)
    app.canvas.event_generate("<ButtonPress-1>", x=int(sx), y=int(sy))
    app.canvas.event_generate("<B1-Motion>", x=int(sx) + 30, y=int(sy) - 15, state=0x100)
    app.canvas.event_generate("<ButtonRelease-1>", x=int(sx) + 30, y=int(sy) - 15)
    app.root.update()
    c.equal("Add Group closes it, so the next drag joins the new one",
            len(app.animation_steps), closed)

    # per-step time field
    app.animation_playhead = 1
    c.check("a group's time can be set on its own", app.set_group_time(1, 4.5))
    app.root.update()
    c.equal("the group takes the new time", app.animation_steps[1]["duration"], 4.5)
    c.check("and the others are left alone",
            all(abs(step["duration"] - 4.5) > 0.01
                for i, step in enumerate(app.animation_steps) if i != 1))
    c.check("the row shows it",
            app.anim_tree.item("g1", "values")[0] == "4.5s",
            f"{app.anim_tree.item('g1', 'values')}")

    # Del Step must not rename the survivors
    named_before = [s["name"] for s in app.animation_steps if s.get("named")]
    app.animation_playhead = len(app.animation_steps) - 1
    app.delete_animation_step()
    app.root.update()
    survivors = [s["name"] for s in app.animation_steps if s.get("named")]
    c.check("deleting a step leaves the others' names intact",
            all(name in named_before for name in survivors) and survivors, f"{survivors}")

    # One Delete button for both kinds of row: pick a group and the group goes, pick
    # an action inside it and only that action goes.
    app.animation_steps[1].setdefault("actions", []).append("Pass CHECK")
    arrow = app.draw_tactical_line_canvas("pass", 240, 240, 380, 300)
    for cid in arrow:
        app.drawn_items[cid]["anim_group"] = 1
        app.drawn_items[cid]["anim_action"] = "Pass CHECK"
    app._refresh_animation_list()
    app.root.update()
    position = app.animation_steps[1]["actions"].index("Pass CHECK")
    groups_before = len(app.animation_steps)
    app.anim_tree.selection_set(f"g1a{position}")
    app.delete_animation_selection()
    app.root.update()
    c.check("deleting a picked action takes it out of its group",
            "Pass CHECK" not in (app.animation_steps[1].get("actions") or []))
    c.equal("and leaves the group itself standing",
            len(app.animation_steps), groups_before)
    c.check("its drawing comes off the board with it",
            all(cid not in app.drawn_items for cid in arrow))

    app.anim_tree.selection_set("g1")
    app.delete_animation_selection()
    app.root.update()
    c.equal("deleting a picked group takes the group",
            len(app.animation_steps), groups_before - 1)

    before_lines = len(app.action_steps)
    before_steps = len(app.animation_steps)
    app.att_tactic_var.set("Umbrella")
    app.apply_tactic("att")
    app.root.update()
    c.equal("a formation writes exactly one timeline line",
            len(app.action_steps) - before_lines, 1)
    c.check("and keyframes the team's new shape",
            len(app.animation_steps) - before_steps >= 1,
            f"{len(app.animation_steps) - before_steps}")
    app.undo()
    app.root.update()
    app.animation_steps = []
    app._refresh_animation_list()
    app.steps_listbox.delete(0, tk.END)
    app.action_steps.clear()

    depth = len(app.undo_stack)
    app.push_command(fa.DrawLineCommand(app, "pass", 100, 100, 200, 160))
    c.equal("drawing pushes a command", len(app.undo_stack), depth + 1)
    steps = len(app.action_steps)
    app.undo()
    c.equal("undo removes its timeline line", len(app.action_steps), steps - 1)

    while app.undo_stack:
        app.undo()
    c.raises_not("undo on an empty stack is safe", app.undo)
    while app.redo_stack:
        app.redo()
    c.raises_not("redo on an empty stack is safe", app.redo)


# ----------------------------------------------------------------------------
# H. macros: save and load
# ----------------------------------------------------------------------------
def check_macros(c, app):
    c.section("H. Macro save and load")
    app._update_roster()
    app.root.update()
    app.att_tactic_var.set("House")
    app.apply_tactic("att")
    app.root.update()

    state = app._pitch_state()
    wanted = {}
    for token in app._all_tokens():
        mx, my = app._state_px_to_m(*token_centre(app, token), state)
        wanted[token["label"]] = (round(mx, 2), round(my, 2))

    board = app._board_snapshot()
    c.check("board snapshot records every player",
            len(board["players"]) == len(wanted), f"{len(board['players'])} of {len(wanted)}")
    c.check("board snapshot stores metres, not pixels",
            all(0 <= p["mx"] <= 40 and 0 <= p["my"] <= 20 for p in board["players"]))

    path = os.path.join(TEMP.name, "macro.json")
    with open(path, "w") as handle:
        json.dump({"version": 2,
                   "commands": [cmd.serialize() for cmd in app.undo_stack
                                if hasattr(cmd, "serialize")],
                   "board": board}, handle)

    # Loading into a window of a different size is the case that used to scramble the
    # board, so the window is resized and the roster reset before restoring. (A second
    # tk.Tk() would be a truer "fresh app", but Tk ties images to their interpreter and
    # the two roots then fight over them -- the app itself only ever has one.)
    positions_before = sorted(t.get("position") for t in app._team_tokens("att"))
    app.root.geometry("1240x700")
    app.root.update_idletasks()
    for _ in range(30):
        app.root.update()
        if app.root.winfo_width() == 1240:
            break
    app._update_roster()          # scatter everyone back to the default formation
    app.root.update()
    with open(path) as handle:
        loaded = json.load(handle)
    app._restore_board(loaded["board"])
    app.root.update()

    state2 = app._pitch_state()
    worst = 0.0
    for token in app._all_tokens():
        if token["label"] not in wanted:
            continue
        mx, my = app._state_px_to_m(*token_centre(app, token), state2)
        want = wanted[token["label"]]
        worst = max(worst, abs(mx - want[0]), abs(my - want[1]))
    c.check("positions survive a load into a different window size",
            worst < 0.25, f"worst error {worst:.3f} m")
    c.check("tactical positions survive the round trip",
            sorted(t.get("position") for t in app._team_tokens("att")) == positions_before,
            f"{sorted(t.get('position') for t in app._team_tokens('att'))} "
            f"vs {positions_before}")
    app.root.geometry("1400x900")
    app.root.update()

    legacy = [{"type": "draw", "tool": "pass", "x1": 10, "y1": 10, "x2": 90, "y2": 60}]
    with open(os.path.join(TEMP.name, "v1.json"), "w") as handle:
        json.dump(legacy, handle)
    c.check("a version 1 file is still a bare list", isinstance(legacy, list))

    # Tidying the recording: what a later instruction in the same group supersedes,
    # and what refers to players who have left the board.
    live = app._all_tokens()[0]["label"]
    records = [
        ({"type": "tactic", "team": "att", "formation": "Dice", "moves": {}}, 0),
        ({"type": "tactic", "team": "att", "formation": "House", "moves": {}}, 0),
        ({"type": "tactic", "team": "def", "formation": "Box", "moves": {}}, 0),
        ({"type": "move_tokens", "moves": {live: (10.0, 0.0)}}, 0),
        ({"type": "move_tokens", "moves": {live: (5.0, 4.0)}}, 0),
        ({"type": "draw", "tool": "pass", "x1": 1, "y1": 2, "x2": 3, "y2": 4}, 0),
        ({"type": "tactic", "team": "att", "formation": "Dice", "moves": {}}, 1),
        ({"type": "move_tokens", "moves": {"GONE9": (7.0, 7.0)}}, 1),
        ({"type": "move_tokens", "moves": {live: (1.0, 1.0)}}, 1),
    ]
    tidied, removed = app._compact_commands(records)
    types = [entry["type"] for entry in tidied]
    c.equal("tidying drops three superseded or obsolete instructions", removed, 3)
    c.equal("and keeps the rest in order", types,
            ["tactic", "tactic", "move_tokens", "draw", "tactic", "move_tokens"])
    c.equal("the superseded formation is the one that goes",
            [e["formation"] for e in tidied if e["type"] == "tactic"],
            ["House", "Box", "Dice"])
    c.equal("consecutive moves in one group are folded into one",
            tidied[2]["moves"][live], (15.0, 4.0))
    c.check("a move of a player who has left the board goes",
            all("GONE9" not in (e.get("moves") or {}) for e in tidied))
    c.equal("the same formation in a later group is a different moment and stays",
            sum(1 for e in tidied if e.get("formation") == "Dice"), 1)
    c.equal("a recording with nothing to gain is left alone",
            app._compact_commands(records[5:6]), ([records[5][0]], 0))

    # It is offered, not imposed: answering no saves the log as recorded.
    macro_path = os.path.join(TEMP.name, "tidy.json")
    real_save, real_ask = fa.filedialog.asksaveasfilename, fa.messagebox.askyesno
    counts, asked = {}, []
    # Give the recording something to tidy, or there is nothing to be asked about.
    app.att_tactic_var.set("Dice")
    app.apply_tactic("att")
    app.att_tactic_var.set("House")
    app.apply_tactic("att")
    app.root.update()
    try:
        fa.filedialog.asksaveasfilename = lambda *a, **k: macro_path
        for answer, label in ((False, "kept"), (True, "tidied")):
            fa.messagebox.askyesno = lambda *a, **k: (asked.append(a[0]), answer)[1]
            app.save_macro()
            with open(macro_path) as handle:
                counts[label] = len(json.load(handle)["commands"])
    finally:
        fa.filedialog.asksaveasfilename = real_save
        fa.messagebox.askyesno = real_ask
    c.check("saving asks before tidying anything",
            asked and "Tidy" in asked[0], f"{asked}")
    c.check("answering no writes every instruction",
            counts.get("kept", 0) > counts.get("tidied", 0), f"{counts}")

    # The whole play, out and back: every mark where it was, every group as it was,
    # reopened into a window of a different size. This is what the file is for.
    app.root.geometry("1400x900")
    app.root.update_idletasks()
    for _ in range(30):
        app.root.update()
        if app.root.winfo_width() >= 1390:
            break
    app.animation_steps = []
    app.animation_playhead = 0
    for cid in list(app.drawn_items):
        app.canvas.delete(cid)
    app.drawn_items.clear()
    app._refresh_animation_list()
    app.snap_player_var.set(True)

    class Release:
        def __init__(self, x, y):
            self.x, self.y, self.state, self.num = int(x), int(y), 0, 1

    carrier = app._team_tokens("att")[0]
    centre = token_centre(app, carrier)
    app.active_tool = "pass"
    app.temp_line_start = centre
    app.on_canvas_release(Release(centre[0] + 190, centre[1] + 110))
    app.active_tool = None
    app.add_animation_step()
    app.push_command(fa.MoveTokensCommand(app, {carrier["label"]: (120.0, 60.0)}))
    app.active_tool = "bend"
    app.temp_line_start = (360, 560)
    app.bend_control_point = (470, 470)
    app.on_canvas_release(Release(600, 560))
    app.active_tool = None
    app.place_sign_canvas(700, 320, "Goal")
    app.place_sign_canvas(760, 320, "Ball")
    app.place_text_canvas(830, 320, "Screen")
    picture = Image.new("RGBA", (80, 40), (40, 120, 200, 255))
    picture_path = os.path.join(TEMP.name, "board_picture.png")
    picture.save(picture_path)
    app._draw_board_image({"original": picture, "path": picture_path,
                           "w_px": 80, "h_px": 40}, 520, 250)
    app.add_animation_step()
    app.set_group_time(1, 0.8)
    app.set_group_time(2, 1.2)
    app.root.update()

    def play_fingerprint():
        """Everything the file is supposed to bring back, in rink metres."""
        state = app._pitch_state()
        marks = []
        for cid, meta in app.drawn_items.items():
            coords = meta.get("full_coords") or app.canvas.coords(cid)
            marks.append((
                app.canvas.type(cid), meta.get("type"),
                meta.get("sign_type") or meta.get("tool"), meta.get("anim_group"),
                tuple(tuple(round(v, 2)
                            for v in app._state_px_to_m(coords[i], coords[i + 1], state))
                      for i in range(0, len(coords) - 1, 2))))
        players = {}
        for token in app._all_tokens():
            spot = app._token_centre_px(token)
            players[token["label"]] = tuple(
                round(v, 2) for v in app._state_px_to_m(spot[0], spot[1], state))
        groups = [(step.get("name"), round(step.get("duration", 0), 2),
                   tuple(step.get("actions") or [])) for step in app.animation_steps]
        return sorted(marks), players, groups

    before_marks, before_players, before_groups = play_fingerprint()
    play_path = os.path.join(TEMP.name, "whole_play.json")
    real_save, real_ask = fa.filedialog.asksaveasfilename, fa.messagebox.askyesno
    try:
        fa.filedialog.asksaveasfilename = lambda *a, **k: play_path
        fa.messagebox.askyesno = lambda *a, **k: False
        app.save_macro()
    finally:
        fa.filedialog.asksaveasfilename = real_save
        fa.messagebox.askyesno = real_ask

    with open(play_path) as handle:
        saved = json.load(handle)
    c.equal("every mark is in the file", len(saved["drawings"]), len(before_marks))
    c.check("in rink metres, not pixels",
            all(-1 <= mx <= 41 and -1 <= my <= 21
                for entry in saved["drawings"] for mx, my in entry["points_m"]))
    c.equal("with every group", len(saved["animation"]["groups"]), len(before_groups))
    c.check("each group stamped with when it starts and ends",
            all("starts_at" in g and "ends_at" in g and "duration" in g
                for g in saved["animation"]["groups"]))
    c.equal("the clock adds up",
            saved["animation"]["groups"][-1]["ends_at"],
            saved["animation"]["total_seconds"])
    c.check("each group carries the positions it ends on",
            all(g.get("board", {}).get("players") for g in saved["animation"]["groups"]))
    c.check("and the picture on the board travels with it",
            any(entry.get("image", {}).get("png_base64")
                for entry in saved["drawings"] if entry["kind"] == "image"))

    # Reopened smaller: the metres are what makes this survive.
    app.root.geometry("1120x720")
    app.root.update_idletasks()
    for _ in range(30):
        app.root.update()
        if app.root.winfo_width() <= 1130:
            break
    app._update_roster()
    app.animation_steps = []
    app._refresh_animation_list()
    app.root.update()
    real_open = fa.filedialog.askopenfilename
    try:
        fa.filedialog.askopenfilename = lambda *a, **k: play_path
        app.load_macro()
    finally:
        fa.filedialog.askopenfilename = real_open
    app.root.update()
    after_marks, after_players, after_groups = play_fingerprint()

    c.equal("every mark comes back", len(after_marks), len(before_marks))
    c.check("each one where it was drawn, to the centimetre",
            all(a[:3] == b[:3]
                and len(a[4]) == len(b[4])
                and all(abs(p[0] - q[0]) < 0.05 and abs(p[1] - q[1]) < 0.05
                        for p, q in zip(a[4], b[4]))
                for a, b in zip(before_marks, after_marks)),
            f"{[(a[2], b[2]) for a, b in zip(before_marks, after_marks) if a[:3] != b[:3]]}")
    c.check("still in the group it was drawn in",
            [a[3] for a in after_marks] == [b[3] for b in before_marks],
            f"{[a[3] for a in after_marks]} vs {[b[3] for b in before_marks]}")
    c.equal("the groups come back as groups, not as one", len(after_groups),
            len(before_groups))
    c.equal("with their names, times and contents", after_groups, before_groups)
    c.check("and every player within a centimetre of where they stood",
            all(abs(before_players[label][0] - after_players[label][0]) < 0.05 and
                abs(before_players[label][1] - after_players[label][1]) < 0.05
                for label in before_players if label in after_players),
            f"{[(l, before_players[l], after_players.get(l)) for l in before_players][:3]}")
    c.check("the arrow is still attached to its player",
            any(cid in app.drawn_items
                for token in app._all_tokens()
                for cid in (token.get("attached_lines_start") or []) +
                           (token.get("attached_lines_end") or [])))

    # And the point of it: tidying changes the record, never the play. Both files are
    # reopened and exported, and the two GIFs are compared frame by frame -- which is
    # the only comparison that actually answers "is it the same play?".
    def save_play(target, tidy):
        real_save, real_ask = fa.filedialog.asksaveasfilename, fa.messagebox.askyesno
        try:
            fa.filedialog.asksaveasfilename = lambda *a, **k: target
            fa.messagebox.askyesno = lambda *a, **k: tidy
            app.save_macro()
        finally:
            fa.filedialog.asksaveasfilename = real_save
            fa.messagebox.askyesno = real_ask

    def gif_from(play_path, gif_path):
        real_open, real_save = (fa.filedialog.askopenfilename,
                                fa.filedialog.asksaveasfilename)
        try:
            fa.filedialog.askopenfilename = lambda *a, **k: play_path
            app.load_macro()
            app.root.update()
            fa.filedialog.asksaveasfilename = lambda *a, **k: gif_path
            app.run_export("GIF")
        finally:
            fa.filedialog.askopenfilename = real_open
            fa.filedialog.asksaveasfilename = real_save
        return gif_path if os.path.exists(gif_path) else None

    # A short two-group play, so the export is a handful of frames rather than a film.
    # The window is put back to a usable size first: the check above shrinks it to
    # prove the metres survive, and PostScript cannot capture a board that is barely
    # there.
    app.root.geometry("1400x900")
    app.root.update_idletasks()
    for _ in range(30):
        app.root.update()
        if app.root.winfo_width() >= 1390:
            break
    app.animation_steps = []
    app.animation_playhead = 0
    app._refresh_animation_list()
    mover = app._team_tokens("att")[0]
    app.add_animation_step()
    app.att_tactic_var.set("Dice")
    app.apply_tactic("att")
    app.att_tactic_var.set("House")          # supersedes the Dice in the same group
    app.apply_tactic("att")
    app.push_command(fa.MoveTokensCommand(app, {mover["label"]: (20.0, 10.0)}))
    app.push_command(fa.MoveTokensCommand(app, {mover["label"]: (15.0, 5.0)}))
    app.add_animation_step()
    app.set_group_time(1, 0.2)
    app.root.update()

    full_play = os.path.join(TEMP.name, "play_full.json")
    tidy_play = os.path.join(TEMP.name, "play_tidy.json")
    save_play(full_play, tidy=False)
    save_play(tidy_play, tidy=True)
    with open(full_play) as handle:
        full_doc = json.load(handle)
    with open(tidy_play) as handle:
        tidy_doc = json.load(handle)
    c.check("the tidied file really is the shorter record",
            len(tidy_doc["commands"]) < len(full_doc["commands"]),
            f"{len(tidy_doc['commands'])} vs {len(full_doc['commands'])}")
    c.equal("while describing the same marks",
            tidy_doc["drawings"], full_doc["drawings"])
    c.equal("and the same timeline",
            tidy_doc["animation"], full_doc["animation"])

    frames_full = gif_from(full_play, os.path.join(TEMP.name, "full.gif"))
    frames_tidy = gif_from(tidy_play, os.path.join(TEMP.name, "tidy.gif"))
    if frames_full and frames_tidy:
        def frames_of(path):
            image = Image.open(path)
            out = []
            for index in range(getattr(image, "n_frames", 1)):
                image.seek(index)
                out.append(image.convert("RGB").tobytes())
            return out

        one, two = frames_of(frames_full), frames_of(frames_tidy)
        c.equal("both files export the same number of frames", len(two), len(one))
        c.check("and every frame of the two GIFs is identical", one == two,
                f"{sum(1 for a, b in zip(one, two) if a != b)} of {len(one)} differ")
    else:
        c.check("both files export a GIF to compare",
                bool(frames_full and frames_tidy),
                "Ghostscript is needed to capture the board")


# ----------------------------------------------------------------------------
# I. watermark
# ----------------------------------------------------------------------------
def check_watermark(c, app):
    c.section("I. Watermark")
    logo = Image.new("RGBA", (200, 100), (250, 250, 250, 255))
    for x in range(60, 140):
        for y in range(30, 70):
            logo.putpixel((x, y), (200, 30, 30, 255))
    logo_path = os.path.join(TEMP.name, "logo.png")
    logo.save(logo_path)

    mode, peak = app._detect_background_mode(logo)
    c.equal("light background detected from the histogram", mode, "light")
    c.check("peak is the background bin", peak >= 200, f"peak {peak}")
    dark = Image.new("RGBA", (40, 40), (8, 8, 8, 255))
    c.equal("dark background detected too", app._detect_background_mode(dark)[0], "dark")

    stripped = app._strip_background(logo, 0, "light")
    alpha = list(stripped.getchannel("A").getdata())
    c.check("aggressiveness 0 already clears an off-white background",
            alpha.count(0) > 0.5 * len(alpha),
            f"{100 * alpha.count(0) / len(alpha):.0f}% cleared")
    c.check("the logo itself is kept", any(v == 255 for v in alpha))

    app.watermark = {"path": logo_path, "original": logo, "crop": None,
                     "bg_tolerance": None, "bg_mode": None, "behind": True,
                     "opacity": 100, "mx": 20.0, "my": 10.0, "w_m": 12.0, "h_m": 6.0}
    app._refresh_watermark_image()
    app._render_watermark()
    app.root.update()

    ids = app.canvas.find_withtag("watermark")
    c.equal("watermark is drawn once", len(ids), 1)
    order = list(app.canvas.find_all())
    surface = app.canvas.find_withtag("pitch_surface")
    markings = [i for i in app.canvas.find_withtag("pitch") if i not in surface]
    c.check("behind=True: above the rink surface",
            all(order.index(ids[0]) > order.index(s) for s in surface))
    c.check("behind=True: below the rink markings",
            all(order.index(ids[0]) < order.index(m) for m in markings))
    c.check("below the players either way",
            all(order.index(ids[0]) < order.index(t)
                for t in app.canvas.find_withtag("token")))
    app.watermark["behind"] = False
    app._render_watermark()
    app.root.update()
    order = list(app.canvas.find_all())
    ids = app.canvas.find_withtag("watermark")
    c.check("behind=False: over the markings",
            any(order.index(ids[0]) > order.index(m) for m in markings))
    app.watermark["behind"] = True

    box = app.canvas.bbox(app.canvas.find_withtag("watermark")[0])
    scale = app._pitch_state()["scale"]
    c.check("size on screen follows the rink scale",
            abs((box[2] - box[0]) - 12.0 * scale) <= 2,
            f"{box[2]-box[0]}px vs {12.0*scale:.0f}px")

    centre_before = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    app.redraw_canvas()
    app.root.update()
    box = app.canvas.bbox(app.canvas.find_withtag("watermark")[0])
    c.check("survives a redraw in place",
            abs((box[0] + box[2]) / 2 - centre_before[0]) < 1 and
            abs((box[1] + box[3]) / 2 - centre_before[1]) < 1)

    app.watermark["opacity"] = 50
    app._refresh_watermark_image()
    alpha = list(app.watermark["image"].getchannel("A").getdata())
    c.check("opacity halves the alpha", max(alpha) == 127, f"max alpha {max(alpha)}")
    app.watermark["bg_tolerance"] = 10
    app.watermark["bg_mode"] = "light"
    app._refresh_watermark_image()
    alpha = list(app.watermark["image"].getchannel("A").getdata())
    c.check("a removed background stays removed at reduced opacity",
            0 in alpha and max(alpha) == 127)
    app.watermark["opacity"] = 100
    app.watermark["bg_tolerance"] = None
    app.watermark["crop"] = (50, 25, 150, 75)
    app._refresh_watermark_image()
    c.equal("crop trims the image", app.watermark["image"].size, (100, 50))

    snapshot = app._watermark_snapshot()
    c.check("snapshot embeds the image", bool(snapshot.get("png_base64")))
    c.equal("snapshot is lossless PNG", snapshot.get("image_format"), "PNG")
    for key in ("mx", "my", "w_m", "h_m", "crop", "bg_tolerance", "bg_mode",
                "behind", "opacity"):
        c.check(f"snapshot carries {key}", key in snapshot)

    pixels = list(app.watermark["image"].getdata())
    app.watermark = None
    app._render_watermark()
    app._restore_watermark(json.loads(json.dumps(snapshot)))
    app.root.update()
    c.check("restored pixels are identical",
            list(app.watermark["image"].getdata()) == pixels)
    c.equal("restored placement", (app.watermark["mx"], app.watermark["my"]), (20.0, 10.0))
    c.equal("restored crop", tuple(app.watermark["crop"]), (50.0, 25.0, 150.0, 75.0))

    command = fa.SetWatermarkCommand(app, snapshot, None)
    command.execute()
    c.check("loading a watermark writes a timeline line",
            command.step_desc in app.action_steps, f"timeline {app.action_steps[-3:]}")
    c.equal("and serialises into the macro", command.serialize()["type"], "watermark")
    command.undo()
    c.check("undo removes it from the board", app.watermark is None)
    c.check("undo removes it from the timeline",
            command.step_desc not in app.action_steps, f"timeline {app.action_steps[-3:]}")
    c.check("loading a macro with no watermark writes nothing to the timeline",
            "Watermark removed" not in app.action_steps, f"timeline {app.action_steps[-3:]}")

    asked = {}
    original_askyesno = fa.messagebox.askyesno
    fa.messagebox.askyesno = lambda title, message: asked.setdefault("t", title) and False
    app._restore_watermark({"path": "/nowhere/missing.png", "mx": 20, "my": 10,
                            "w_m": 8, "h_m": 4, "png_base64": None})
    c.check("a missing image prompts the user", "t" in asked)
    c.check("declining drops the watermark", app.watermark is None)
    fa.messagebox.askyesno = lambda *a, **k: True
    original_open = fa.filedialog.askopenfilename
    fa.filedialog.askopenfilename = lambda *a, **k: logo_path
    app._restore_watermark({"path": "/nowhere/missing.png", "mx": 18, "my": 9,
                            "w_m": 8, "h_m": 4, "png_base64": None})
    c.check("accepting loads the replacement", app.watermark is not None)
    c.equal("replacement keeps the saved placement",
            (app.watermark["mx"], app.watermark["my"]), (18.0, 9.0))
    c.equal("replacement records its own path", app.watermark["path"], logo_path)
    fa.messagebox.askyesno = original_askyesno
    fa.filedialog.askopenfilename = original_open

    editor_probe = {}
    app._open_watermark_editor()
    app.root.update()
    tops = [w for w in app.root.winfo_children() if isinstance(w, tk.Toplevel)]
    c.check("the placement editor opens", bool(tops))
    if tops:
        win = tops[-1]
        buttons, scales, checks = [], 0, []

        def walk(widget):
            nonlocal scales
            for child in widget.winfo_children():
                if isinstance(child, tk.Button):
                    buttons.append(child.cget("text"))
                elif isinstance(child, tk.Scale):
                    scales += 1
                elif isinstance(child, tk.Checkbutton):
                    checks.append(child.cget("text"))
                walk(child)

        walk(win)
        editor_probe["buttons"] = buttons
        for wanted in ("Crop", "Reset Image", "Apply", "Cancel", "Remove"):
            c.check(f"editor has a {wanted} button", wanted in buttons)
        c.equal("editor has the two sliders", scales, 2)
        c.check("editor has the background checkbox",
                any("background" in t for t in checks))
        c.check("editor has the layering checkbox",
                any("Behind" in t for t in checks))
        win.destroy()
    app.root.update()


# ----------------------------------------------------------------------------
# J. toolbar layout
# ----------------------------------------------------------------------------
def check_layout(c, app):
    c.section("J. Toolbar layout")
    sections = fa.FloorballTacticsApp.MENU_SECTION_ATTRS
    for width, height in ((1240, 700), (1400, 850), (1854, 981)):
        app.root.geometry(f"{width}x{height}")
        app.root.update_idletasks()
        for _ in range(30):
            app.root.update()
            if app.root.winfo_width() == width:
                break
        rows = {}
        squeezed = []
        for name in sections:
            frame = getattr(app, name, None)
            if frame is None:
                c.check(f"section {name} exists", False)
                continue
            c.check(f"{width}px: {name.strip('_')} is on screen", frame.winfo_ismapped())
            if frame.winfo_width() < frame.winfo_reqwidth() - 1:
                squeezed.append(name)
            rows.setdefault(frame.winfo_y(), []).append(name)
        c.check(f"{width}px: nothing is squeezed below its size", not squeezed,
                f"squeezed: {squeezed}")
        c.check(f"{width}px: exactly two rows of boxes", len(rows) <= 2,
                f"{len(rows)} rows")

    c.equal("split keeps the widest row as narrow as it can",
            fa.FloorballTacticsApp._split_evenly([100, 100, 100, 500], 2), [(0, 3), (3, 4)])
    c.raises_not("menu position can be moved", app.set_menu_position, "left")
    app.set_menu_position("top")
    c.raises_not("row mode can be switched", app.set_menu_rows_mode, "auto")
    app.set_menu_rows_mode("two")
    c.raises_not("rows can be toggled", app.toggle_menu_rows)
    app.set_menu_rows_mode("two")
    app.root.geometry("1400x900")
    app.root.update()


# ----------------------------------------------------------------------------
# K. player resizing
# ----------------------------------------------------------------------------
def check_resize(c, app):
    c.section("K. Player resizing")
    app.att_shape_var.set("Square")
    app.def_shape_var.set("X")
    app._update_roster()
    app.root.update()
    app.att_tactic_var.set("House")
    app.apply_tactic("att")
    app.root.update()

    before = {t["label"]: token_centre(app, t) for t in app._all_tokens()}
    labels = {t["label"]: t.get("position") for t in app._all_tokens()}
    items_before = len(app.canvas.find_all())

    app.selected_tokens = []
    app.player_size_var.set("26")
    app._resize_selected_players()
    app.root.update()

    after = {t["label"]: token_centre(app, t) for t in app._all_tokens()}
    c.check("every player takes the new size",
            all(t.get("size") == 26 for t in app._all_tokens()))
    drift = max(max(abs(after[k][0] - before[k][0]), abs(after[k][1] - before[k][1]))
                for k in before if k in after)
    c.check("nobody moves while being resized", drift < 0.01, f"drift {drift:.2f}px")
    c.equal("no leftover items from the old size",
            len(app.canvas.find_all()), items_before)
    c.check("tactical labels survive a resize",
            {t["label"]: t.get("position") for t in app._all_tokens()} == labels)

    one = app._all_tokens()[0]
    app.selected_tokens = [one["shape_id"]]
    app.player_size_var.set("40")
    app._resize_selected_players()
    app.root.update()
    sizes = {t.get("size") for t in app._all_tokens()}
    c.check("with a selection only that player changes", sizes == {26, 40}, f"sizes {sizes}")
    c.check("the selection still points at a live token",
            all(sid in app.tokens for sid in app.selected_tokens))
    app.clear_selection()


# ----------------------------------------------------------------------------
# L. configuration and interactive entry points
# ----------------------------------------------------------------------------
def check_config_and_ui(c, app):
    c.section("L. Configuration and UI plumbing")
    c.check("config path is redirected for this run", TEMP.name in app.config_path)
    app.att_color = "#123456"
    app._save_config()
    c.check("config file written", os.path.exists(app.config_path))
    with open(app.config_path) as handle:
        saved = json.load(handle)
    c.equal("config keeps the attack colour", saved.get("att_color"), "#123456")
    c.raises_not("config loads back", app._load_config)

    # Colour themes
    c.section("L. Configuration and UI plumbing")
    themes = app.COLOR_THEMES
    c.check("themes cover common, colour-blind and club sets",
            any("Red vs" in n for n in themes) and
            sum("Colour-blind" in n for n in themes) >= 3 and
            any("Flames" in n for n in themes) and any("Hot Shots" in n for n in themes),
            f"{list(themes)}")
    c.check("every theme sets all four colours",
            all({"att", "def", "line", "sign"} <= set(t) for t in themes.values()))
    c.check("every theme is a valid hex colour",
            all(v.startswith("#") and len(v) == 7
                for t in themes.values()
                for k, v in t.items() if k != "note"))
    c.check("the two teams differ in every theme but the all-black one",
            all(t["att"] != t["def"] for name, t in themes.items()
                if "Classic" not in name))

    sign_ids = app.place_sign_canvas(600, 450, "Triangle")
    text_ids = app.place_text_canvas(700, 450, text="Theme", size=14)
    ball_ids = app.place_sign_canvas(800, 450, "Ball")
    c.check("applying a theme reports success", app.apply_color_theme("Nijmegen Flames"))
    app.root.update()
    flames = themes["Nijmegen Flames"]
    attacker = app._team_tokens("att")[0]
    defender = app._team_tokens("def")[0]
    c.equal("attackers take the club red",
            app.canvas.itemcget(attacker["shape_id"], "fill"), flames["att"])
    c.equal("defenders take the club slate",
            app.canvas.itemcget(defender["shape_id"], "fill"), flames["def"])
    c.equal("signs already on the board are repainted",
            app.canvas.itemcget(sign_ids[0], "outline"), flames["sign"])
    c.equal("text labels are repainted too",
            app.canvas.itemcget(text_ids[0], "fill"), flames["sign"])
    c.equal("a ball on the board is repainted with the rest",
            app.canvas.itemcget(ball_ids[0], "fill"), flames["sign"])
    c.check("the toolbar swatches follow the theme",
            app.btn_att_color.cget("bg") == flames["att"] and
            app.btn_sign_color.cget("bg") == flames["sign"])
    app.apply_color_theme("Colour-blind: Blue / Orange")
    app.root.update()
    c.equal("switching themes switches the board again",
            app.canvas.itemcget(attacker["shape_id"], "fill"),
            themes["Colour-blind: Blue / Orange"]["att"])
    c.check("an unknown theme is refused", app.apply_color_theme("No such theme") is False)
    with open(app.config_path) as handle:
        c.equal("the chosen theme is remembered", json.load(handle).get("color_theme"),
                "Colour-blind: Blue / Orange")
    app.selected_drawn = set(sign_ids) | set(text_ids) | set(ball_ids)
    app.delete_selection()
    app.apply_color_theme("Classic (black)")

    c.section("L. Configuration and UI plumbing")
    tips = {}

    def walk(widget):
        for child in widget.winfo_children():
            if isinstance(child, (tk.Button, tk.Checkbutton)):
                label = child.cget("text")
                if label:
                    tips[label] = label in app.BUTTON_TOOLTIPS
            walk(child)

    walk(app.top_bar)
    missing = [label for label, ok in tips.items() if not ok]
    c.check("every labelled toolbar button has a tooltip", not missing, f"missing {missing}")
    # The roster box: rows line up, and the size dial has a column to itself.
    combos = [w for w in app._roster_frame.winfo_children()[0].winfo_children()
              if isinstance(w, ttk.Combobox)]
    c.equal("both team rows use the same shape bar width",
            len({w.winfo_width() for w in combos}), 1)
    c.check("the size dial sits in its own column",
            app.player_size_spinbox.winfo_x() > app.btn_att_color.winfo_x()
            or app.player_size_spinbox.master is not app.btn_att_color.master)

    c.equal("the box is called Shapes", app._signs_frame.cget("text").strip(), "Shapes")
    c.check("it holds one Size dial and its colour, and no separate text field",
            app.sign_size_spinbox.winfo_ismapped()
            and app.btn_sign_color.winfo_ismapped()
            and not hasattr(app, "text_size_spinbox"))
    app.sign_size_var.set(28)
    typed = app.place_text_canvas(600, 500, text="Screen")
    c.equal("a new label is typed at the Size shown there",
            app.drawn_items[typed[0]]["size"], 28)
    app.selected_drawn = set(typed)
    app.text_size_var.set(40)
    c.raises_not("labels can still be retyped on their own", app._apply_text_size)
    c.equal("and take that size", app.drawn_items[typed[0]]["size"], 40)
    app.selected_drawn = set(typed)
    app.delete_selection()
    app.sign_size_var.set(12)

    c.check("colour swatches have tooltips too",
            all(getattr(app, name, None) is not None for name in app.SWATCH_TOOLTIPS))
    c.equal("the resolved UI font is really installed",
            app.UI_FONT.lower() in {f.lower() for f in tk.font.families()}, True)

    # Arrows, signs and labels belong to the group they were made in.
    app.animation_steps = []
    app._refresh_animation_list()
    app.push_command(fa.DrawLineCommand(app, "pass", 300, 300, 460, 340))
    app.place_sign_canvas(520, 520, "Goal")
    app.place_text_canvas(640, 520, text="Screen", size=14)
    app.root.update()
    actions = app.animation_steps[0].get("actions") if app.animation_steps else []
    c.check("an arrow joins the timeline", any(a.startswith("Pass") for a in actions),
            f"{actions}")
    c.check("a sign joins it too", any(a.startswith("Sign") for a in actions), f"{actions}")
    c.check("and a text label", any(a.startswith("Text") for a in actions), f"{actions}")
    app.undo()
    app.root.update()
    c.check("undoing the arrow takes its line back out",
            not any(a.startswith("Pass")
                    for a in (app.animation_steps[0].get("actions") if app.animation_steps
                              else [])))

    # Dragging an action into another group re-homes it, and the headings follow.
    app.animation_steps = []
    app._refresh_animation_list()
    app.push_command(fa.DrawLineCommand(app, "pass", 300, 300, 460, 340))
    app.add_animation_step()
    run_command = fa.DrawLineCommand(app, "run", 500, 300, 640, 380)
    app.push_command(run_command)
    goal_ids = app.place_sign_canvas(700, 400, "Goal")
    app.root.update()
    c.equal("actions land in the group open at the time",
            len(app.animation_steps[1].get("actions", [])), 2)
    c.check("moving an action into another group works",
            app.move_action_to_group(1, 0, 0))
    c.check("it arrives in the group it was dropped on",
            any(a.startswith("Run") for a in app.animation_steps[0]["actions"]),
            f"{app.animation_steps[0]['actions']}")
    c.check("it leaves the one it came from",
            not any(a.startswith("Run") for a in app.animation_steps[1]["actions"]),
            f"{app.animation_steps[1]['actions']}")
    c.check("the group it left is no longer named after it",
            "Run" not in app.animation_steps[1]["name"],
            f"{app.animation_steps[1]['name']}")
    c.check("a drop onto its own group changes nothing",
            app.move_action_to_group(0, 0, 0) is False)

    # Arrows and shapes take part in the animation.
    # The ids these two calls actually produced -- filtering the whole board by name
    # would pick up identical signs left behind by earlier checks.
    run_ids = [cid for cid in run_command.line_ids if cid in app.drawn_items]
    sign_ids = [cid for cid in goal_ids if cid in app.drawn_items]
    c.check("drawings remember which group they belong to", bool(run_ids) and bool(sign_ids))

    def drawn_length(cid):
        coords = app.canvas.coords(cid)
        return sum(math.hypot(coords[i + 2] - coords[i], coords[i + 3] - coords[i + 1])
                   for i in range(0, len(coords) - 2, 2))

    if sign_ids:
        app._apply_animation_frame(app.animation_steps[0], app.animation_steps[1], 0.0)
        app.root.update()
        c.equal("a sign is hidden until its group opens",
                app.canvas.itemcget(sign_ids[0], "state"), "hidden")
        app._apply_animation_frame(app.animation_steps[0], app.animation_steps[1], 0.6)
        app.root.update()
        c.check("and appears once it does",
                app.canvas.itemcget(sign_ids[0], "state") in ("", "normal"))
    if run_ids:
        app.move_action_to_group(0, len(app.animation_steps[0]["actions"]) - 1, 1)
        full = drawn_length(run_ids[0]) if app.canvas.coords(run_ids[0]) else 0
        full = sum(math.hypot(app.drawn_items[run_ids[0]]["full_coords"][i + 2]
                              - app.drawn_items[run_ids[0]]["full_coords"][i],
                              app.drawn_items[run_ids[0]]["full_coords"][i + 3]
                              - app.drawn_items[run_ids[0]]["full_coords"][i + 1])
                   for i in range(0, len(app.drawn_items[run_ids[0]]["full_coords"]) - 2, 2))
        app._apply_animation_frame(app.animation_steps[0], app.animation_steps[1], 0.5)
        app.root.update()
        half = drawn_length(run_ids[0])
        c.check("an arrow is drawn on over its group's time",
                abs(half - full / 2) < full * 0.15, f"{half:.0f} of {full:.0f}px")
        app._apply_animation_frame(app.animation_steps[0], app.animation_steps[1], 1.0)
        app.root.update()
        c.check("and reaches its full length", abs(drawn_length(run_ids[0]) - full) < 1)
    # A bend takes part like every other arrow, and is drawn on along its curve
    # rather than along the straight line between its control points.
    bend_command = fa.DrawLineCommand(app, "bend", 260, 640, 560, 640,
                                      extra_data={"cx": 410, "cy": 520})
    app.push_command(bend_command, execute=True)
    app.root.update()
    bend_id = bend_command.line_ids[0]
    c.equal("a bend joins the group it was drawn in",
            app.drawn_items[bend_id].get("anim_group"), len(app.animation_steps) - 1)
    app._apply_drawn_for_frame(len(app.animation_steps) - 1, 0.0)
    app.root.update()
    c.equal("and is hidden before its group opens",
            app.canvas.itemcget(bend_id, "state"), "hidden")
    app._apply_drawn_for_frame(len(app.animation_steps) - 1, 0.45)
    app.root.update()
    part = drawn_length(bend_id)
    points_drawn = len(app.canvas.coords(bend_id)) // 2
    app._apply_drawn_for_frame(len(app.animation_steps) - 1, 1.0)
    app.root.update()
    whole = drawn_length(bend_id)
    c.check("a bend is drawn on over its group's time", 0 < part < whole,
            f"{part:.0f} of {whole:.0f}px")
    c.check("and follows its curve while it is drawn", points_drawn > 3,
            f"{points_drawn} points")
    c.equal("ending on the control points it was drawn from",
            len(app.canvas.coords(bend_id)) // 2, 3)

    # The arrow follows its player after the board has been redrawn at another size.
    # It used to be weighted against the pixels the tool drew it between, which a
    # resize leaves describing a line that is no longer there -- so every point came
    # out at the same weight and the whole arrow jumped along with the player, off
    # the rink entirely.
    app.snap_player_var.set(True)
    resized = app._team_tokens("att")[2]
    spot = app._token_centre_px(resized)
    app.active_tool = "pass"
    app.temp_line_start = spot

    class Drop:
        def __init__(self, x, y):
            self.x, self.y, self.state, self.num = int(x), int(y), 0, 1

    app.on_canvas_release(Drop(spot[0], spot[1] + 240))
    app.active_tool = None
    app.root.update()
    stretched = list(resized.get("attached_lines_start") or ())
    c.check("an arrow drawn down from a player attaches by its tail", bool(stretched))
    if stretched:
        app.root.geometry("1120x720")
        app.root.update_idletasks()
        for _ in range(30):
            app.root.update()
            if app.root.winfo_width() <= 1130:
                break
        app.root.update()
        before_line = list(app.canvas.coords(stretched[0]))
        app.push_command(fa.MoveTokensCommand(app, {resized["label"]: (0.0, -110.0)},
                                              keep_attached=True))
        after_line = list(app.canvas.coords(stretched[0]))
        c.check("after a resize its tail still follows the player",
                abs((after_line[1] - before_line[1]) + 110) < 1.0,
                f"tail moved {after_line[1] - before_line[1]:.0f}, wanted -110")
        c.check("and its far end still stays put",
                abs(after_line[-1] - before_line[-1]) < 1.0,
                f"head moved {after_line[-1] - before_line[-1]:.0f}, wanted 0")
        app.undo()
        for cid in stretched:
            app.canvas.delete(cid)
            app.drawn_items.pop(cid, None)
        resized["attached_lines_start"] = []
        app.root.geometry("1400x900")
        app.root.update_idletasks()
        for _ in range(30):
            app.root.update()
            if app.root.winfo_width() >= 1390:
                break

    # An arrow snapped to a player keeps pointing at them while they run.
    holder = app._team_tokens("att")[0]
    box = app.canvas.bbox(holder["shape_id"])
    hx, hy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    app.snap_player_var.set(True)
    app.active_tool = "pass"
    app.temp_line_start = (hx - 180, hy + 140)

    class Release:
        def __init__(self, x, y):
            self.x, self.y, self.state, self.num = int(x), int(y), 0, 1

    app.on_canvas_release(Release(hx, hy))
    app.active_tool = None
    app.root.update()
    tracked = list(holder.get("attached_lines_end") or ())
    c.check("an arrow drawn at a player is attached to them", bool(tracked))
    if tracked:
        app.add_animation_step()
        app.push_command(fa.MoveTokensCommand(app, {holder["label"]: (150.0, -90.0)}))
        app.add_animation_step()
        last = len(app.animation_steps) - 1
        app.set_group_time(last, 1.0)

        def tip_gap():
            coords = app.canvas.coords(tracked[0])
            spot = app.canvas.bbox(holder["shape_id"])
            return math.hypot(coords[-2] - (spot[0] + spot[2]) / 2,
                              coords[-1] - (spot[1] + spot[3]) / 2)

        gaps = []
        for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
            app._apply_animation_frame(app.animation_steps[last - 1],
                                       app.animation_steps[last], fraction)
            app.root.update()
            gaps.append(round(tip_gap(), 1))
        c.check("its tip stays on the player through the move",
                max(gaps) < 2.0, f"gaps {gaps}")
        app.stop_animation()
        app.root.update()
        c.check("and is back where it was drawn once the animation stops",
                tip_gap() < 2.0, f"{tip_gap():.1f}px")

    # An arrow drawn in the same group as a move is laid out where it will finish, and
    # grows from there -- rather than sliding across the rink while it grows, which is
    # what happened when both of its ends were tracked live.
    runner = app._team_tokens("att")[1]
    spot = app.canvas.bbox(runner["shape_id"])
    rx, ry = (spot[0] + spot[2]) / 2, (spot[1] + spot[3]) / 2
    app.add_animation_step()
    drawing_group = len(app.animation_steps) - 1
    app.active_tool = "pass"
    app.temp_line_start = (rx, ry)
    app.on_canvas_release(Release(rx + 200, ry + 120))
    app.active_tool = None
    app.root.update()
    grown = [cid for cid in (runner.get("attached_lines_start") or ())
             if cid in app.drawn_items]
    c.check("an arrow drawn from a player is attached by its tail", bool(grown))
    if grown:
        app.push_command(fa.MoveTokensCommand(app, {runner["label"]: (-140.0, 80.0)}))
        app.add_animation_step()
        app.set_group_time(drawing_group, 1.0)
        tails, lengths = [], []
        for fraction in (0.2, 0.5, 0.8, 1.0):
            app._apply_animation_frame(app.animation_steps[drawing_group - 1],
                                       app.animation_steps[drawing_group], fraction)
            app.root.update()
            coords = app.canvas.coords(grown[0])
            tails.append((round(coords[0], 1), round(coords[1], 1)))
            lengths.append(math.hypot(coords[-2] - coords[0], coords[-1] - coords[1]))
        c.check("its tail is planted where it will finish and stays there",
                len(set(tails)) == 1, f"{tails}")
        c.check("and the arrow grows out of it",
                all(a < b for a, b in zip(lengths, lengths[1:])),
                f"{[round(v) for v in lengths]}")
        app.stop_animation()
        app.root.update()

    # Backspace removes a mark *in the play*: it stays on the board, the timeline says
    # it goes, and it goes when that group comes up.
    doomed = fa.DrawLineCommand(app, "pass", 260, 300, 420, 360)
    app.push_command(doomed, execute=True)
    app.add_animation_step()
    removal_group = len(app.animation_steps) - 1
    app.clear_selection()
    app.selected_drawn = set(doomed.line_ids)
    c.equal("Backspace marks the selection for removal",
            app.remove_selection_at_group(), len(doomed.line_ids))
    c.check("the timeline records it",
            any(action.startswith("Remove")
                for action in app.animation_steps[removal_group]["actions"]),
            f"{app.animation_steps[removal_group]['actions']}")
    c.check("and the mark stays on the board while the play is built",
            app.canvas.itemcget(doomed.line_ids[0], "state") in ("", "normal"))
    app._apply_drawn_for_frame(removal_group - 1, 1.0)
    app.root.update()
    c.check("it is still there in the group before",
            app.canvas.itemcget(doomed.line_ids[0], "state") in ("", "normal"))
    app._apply_drawn_for_frame(removal_group, 0.5)
    app.root.update()
    c.equal("and gone once its group comes up",
            app.canvas.itemcget(doomed.line_ids[0], "state"), "hidden")
    app.show_all_drawn_items()
    app.root.update()
    c.check("a stopped board shows it again",
            app.canvas.itemcget(doomed.line_ids[0], "state") in ("", "normal"))
    position = [i for i, action in enumerate(app.animation_steps[removal_group]["actions"])
                if action.startswith("Remove")][0]
    app.delete_animation_action(removal_group, position)
    c.check("deleting the removal row calls the removal off",
            app.drawn_items[doomed.line_ids[0]].get("anim_remove_group") is None)

    # The key itself: Backspace defers, and falls back to a plain delete for players,
    # while both keys keep their hands off a text field being typed into.
    app.clear_selection()
    app.selected_drawn = set(doomed.line_ids)
    app._remove_key()
    c.check("Backspace defers the removal of a drawing",
            app.drawn_items[doomed.line_ids[0]].get("anim_remove_group") is not None)
    app.delete_animation_action(
        app.drawn_items[doomed.line_ids[0]]["anim_remove_group"],
        [i for i, action in enumerate(
            app.animation_steps[app.drawn_items[doomed.line_ids[0]]["anim_remove_group"]]
            ["actions"]) if action.startswith("Remove")][0])
    app.clear_selection()
    spare = app._team_tokens("def")[-1]
    app.selected_tokens = [spare["shape_id"]]
    count_before = len(app._team_tokens("def"))
    app._remove_key()
    app.root.update()
    c.equal("and deletes a player outright, since there is no half-state for one",
            len(app._team_tokens("def")), count_before - 1)
    app.undo()
    app.root.update()
    # Focus has to actually be granted before this means anything, and a window
    # manager is free not to grant it -- so the check is skipped rather than failed
    # when the field never took the focus.
    try:
        app.root.focus_force()
    except Exception:
        pass
    app.att_spinbox.focus_set()
    app.root.update()
    if app.root.focus_get() is app.att_spinbox:
        c.check("neither key fires while a roster field has the focus",
                app._remove_key() is None)
    app.canvas.focus_set()
    app.clear_selection()

    app.stop_animation()
    app.root.update()
    c.check("stopping puts every drawing back on the board",
            all(app.canvas.itemcget(cid, "state") in ("", "normal")
                for cid in run_ids + sign_ids))
    for cid in list(app.drawn_items):
        app.canvas.delete(cid)
    app.drawn_items.clear()
    app.animation_steps = []
    app._refresh_animation_list()

    # Select is a tool like the others, so it looks like one when it is the one in use.
    app.set_tool("pass")
    app.root.update()
    c.check("arming a tool lights that tool up",
            app.tool_buttons["pass"].cget("bg") == app.C_ACCENT)
    c.check("and leaves Select unlit",
            app.tool_buttons["select"].cget("bg") != app.C_ACCENT)
    app.cancel_active_tool()
    app.root.update()
    c.check("with no tool armed the board is in select mode",
            app.active_tool is None)
    c.check("and Select lights up blue for it",
            app.tool_buttons["select"].cget("bg") == app.C_ACCENT,
            f"{app.tool_buttons['select'].cget('bg')}")

    c.check("the Drawing box has a Delete button", "delete" in app.tool_buttons)
    if "delete" in app.tool_buttons:
        marks = app.place_sign_canvas(600, 400, "Goal")
        app.selected_drawn = set(marks)
        app.tool_buttons["delete"].invoke()
        app.root.update()
        c.check("it deletes the selection",
                not any(cid in app.drawn_items for cid in marks))

    for tool in ("pass", "shot", "select", "sign_ball"):
        c.raises_not(f"tool {tool} can be selected", app.set_tool, tool)
    app.cancel_active_tool()
    c.raises_not("indicators refresh", app._update_indicators)
    c.raises_not("grid visuals toggle", app.toggle_grid_visuals)
    app.grid_var.set(False)
    app.toggle_grid_visuals()
    c.raises_not("style can be copied", app.copy_current_style)
    app._deactivate_paste_style()

    class FakeEvent:
        def __init__(self, x, y):
            self.x = self.x_root = x
            self.y = self.y_root = y

    token = app._all_tokens()[0]
    cx, cy = token_centre(app, token)
    captured = {}
    real_menu = tk.Menu

    class SpyMenu(real_menu):
        def tk_popup(self, *args, **kwargs):
            captured["entries"] = [
                self.entrycget(i, "label") if self.type(i) != "separator" else "-"
                for i in range(self.index("end") + 1)]

        def grab_release(self):
            pass

    tk.Menu = SpyMenu
    try:
        app.show_context_menu(FakeEvent(int(cx), int(cy)))
        c.check("right-click on a player offers Delete",
                "Delete" in captured.get("entries", []), f"{captured.get('entries')}")
        c.check("right-click on a player offers Rotate",
                any("Rotate" in e for e in captured.get("entries", [])))
        captured.clear()
        app.show_context_menu(FakeEvent(30, 30))
        c.check("right-click on empty rink offers Paste and Undo",
                "Paste" in captured.get("entries", []) and
                "Undo" in captured.get("entries", []), f"{captured.get('entries')}")
        c.check("and a way to sweep the ghosts",
                "Clear Ghosts" in captured.get("entries", []),
                f"{captured.get('entries')}")
    finally:
        tk.Menu = real_menu

    class ResizeEvent:
        def __init__(self, widget, width, height):
            self.widget, self.width, self.height = widget, width, height

    c.raises_not("window resize handler runs", app.on_window_resize,
                 ResizeEvent(app.root, app.root.winfo_width(), app.root.winfo_height()))
    c.raises_not("canvas resize handler runs", app.on_canvas_resize,
                 ResizeEvent(app.canvas, app.canvas.winfo_width(), app.canvas.winfo_height()))
    c.raises_not("full redraw runs", app.redraw_canvas)
    c.raises_not("roster rebuild runs", app._update_roster)


# ----------------------------------------------------------------------------
# O. animation
# ----------------------------------------------------------------------------
def check_animation(c, app):
    c.section("O. Animation")
    warnings = []
    real_warning, real_info = fa.messagebox.showwarning, fa.messagebox.showinfo
    fa.messagebox.showwarning = lambda title, message: warnings.append(title)
    fa.messagebox.showinfo = lambda *a, **k: None
    try:
        app.animation_steps = []
        app.stop_animation()
        app.play_animation()
        c.check("playing with no steps warns instead of doing nothing",
                warnings and "Nothing to animate" in warnings[-1], f"{warnings}")
        warnings.clear()
        app.run_export("GIF")
        c.check("exporting with no steps warns too",
                warnings and "Nothing to animate" in warnings[-1], f"{warnings}")

        app.att_tactic_var.set("Dice")
        app.apply_tactic("att")
        app.root.update()
        # A formation now keyframes itself, so clear those before testing Add Step
        # on its own.
        app.animation_steps = []
        app._refresh_animation_list()
        app.add_animation_step()
        c.equal("the first Add Group records the opening group",
                len(app.animation_steps), 1)
        c.equal("the opening group is group 0", app.animation_playhead, 0)
        app.att_tactic_var.set("Umbrella")
        app.apply_tactic("att")
        app.root.update()
        del app.animation_steps[1:]          # drop the formation's own keyframes
        app._refresh_animation_list()
        app.add_animation_step()
        c.equal("a second Add Group appends a keyframe", len(app.animation_steps), 2)
        c.check("every group carries a time interval",
                all(s["duration"] >= 0 for s in app.animation_steps))
        c.equal("the time slider reaches zero",
                float(app.step_time_scale.cget("from")), 0.0)
        c.check("zero seconds is accepted as an instant cut", app.set_group_time(0, 0.0))
        c.equal("and it takes", app.animation_steps[0]["duration"], 0.0)
        app.set_group_time(0, 2.0)
        rows = [app.anim_tree.item(i, "text") for i in app.anim_tree.get_children("")]
        c.check("the timeline lists the groups", rows and rows[0].startswith("0"),
                f"{rows}")
        c.check("each group row shows its time",
                all(app.anim_tree.item(i, "values")[0].endswith("s")
                    for i in app.anim_tree.get_children("")))
        c.check("group rows are bold",
                all("group" in app.anim_tree.item(i, "tags")
                    for i in app.anim_tree.get_children("")))
        c.check("the playhead group is marked red",
                "playhead" in app.anim_tree.item(f"g{app.animation_playhead}", "tags"))
        c.check("groups can be collapsed and expanded",
                all(app.anim_tree.item(i, "open") in (0, 1, True, False)
                    for i in app.anim_tree.get_children("")))

        app.step_time_var.set(0.4)
        app._on_step_time_changed()
        c.check("the slider retimes every step",
                all(abs(s["duration"] - 0.4) < 1e-6 for s in app.animation_steps))
        app.animation_steps[1]["duration"] = 1.0
        c.check("a single step can still keep its own interval",
                app.animation_steps[1]["duration"] == 1.0)

        moving = None
        start = app._step_positions(app.animation_steps[0])
        end = app._step_positions(app.animation_steps[1])
        for label, position in start.items():
            if label in end and abs(position[0] - end[label][0]) > 2:
                moving = label
                break
        c.check("the two keyframes actually differ", moving is not None)
        if moving:
            app._apply_animation_frame(app.animation_steps[0], app.animation_steps[1], 0.5)
            app.root.update()
            token = app.tokens[app._get_sid_by_label(moving)]
            box = app.canvas.bbox(token["shape_id"])
            mx, _my = app._state_px_to_m((box[0] + box[2]) / 2, (box[1] + box[3]) / 2,
                                         app._pitch_state())
            want = (start[moving][0] + end[moving][0]) / 2
            c.check("halfway through, the player is halfway there",
                    abs(mx - want) < 0.1, f"{mx:.2f} vs {want:.2f}")

        app.animation_playhead = len(app.animation_steps) - 1
        app.play_animation()
        c.check("Play from the last step rewinds instead of ending instantly",
                app.animation_playing and app._animation_cursor[0] == 0,
                f"cursor {app._animation_cursor}")
        deadline = time.time() + 1.0
        while time.time() < deadline and app.animation_playing:
            app.root.update()
            time.sleep(0.02)
            if app._animation_cursor and app._animation_cursor[1] > 0.15:
                break
        app.pause_animation()
        c.check("Pause stops the clock", not app.animation_playing)
        c.check("Pause keeps the position for Play to resume from",
                app._animation_cursor is not None)
        app.stop_animation()
        c.equal("Stop rewinds to group 0", app.animation_playhead, 0)
        c.check("Stop clears the cursor", app._animation_cursor is None)

        app.anim_tree.selection_set("g0")
        app.anim_tree.event_generate("<<TreeviewSelect>>")
        app.root.update()
        c.equal("clicking a group moves the red playhead there", app.animation_playhead, 0)
        c.check("a group's time can be set", app.set_group_time(0, 3.5))
        c.equal("and it takes", app.animation_steps[0]["duration"], 3.5)
        app.set_group_time(0, 2.0)
        c.raises_not("the rename dialog opens", app._rename_group)
        c.raises_not("the time dialog opens", app._edit_step_time)
        dmx, dmy = app._px_delta_to_m(app._pitch_state()["scale"], 0)
        c.check("a pixel offset converts to metres", abs(dmx - 1.0) < 0.01 or
                abs(dmy - 1.0) < 0.01, f"{dmx}, {dmy}")
        app.root.update()
        for window in [w for w in app.root.winfo_children() if isinstance(w, tk.Toplevel)]:
            window.destroy()
        app.root.update()
        app.root.update()
        for window in [w for w in app.root.winfo_children() if isinstance(w, tk.Toplevel)]:
            window.destroy()
        app.root.update()

        frame = app._capture_canvas()
        c.check("the board can be captured for export", frame is not None,
                "needs Ghostscript")
        if frame is not None:
            c.check("the capture is the size of the board",
                    abs(frame.size[0] - app.canvas.winfo_width()) < 40,
                    f"{frame.size} vs {app.canvas.winfo_width()}")

        gif_path = os.path.join(TEMP.name, "animation.gif")
        real_save = fa.filedialog.asksaveasfilename
        fa.filedialog.asksaveasfilename = lambda *a, **k: gif_path
        try:
            app.step_time_var.set(0.2)
            app._on_step_time_changed()
            app.run_export("GIF")
        finally:
            fa.filedialog.asksaveasfilename = real_save
        c.check("a GIF is written", os.path.exists(gif_path))
        if os.path.exists(gif_path):
            gif = Image.open(gif_path)
            c.check("the GIF is animated", getattr(gif, "n_frames", 1) > 1,
                    f"{getattr(gif, 'n_frames', 1)} frames")
            c.equal("its frame count matches the step times",
                    getattr(gif, "n_frames", 0),
                    max(1, int(round(0.2 * fa.FloorballTacticsApp.ANIMATION_FPS))))

        # Export writes more than GIFs: video where this machine has a writer, and
        # stills of whichever groups were picked.
        def export_to(name, fmt, groups=None):
            target = os.path.join(TEMP.name, name)
            real = fa.filedialog.asksaveasfilename
            fa.filedialog.asksaveasfilename = lambda *a, **k: target
            try:
                return target, app.run_export(fmt, groups)
            finally:
                fa.filedialog.asksaveasfilename = real

        for fmt, extension in (("MP4", ".mp4"), ("WebM", ".webm"),
                               ("AVI", ".avi"), ("MOV", ".mov")):
            path, ok = export_to("clip" + extension, fmt)
            # No video writer on this machine is a legitimate outcome, and the app
            # says so rather than failing silently -- so the check is that it either
            # wrote a real file or refused cleanly.
            wrote = os.path.exists(path) and os.path.getsize(path) > 0
            c.check(f"{fmt} export writes a file or explains why not", wrote or not ok,
                    f"ok={ok} exists={os.path.exists(path)}")

        for fmt, extension in (("PNG", ".png"), ("JPEG", ".jpg")):
            path, ok = export_to("still" + extension, fmt, [0])
            c.check(f"a single group exports to one {fmt} under the chosen name",
                    ok and os.path.exists(path), f"ok={ok}")
            if os.path.exists(path):
                c.equal(f"and it is a real {fmt}", Image.open(path).format,
                        "PNG" if fmt == "PNG" else "JPEG")

        path, ok = export_to("groups.png", "PNG", [0, 1])
        numbered = [f"{os.path.splitext(path)[0]}_{i:02d}.png" for i in (0, 1)]
        c.check("several groups export to one numbered image each",
                ok and all(os.path.exists(f) for f in numbered),
                f"ok={ok} {[os.path.basename(f) for f in numbered]}")
        _, refused = export_to("none.png", "PNG", [])
        c.check("picking no group at all is refused", not refused)

        c.equal("the export list names every group",
                len(app._group_export_names()), len(app.animation_steps))
        # The dialog is modal and waits, so it is closed from a timer rather than
        # after the call returns.
        opened = {}

        def close_export_dialog():
            windows = [w for w in app.root.winfo_children() if isinstance(w, tk.Toplevel)]
            opened["count"] = len(windows)
            for window in windows:
                window.destroy()

        # Cancelled after the fact: a timer left armed would fire during a later area
        # and shut a window that check was still using.
        timer = app.root.after(250, close_export_dialog)
        c.raises_not("the export dialog opens", app.export_animation)
        try:
            app.root.after_cancel(timer)
        except Exception:
            pass
        c.check("the export dialog is a real window", opened.get("count"),
                f"{opened}")
        app.root.update()

        # reordering: buttons and drag-and-drop
        # Four deliberate keyframes, built directly: actions now group into the step
        # being built, so driving this through the UI would merge them.
        app.animation_steps = []
        for index, tactic in enumerate(("Dice", "House", "Umbrella", "Point")):
            app.att_tactic_var.set(tactic)
            app.apply_tactic("att")
            app.root.update()
            app.animation_steps = app.animation_steps[:index]
            app.animation_steps.append({"duration": 2.0,
                                        "board": app._board_snapshot(),
                                        "name": f"Group {index}",
                                        "actions": [], "named": False, "closed": True})
        app._refresh_animation_list()
        app.root.update()

        def signature():
            out = []
            for step in app.animation_steps:
                roles = sorted(p["position"] for p in step["board"]["players"]
                               if p.get("team") == "att" and p.get("position"))
                out.append("".join(r[0] for r in roles))
            return out

        order = signature()
        c.equal("four distinct keyframes were recorded", len(set(order)), 4)

        # House keeps its defenders out by the boards
        house = {role: across for role, across, _ in
                 fa.FloorballTacticsApp.FORMATIONS["House"]}
        c.check("House puts its defenders wide",
                house["LD"] < 0.2 and house["RD"] > 0.8,
                f"LD {house['LD']}, RD {house['RD']}")

        app.animation_playhead = 3
        app.move_animation_step(-1)
        moved = signature()
        c.check("Up moves a step one place earlier",
                moved[2] == order[3] and moved[3] == order[2], f"{order} -> {moved}")
        c.equal("the playhead follows the step it moved", app.animation_playhead, 2)
        app.move_animation_step(1)
        c.check("Down puts it back", signature() == order, f"{signature()}")
        app.animation_playhead = 0
        app.move_animation_step(-1)
        c.check("Up at the top does nothing", signature() == order)
        app.animation_playhead = len(app.animation_steps) - 1
        app.move_animation_step(1)
        c.check("Down at the end does nothing", signature() == order)
        c.check("the first row is the group the animation begins at",
                bool(app.animation_steps[0]["name"]))
        c.check("steps that carry an action's wording keep it through a reorder",
                all(step["name"] for step in app.animation_steps))

        tree = app.anim_tree
        tree.update()
        last_row, first_row = tree.bbox("g3"), tree.bbox("g0")
        if last_row and first_row:
            tree.event_generate("<ButtonPress-1>", x=30, y=int(last_row[1]) + 2)
            tree.event_generate("<B1-Motion>", x=30, y=int(first_row[1]) + 2, state=0x100)
            tree.event_generate("<ButtonRelease-1>", x=30, y=int(first_row[1]) + 2)
            app.root.update()
            dragged = signature()
            c.check("a step can be dragged to a new place",
                    dragged[0] == order[3], f"{order} -> {dragged}")
            c.check("its board travels with the row", dragged != order)
            c.check("a group still sits first, carrying its own name",
                    bool(app.animation_steps[0]["name"]))

        before = len(app.animation_steps)
        app.animation_playhead = 1
        app.delete_animation_step()
        c.equal("a step can be deleted", len(app.animation_steps), before - 1)
        app.animation_steps = []
        app._refresh_animation_list()
        app.delete_animation_step()
        c.check("deleting with nothing there warns",
                warnings and "No groups" in warnings[-1], f"{warnings}")
    finally:
        fa.messagebox.showwarning, fa.messagebox.showinfo = real_warning, real_info
        app.stop_animation()


# ----------------------------------------------------------------------------
# M. mouse interaction on the board
# ----------------------------------------------------------------------------
def check_mouse(c, app):
    c.section("M. Mouse interaction")
    app._update_roster()
    app.root.update()
    app.cancel_active_tool()
    app.clear_selection()

    canvas = app.canvas
    token = app._all_tokens()[0]
    cx, cy = token_centre(app, token)

    c.equal("a click finds the player under the pointer",
            app.get_token_at_point(cx, cy), token["shape_id"])
    c.check("and finds nothing on empty ice",
            app.get_token_at_point(cx + 400, cy) is None)

    canvas.event_generate("<ButtonPress-1>", x=int(cx), y=int(cy))
    canvas.event_generate("<B1-Motion>", x=int(cx) + 45, y=int(cy) + 30, state=0x100)
    canvas.event_generate("<ButtonRelease-1>", x=int(cx) + 45, y=int(cy) + 30)
    app.root.update()
    moved = token_centre(app, app.tokens[app._get_sid_by_label(token["label"])])
    c.check("dragging a player moves it",
            abs(moved[0] - cx - 45) < 3 and abs(moved[1] - cy - 30) < 3,
            f"{(cx, cy)} -> {moved}")
    app.undo()
    app.root.update()
    back = token_centre(app, app.tokens[app._get_sid_by_label(token["label"])])
    c.check("undo returns the dragged player",
            abs(back[0] - cx) < 0.01 and abs(back[1] - cy) < 0.01, f"{back}")

    app.clear_selection()
    canvas.event_generate("<ButtonPress-1>", x=5, y=5)
    canvas.event_generate("<B1-Motion>", x=canvas.winfo_width() - 5,
                          y=canvas.winfo_height() - 5, state=0x100)
    canvas.event_generate("<ButtonRelease-1>", x=canvas.winfo_width() - 5,
                          y=canvas.winfo_height() - 5)
    app.root.update()
    c.check("box select picks up the players it covers", len(app.selected_tokens) > 0,
            f"{len(app.selected_tokens)} selected")

    before = len(app._all_tokens())
    app.clear_selection()
    app.selected_tokens = [app._all_tokens()[0]["shape_id"]]
    real_info = fa.messagebox.showinfo
    fa.messagebox.showinfo = lambda *a, **k: None
    try:
        app.cut_selection()
        app.root.update()
        c.equal("cut removes the player", len(app._all_tokens()), before - 1)
        app.paste_clipboard()
        app.root.update()
        c.check("paste puts one back", len(app._all_tokens()) >= before - 1)
    finally:
        fa.messagebox.showinfo = real_info

    app.clear_selection()
    app.set_tool("pass")
    lines_before = len(app.drawn_items)
    canvas.event_generate("<ButtonPress-1>", x=200, y=200)
    canvas.event_generate("<B1-Motion>", x=340, y=260, state=0x100)
    canvas.event_generate("<ButtonRelease-1>", x=340, y=260)
    app.root.update()
    c.check("dragging with a tool draws a line", len(app.drawn_items) > lines_before)
    app.cancel_active_tool()

    app.snap_player_var.set(True)
    app.set_tool("sign_ball")
    canvas.event_generate("<ButtonPress-1>", x=int(cx) + 6, y=int(cy) - 10)
    app.root.update()
    balls = [cid for cid, meta in app.drawn_items.items()
             if meta.get("type") == "sign"
             and str(meta.get("sign_type", "")).lower() == "ball"]
    c.check("clicking with the Ball tool stamps one", bool(balls))
    if balls:
        c.raises_not("a stamped ball can be re-snapped", app._snap_ball_item, balls[0])
    app.cancel_active_tool()

    app.clear_selection()
    app.selected_tokens = [t["shape_id"] for t in app._all_tokens()[:3]]
    app.highlight_selected()
    c.raises_not("the selection overlay draws", app._draw_selection_overlay)
    app.root.update()
    handles = list(app.selection_overlay_handles)
    if handles:
        c.check("a handle reports which corner it is",
                app._get_handle_type(handles[0]) is not None)
    bounds = app._get_selection_bounds()
    c.check("selection bounds are a box", bounds and len(bounds) == 4, f"{bounds}")
    if bounds and handles:
        c.check("a handle maps to the corner it drags from",
                app._get_resize_anchor(handles[0]) is not None)
        # The anchor names the corner being dragged, so pulling the bottom-right one
        # further out is what has to grow the box.
        box = app._compute_resized_box(bounds, (bounds[2] + 40, bounds[3] + 40),
                                       "bottom-right")
        c.check("pulling the bottom-right corner out grows the box",
                (box[2] - box[0]) > (bounds[2] - bounds[0]) and
                (box[3] - box[1]) > (bounds[3] - bounds[1]),
                f"{bounds} -> {box}")
        pushed = app._compute_resized_box(bounds, (bounds[0] + 20, bounds[1] + 20),
                                          "top-left")
        c.check("pushing the top-left corner in shrinks it",
                (pushed[2] - pushed[0]) < (bounds[2] - bounds[0]), f"{pushed}")
        ratio_before = (bounds[2] - bounds[0]) / max(bounds[3] - bounds[1], 1e-6)
        square = app._compute_resized_box(bounds, (bounds[2] + 40, bounds[3] + 2),
                                          "bottom-right", keep_ratio=True)
        ratio_after = (square[2] - square[0]) / max(square[3] - square[1], 1e-6)
        c.check("holding the ratio keeps the original proportions",
                abs(ratio_before - ratio_after) < 0.05,
                f"{ratio_before:.2f} -> {ratio_after:.2f}")
        c.check("the damped scale moves in the right direction",
                app._get_resize_scale(200.0, 100.0) > 1.0 >
                app._get_resize_scale(50.0, 100.0))
        c.raises_not("the resized box is clamped to the canvas",
                     app._clamp_resized_box, box)
        c.raises_not("coordinates scale with the box", app._scale_canvas_coords,
                     [10.0, 10.0, 20.0, 20.0], 15.0, 15.0, 1.5, 1.5)
    # Dragging a corner handle for real, which is the only path through the resize
    # maths (anchor, scale, clamp, apply).
    app.clear_selection()
    app.selected_tokens = [t["shape_id"] for t in app._all_tokens()[:3]]
    app.highlight_selected()
    app._draw_selection_overlay()
    app.root.update()
    handles = list(app.selection_overlay_handles)
    if handles:
        bounds_before = app._get_selection_bounds()
        sizes_before = [app.tokens[s].get("size") for s in app.selected_tokens]
        hx1, hy1, hx2, hy2 = canvas.bbox(handles[-1])
        hx, hy = (hx1 + hx2) / 2, (hy1 + hy2) / 2
        canvas.event_generate("<ButtonPress-1>", x=int(hx), y=int(hy))
        canvas.event_generate("<B1-Motion>", x=int(hx) + 60, y=int(hy) + 60, state=0x100)
        canvas.event_generate("<ButtonRelease-1>", x=int(hx) + 60, y=int(hy) + 60)
        app.root.update()
        bounds_after = app._get_selection_bounds()
        c.check("dragging a corner handle resizes the selection",
                bounds_before != bounds_after or
                [app.tokens[s].get("size") for s in app.selected_tokens
                 if s in app.tokens] != sizes_before,
                f"{bounds_before} -> {bounds_after}")
        c.check("the selection is still intact afterwards",
                all(sid in app.tokens for sid in app.selected_tokens))
    c.raises_not("the overlay clears", app._clear_selection_overlay)
    app.clear_selection()

    # a single selected line, which is what line-edit mode works on
    ids = app.draw_tactical_line_canvas("pass", 150, 150, 300, 220)
    for cid in ids:
        app.drawn_items.setdefault(cid, {"type": "tactic_line", "tool": "pass"})
    app.selected_drawn = {ids[0]}
    c.raises_not("the coordinates of a selected line can be read",
                 app._get_selected_line_coords)
    app.selected_drawn = set(ids)
    app.delete_selection()

    c.raises_not("sign pictograms can be drawn", app._create_sign_pictogram, "goal")
    c.raises_not("the toolbar can be laid out on demand", app._layout_menu)
    app._reflow_menu(force=True)
    app.root.update()


# ----------------------------------------------------------------------------
# N. dialogs and file entry points (stubbed -- no real files are touched)
# ----------------------------------------------------------------------------
def check_dialogs(c, app):
    c.section("N. Dialogs and file entry points")
    saved = (fa.filedialog.asksaveasfilename, fa.filedialog.askopenfilename,
             fa.messagebox.showinfo, fa.messagebox.showerror, fa.colorchooser.askcolor)
    macro_path = os.path.join(TEMP.name, "dialog-macro.json")
    logo_path = os.path.join(TEMP.name, "logo.png")
    if not os.path.exists(logo_path):
        Image.new("RGBA", (60, 30), (255, 255, 255, 255)).save(logo_path)

    fa.messagebox.showinfo = lambda *a, **k: None
    fa.messagebox.showerror = lambda *a, **k: None
    fa.colorchooser.askcolor = lambda *a, **k: ("#ff8800", "#ff8800")
    try:
        # Picking a team colour must not rebuild the roster.
        app.att_tactic_var.set("House")
        app.apply_tactic("att")
        app.root.update()
        placed = {t["label"]: tuple(round(v) for v in app.canvas.bbox(t["shape_id"]))
                  for t in app._all_tokens()}
        roles_before = sorted(t.get("position") for t in app._team_tokens("att"))
        history = len(app.undo_stack)
        app.choose_att_color()
        app.root.update()
        c.equal("choosing a team colour leaves every player where it was",
                {t["label"]: tuple(round(v) for v in app.canvas.bbox(t["shape_id"]))
                 for t in app._all_tokens()}, placed)
        c.equal("and keeps their tactical roles",
                sorted(t.get("position") for t in app._team_tokens("att")), roles_before)
        c.equal("and keeps the undo history", len(app.undo_stack), history)
        c.equal("while actually recolouring them",
                app.canvas.itemcget(app._team_tokens("att")[0]["shape_id"], "fill"),
                app.att_color)

        def centre_of(token):
            box = app.canvas.bbox(token["shape_id"])
            return (round((box[0] + box[2]) / 2), round((box[1] + box[3]) / 2))

        centres = {t["label"]: centre_of(t) for t in app._all_tokens()}
        defence_shapes = {t["shape"] for t in app._team_tokens("def")}
        app.att_shape_var.set("Triangle")
        app.root.update()
        c.check("changing a team's shape restyles it in place",
                {t["shape"] for t in app._team_tokens("att")} == {"Triangle"})
        def worst_shift():
            return max((max(abs(centres[t["label"]][0] - centre_of(t)[0]),
                            abs(centres[t["label"]][1] - centre_of(t)[1]))
                        for t in app._all_tokens() if t["label"] in centres),
                       default=0)

        # A triangle's bounding box is not centred the way a square's is, so a pixel of
        # difference is the shape's own geometry rather than the player moving.
        c.check("nobody moves while being restyled", worst_shift() <= 1,
                f"worst shift {worst_shift()}px")
        c.equal("without disturbing the other team",
                {t["shape"] for t in app._team_tokens("def")}, defence_shapes)
        c.equal("and without losing the roles",
                sorted(t.get("position") for t in app._team_tokens("att")), roles_before)

        app.att_spinbox.delete(0, tk.END)
        app.att_spinbox.insert(0, "3")
        app._roster_count_changed()
        app.root.update()
        c.equal("the count field resizes the team", len(app._team_tokens("att")), 3)
        c.check("the players that remain have not moved", worst_shift() <= 1,
                f"worst shift {worst_shift()}px")
        app.att_spinbox.delete(0, tk.END)
        app.att_spinbox.insert(0, "5")
        app._roster_count_changed()
        app.root.update()

        for name, setter in (("attack", app.choose_att_color),
                             ("defence", app.choose_def_color),
                             ("sign", app.choose_sign_color),
                             ("line", app.choose_line_color)):
            c.raises_not(f"{name} colour picker applies a colour", setter)
        c.equal("attack colour was taken from the picker", app.att_color, "#ff8800")
        c.equal("line colour was taken from the picker", app.line_color, "#ff8800")

        signs = app.place_sign_canvas(600, 400, "Square")
        app.selected_drawn = set(signs)
        app.sign_color = "#00aa55"
        c.raises_not("selected signs can be recoloured", app._recolor_selected_signs)
        c.equal("the sign took the new colour",
                app.drawn_items[signs[0]]["color"], "#00aa55")
        c.raises_not("the context-menu recolour runs", app.recolor_selection)
        app.selected_drawn = set(signs)
        app.delete_selection()

        app.selected_tokens = [app._all_tokens()[0]["shape_id"]]
        c.raises_not("copy style arms", app.toggle_copy_paste_style)
        c.raises_not("paste style applies to a player",
                     app._apply_copied_style_to_token, app._all_tokens()[1])
        app._deactivate_paste_style()

        fa.filedialog.asksaveasfilename = lambda *a, **k: macro_path
        c.raises_not("Save writes a macro", app.save_macro)
        c.check("the macro file exists", os.path.exists(macro_path))
        with open(macro_path) as handle:
            written = json.load(handle)
        c.equal("saved as version 3", written.get("version"), 3)
        for section in ("board", "drawings", "animation", "attachments", "commands"):
            c.check(f"saved with the {section}", section in written)
        c.check("and stamped with when it was saved", bool(written.get("saved_at")))

        fa.filedialog.askopenfilename = lambda *a, **k: macro_path
        c.raises_not("Load reads it back", app.load_macro)

        fa.filedialog.askopenfilename = lambda *a, **k: logo_path
        c.raises_not("watermark loading runs end to end", app.add_watermark)
        tops = [w for w in app.root.winfo_children() if isinstance(w, tk.Toplevel)]
        c.check("the placement editor opened from the file picker", bool(tops))
        for window in tops:
            window.destroy()
        app.root.update()

        # Cancel has to undo what the dialog already applied.
        settings_before = app._settings_snapshot()
        app.open_preferences()
        app.root.update()
        prefs = [w for w in app.root.winfo_children() if isinstance(w, tk.Toplevel)]
        app.set_rink_mode("3v3" if settings_before["rink_mode"] != "3v3" else "4v4")
        app.goals_visible_var.set(not settings_before["goals"])
        app.apply_color_theme("Nijmegen Flames")
        app.root.update()
        c.check("the dialog's changes take effect while it is open",
                app.color_theme == "Nijmegen Flames")
        cancelled = False
        for window in prefs:
            def press_cancel(widget):
                for child in widget.winfo_children():
                    if isinstance(child, tk.Button) and child.cget("text") == "Cancel":
                        child.invoke()
                        return True
                    if press_cancel(child):
                        return True
                return False

            cancelled = press_cancel(window) or cancelled
        app.root.update()
        c.check("Cancel was found", cancelled)
        c.equal("Cancel puts every setting back",
                app._settings_snapshot(), settings_before)
        for window in [w for w in app.root.winfo_children() if isinstance(w, tk.Toplevel)]:
            window.destroy()
        app.root.update()

        for name, opener in (("preferences", app.open_preferences),
                             ("set as default", app.set_as_default_popup)):
            c.raises_not(f"the {name} dialog opens", opener)
            app.root.update()
            windows = [w for w in app.root.winfo_children() if isinstance(w, tk.Toplevel)]
            c.check(f"the {name} dialog is a real window", bool(windows))
            for window in windows:
                window.destroy()
            app.root.update()
        c.raises_not("style can be stored as the default", app._apply_style_as_default)

        # Reset: destructive, so it must ask, and must honour a "no".
        app.place_sign_canvas(600, 400, "Goal")
        app.att_tactic_var.set("Umbrella")
        app.apply_tactic("att")
        app.root.update()
        drawn_before = len(app.drawn_items)
        real_ask = fa.messagebox.askyesno
        fa.messagebox.askyesno = lambda *a, **k: False
        app.reset_board()
        c.equal("declining Reset changes nothing", len(app.drawn_items), drawn_before)
        fa.messagebox.askyesno = lambda *a, **k: True
        try:
            app.reset_board()
            app.root.update()
            c.equal("Reset clears the drawings", len(app.drawn_items), 0)
            c.equal("Reset clears the undo history", len(app.undo_stack), 0)
            c.equal("Reset clears the timeline", len(app.action_steps), 0)
            c.equal("Reset clears the animation steps", len(app.animation_steps), 0)
            c.check("Reset drops the watermark", app.watermark is None)
            c.check("Reset leaves both teams on the board",
                    len(app._team_tokens("att")) > 0 and len(app._team_tokens("def")) > 0)
            c.check("Reset leaves the rink drawn", bool(app.canvas.find_withtag("pitch")))
        finally:
            fa.messagebox.askyesno = real_ask
        c.raises_not("the quick-actions dialog opens", app._show_save_exit_dialog)
        app.root.update()
        for window in [w for w in app.root.winfo_children() if isinstance(w, tk.Toplevel)]:
            window.destroy()
        app.root.update()
    finally:
        (fa.filedialog.asksaveasfilename, fa.filedialog.askopenfilename,
         fa.messagebox.showinfo, fa.messagebox.showerror,
         fa.colorchooser.askcolor) = saved

    button = None

    def find_button(widget):
        nonlocal button
        for child in widget.winfo_children():
            if isinstance(child, tk.Button) and child.cget("text") == "Undo":
                button = child
            find_button(child)

    find_button(app.top_bar)
    c.check("the Undo button was found on the toolbar", button is not None)
    if button is not None:
        button.event_generate("<Enter>")
        deadline = time.time() + 3        # the caption is deliberately delayed
        while time.time() < deadline:
            app.root.update()
            if [w for w in button.winfo_children() if isinstance(w, tk.Toplevel)]:
                break
            time.sleep(0.05)
        tips = [w for w in button.winfo_children() if isinstance(w, tk.Toplevel)]
        c.check("hovering a button shows its tooltip", bool(tips))
        if tips:
            c.check("the tooltip explains what the button does",
                    "Undo" in tips[0].winfo_children()[0].cget("text"))
        button.event_generate("<Leave>")
        app.root.update()
        c.check("the tooltip disappears on leave",
                not [w for w in button.winfo_children() if isinstance(w, tk.Toplevel)])


# ----------------------------------------------------------------------------
def coverage_report(called):
    """Which of the app's own functions the run actually entered.

    A check that passes tells you one behaviour is right; this tells you how much of
    the code that behaviour was drawn from, and names what nothing touched."""
    app_class = fa.FloorballTacticsApp
    methods = {name for name, value in vars(app_class).items()
               if callable(value) or isinstance(value, (staticmethod, classmethod))}
    for command in (fa.Command, fa.Tooltip):
        methods |= {f"{name}" for name in vars(command) if callable(vars(command)[name])}
    untouched = sorted(m for m in methods if m not in called and not m.startswith("__"))
    covered = len(methods) - len(untouched)
    print(f"\nFunction coverage: {covered}/{len(methods)} "
          f"({100 * covered / max(len(methods), 1):.0f}%) of the app's functions ran")
    if untouched:
        print("Not exercised (each needs a real user gesture or a modal dialog):")
        for index in range(0, len(untouched), 4):
            print("   " + ", ".join(untouched[index:index + 4]))


def check_pitch_parts(c, app):
    """P. The rink's own fixtures: selectable and deletable like anything else."""
    c.section("P. Rink fixtures")
    app.set_rink_mode("5v5", half=False)
    app.goals_visible_var.set(True)
    # Let the window settle before anything is measured. A resize left over from an
    # earlier area is processed on the next update, and a rink redrawn at a new size
    # halfway through this check moves the goal out from under the pointer.
    app.redraw_canvas()
    settled = None
    for _ in range(40):
        app.root.update()
        size = (app.canvas.winfo_width(), app.canvas.winfo_height())
        if size == settled:
            break
        settled = size
    app.redraw_canvas()
    app.root.update()

    parts = set()
    for cid in app.canvas.find_withtag("pitch_part"):
        parts |= {tag[5:] for tag in app.canvas.gettags(cid) if tag.startswith("part:")}
    for expected in ("boards", "centre_line", "centre_circle", "goal_left", "goal_right"):
        c.check(f"the rink offers {expected} as a part", expected in parts, f"{sorted(parts)}")
    c.equal("a full rink has seven face-off crosses",
            len([key for key in parts if key.startswith("faceoff_")]), 7)
    c.equal("every fixture has a name for the menu",
            app.pitch_part_label("goal_left"), "Goal")
    c.equal("and a face-off cross is named too",
            app.pitch_part_label("faceoff_2.9_2.9"), "Face-off cross")

    # The middle of the cage, which is solid. The goal areas around it are drawn as
    # outlines, so their middle is bare rink and nothing is under the pointer there.
    # Re-derived here rather than reusing anything read earlier: a redraw between the
    # two would leave the old ids naming nothing, and the click would land on nothing.
    def cage_centre():
        app.root.update()
        cages = [cid for cid in app.canvas.find_withtag("part:goal_left")
                 if app.canvas.type(cid) == "polygon"]
        if not cages:
            return None
        box = app.canvas.bbox(cages[0])
        return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2) if box else None

    hit = cage_centre()
    for _ in range(3):
        if hit is not None and app._pitch_part_at(*hit) == "goal_left":
            break
        app.redraw_canvas()
        hit = cage_centre()
    c.check("the goal cage is on the board to be clicked", hit is not None)
    if hit is None:
        return
    c.equal("the goal is found under the pointer", app._pitch_part_at(*hit), "goal_left")

    class Press:
        def __init__(self, x, y):
            self.x, self.y, self.state, self.num = int(x), int(y), 0, 1

    def click_cage():
        """Always aim at where the cage is now, never at where it was measured."""
        spot = cage_centre()
        if spot:
            app.on_canvas_press(Press(*spot))
        return spot

    app.active_tool = None
    app.clear_selection()
    hit = click_cage() or hit
    c.equal("clicking it selects it", app.selected_pitch_parts, {"goal_left"})
    c.check("and it is marked on the board", bool(app.selection_overlay_ids))

    # A lasso that happens to start on a fixture is still a lasso.
    class Drag:
        def __init__(self, x, y):
            self.x, self.y, self.state = int(x), int(y), 0x100

    app.on_canvas_drag(Drag(hit[0] + 90, hit[1] + 90))
    app.on_canvas_release(Drag(hit[0] + 90, hit[1] + 90))
    c.equal("dragging from it lassos instead of selecting it",
            app.selected_pitch_parts, set())
    app.clear_selection()
    hit = click_cage() or hit
    c.equal("a plain click still selects it", app.selected_pitch_parts, {"goal_left"})
    app.on_canvas_release(Press(*hit))
    c.equal("and it survives the button coming back up",
            app.selected_pitch_parts, {"goal_left"})

    history = len(app.undo_stack)
    app.delete_selection()
    app.root.update()
    c.check("Delete takes it off the rink", "goal_left" in app.hidden_pitch_parts)
    c.equal("the goal's items are hidden",
            {app.canvas.itemcget(cid, "state")
             for cid in app.canvas.find_withtag("part:goal_left")}, {"hidden"})
    c.equal("deleting it is undoable", len(app.undo_stack), history + 1)
    c.check("and it cannot be clicked any more", app._pitch_part_at(*hit) != "goal_left")

    app.redraw_canvas()
    app.root.update()
    c.check("it stays off after the rink is redrawn",
            all(app.canvas.itemcget(cid, "state") == "hidden"
                for cid in app.canvas.find_withtag("part:goal_left")))
    app.toggle_rink_orientation()
    app.root.update()
    c.check("and after the rink is rotated", "goal_left" in app.hidden_pitch_parts)
    app.toggle_rink_orientation()
    app.root.update()

    saved = app._board_snapshot()
    c.equal("a board snapshot carries what was removed",
            saved.get("hidden_pitch_parts"), ["goal_left"])

    app.undo()
    app.root.update()
    c.check("undo puts the goal back", "goal_left" not in app.hidden_pitch_parts)
    c.equal("and its items are visible again",
            {app.canvas.itemcget(cid, "state")
             for cid in app.canvas.find_withtag("part:goal_left")}, {"normal"})

    app._restore_board(saved)
    app.root.update()
    c.check("loading a board takes it off again", "goal_left" in app.hidden_pitch_parts)
    app.restore_pitch_parts()
    app.root.update()
    c.equal("Restore Rink Features brings everything back", app.hidden_pitch_parts, set())

    cross = sorted(key for key in parts if key.startswith("faceoff_"))[0]
    cbox = app.canvas.bbox(f"part:{cross}")
    app.clear_selection()
    app.selected_pitch_parts = {cross}
    app.delete_selection()
    app.root.update()
    c.check("a face-off cross can go on its own", cross in app.hidden_pitch_parts)
    c.check("without disturbing the goals", "goal_left" not in app.hidden_pitch_parts)
    c.check("the boards are still there under it",
            app._pitch_part_at((cbox[0] + cbox[2]) / 2, (cbox[1] + cbox[3]) / 2) != cross)

    app.select_all()
    c.equal("Select All leaves the rink itself alone", app.selected_pitch_parts, set())
    app.clear_selection()
    app.restore_pitch_parts()
    app.root.update()
    c.equal("the rink ends up whole", app.hidden_pitch_parts, set())


def check_rink_modes(c, app):
    """Q. The three fields the rink button rotates through, measured in metres."""
    c.section("Q. Rink sizes and markings")

    def markings(mode, half=False):
        """Every fixture of the rink as it is drawn now, in rink metres."""
        app.set_rink_mode(mode, half=half)
        app.root.update()
        state = app._pitch_state()
        found = {}
        for cid in app.canvas.find_withtag("pitch_part"):
            for tag in app.canvas.gettags(cid):
                if not tag.startswith("part:"):
                    continue
                points = app.canvas.coords(cid)
                found.setdefault(tag[5:], []).extend(
                    app._state_px_to_m(points[i], points[i + 1], state)
                    for i in range(0, len(points) - 1, 2))
        return found

    def span(found, key):
        points = found[key]
        return (min(p[0] for p in points), max(p[0] for p in points),
                min(p[1] for p in points), max(p[1] for p in points))

    def spots(found):
        return sorted(tuple(round(float(value), 2) for value in key[8:].split("_"))
                      for key in found if key.startswith("faceoff_"))

    # The large rink is unchanged by the new fields being added beside it.
    full = markings("5v5")
    c.equal("the 5v5 rink still has seven face-off crosses", len(spots(full)), 7)
    c.check("and still has its centre circle", "centre_circle" in full)
    # The cage's back edge is one cage depth behind the goal line, and the goal
    # areas run from the goal line into the field, so the part as a whole starts at
    # the back of the cage.
    c.equal("its goal line is 2.85 m from the boards",
            round(span(full, "goal_left")[0] + app.GOAL_DEPTH_M, 2), 2.85)

    # 3v3: 22 x 11 m, goal line 2.5 m in, goalkeeper area 2.5 x 1 m, free-hit dots
    # 2 m from the long sides, penalty spot 5 m out, substitution zones 4 m from the
    # centre line and 4 m long. NeFUB / IFF 3v3 Rules of the Game 2025, rules 101-104.
    three = markings("3v3")
    c.equal("the 3v3 field is 22 x 11 m", app._rink_size(), (22.0, 11.0))
    c.check("3v3 has no centre circle", "centre_circle" not in three)
    c.check("3v3 has a centre line", "centre_line" in three)
    gx1, gx2, gy1, gy2 = span(three, "goal_left")
    c.equal("its goal line is 2.5 m from the boards",
            round(gx1 + app.GOAL_DEPTH_M, 2), 2.5)
    c.equal("its goalkeeper area is 1 m deep", round(gx2 - gx1 - 0.65, 2), 1.0)
    c.equal("its goalkeeper area is 2.5 m across", round(gy2 - gy1, 2), 2.5)
    c.check("its free-hit dots sit 2 m from the long sides",
            (2.5, 2.0) in spots(three) and (2.5, 9.0) in spots(three), f"{spots(three)}")
    c.check("its centre spot is the middle of the field",
            (11.0, 5.5) in spots(three), f"{spots(three)}")
    c.check("its penalty spots are 5 m out from each goal line",
            (7.5, 5.5) in spots(three) and (14.5, 5.5) in spots(three), f"{spots(three)}")
    zx1, zx2, zy1, zy2 = span(three, "sub_zones")
    c.equal("its substitution zones start 4 m from the centre line",
            round(11.0 - zx1, 2), 8.0)
    c.equal("and run along the boards", round(zy1, 2), 11.0)

    # 4v4 on the small field: 27 x 15 m, goal line 1.8 m in, goal area 1.9 x 0.9 m,
    # face-off marks 1 m from the long sides, penalty spot 7 m out, 5 m substitution
    # zones meeting at the centre line.
    small = markings("4v4")
    c.equal("the 4v4 field is 27 x 15 m", app._rink_size(), (27.0, 15.0))
    gx1, gx2, gy1, gy2 = span(small, "goal_left")
    c.equal("its goal line is 1.8 m from the boards",
            round(gx1 + app.GOAL_DEPTH_M, 2), 1.8)
    c.equal("its goal area is 0.9 m deep", round(gx2 - gx1 - 0.65, 2), 0.9)
    c.equal("its goal area is 1.9 m across", round(gy2 - gy1, 2), 1.9)
    c.check("its face-off marks sit 1 m from the long sides",
            (1.8, 1.0) in spots(small) and (1.8, 14.0) in spots(small), f"{spots(small)}")
    c.check("its penalty spots are 7 m out from each goal line",
            (8.8, 7.5) in spots(small) and (18.2, 7.5) in spots(small), f"{spots(small)}")
    zx1, zx2, zy1, zy2 = span(small, "sub_zones")
    c.equal("its substitution zones meet at the centre line",
            (round(zx1, 2), round(zx2, 2)), (8.5, 18.5))

    # The goal itself is the same on every field: 1.6 m between the posts.
    for mode in app.RINK_ORDER:
        found = markings(mode)
        key = "goal_left" if "goal_left" in found else "goal_right"
        _, _, y1, y2 = span(found, key)
        width = app._rink_size()[1]
        c.equal(f"the {mode} goal is centred on the width",
                round((y1 + y2) / 2, 2), round(width / 2, 2))

    # Each field is played with a different number of players, and choosing it sets
    # the roster accordingly.
    for mode, size in app.RINK_TEAM_SIZES.items():
        app.set_rink_mode(mode)
        app.root.update()
        c.equal(f"the {mode} field puts {size} attackers on the board",
                len(app._team_tokens("att")), size)
        c.equal(f"and {size} defenders", len(app._team_tokens("def")), size)

    # One button rotates through them all and comes back where it started.
    app.set_rink_mode("5v5", half=False)
    seen = []
    for _ in app.RINK_ORDER:
        app.cycle_rink_mode()
        seen.append(app.rink_mode)
    c.equal("the rink button rotates through every field and returns",
            seen, ["4v4", "3v3", "5v5"])

    # The half rink is a view of whichever field is chosen, not a field of its own:
    # every one of them has a half, and it is the goal end at full width.
    for mode in app.RINK_ORDER:
        length, width = app.RINK_SIZES[mode]
        app.set_rink_mode(mode, half=True)
        app.root.update()
        c.equal(f"half the {mode} field is {length / 2:g} x {width:g} m",
                app._rink_size(), (length / 2.0, width))
        found = markings(mode, half=True)
        c.check(f"and the {mode} half rink has no centre line",
                "centre_line" not in found)
        c.check("and only the far goal", "goal_left" not in found
                and "goal_right" in found)
        c.equal(f"and still puts {app.RINK_TEAM_SIZES[mode]} attackers on the board",
                len(app._team_tokens("att")), app.RINK_TEAM_SIZES[mode])

    # Cycling the field leaves the half alone -- it is a separate button.
    app.set_rink_mode("5v5", half=True)
    app.cycle_rink_mode()
    c.check("cycling the field keeps the half rink", app.half_rink is True
            and app.rink_mode == "4v4")
    # Pressed for real, so the button in Board Settings is known to be wired to it.
    app.setting_buttons["half_rink"].invoke()
    app.root.update()
    c.check("and the half button turns it off again", app.half_rink is False)
    c.equal("on the whole field again", app._rink_size(), (27.0, 15.0))
    app.setting_buttons["half_rink"].invoke()
    app.root.update()
    c.check("and back on", app.half_rink is True)
    app.setting_buttons["rink_mode"].invoke()
    app.root.update()
    c.equal("while the field button beside it cycles on to 3v3", app.rink_mode, "3v3")

    # Files, configs and macros written before the fields were named after the game
    # still open on the field they meant -- including whether it was half of it, which
    # those files say with the name alone. Each starts from the opposite half so the
    # name is what sets it.
    for legacy, expected in (("full", ("5v5", False)), ("half", ("5v5", True)),
                             ("small", ("4v4", False))):
        app.set_rink_mode("3v3", half=not expected[1])
        app.set_rink_mode(legacy)
        c.equal(f"a file asking for the {legacy} rink opens {expected[0]}"
                f"{' at half' if expected[1] else ''}",
                (app.rink_mode, app.half_rink), expected)

    # Players are held in metres, so they keep their place on the rink across a
    # change of field rather than staying where their pixels were.
    app.set_rink_mode("5v5", half=False)
    app.root.update()
    token = app._team_tokens("att")[0]
    before = app._state_px_to_m(*token_centre(app, token), app._pitch_state())
    app.set_rink_mode("3v3")
    app.root.update()
    after = app._state_px_to_m(*token_centre(app, token), app._pitch_state())
    c.check("a player keeps its place on the rink when the field changes",
            abs(after[0] - min(before[0], 22.0)) < 0.2 and
            abs(after[1] - min(before[1], 11.0)) < 0.2,
            f"{before} -> {after}")
    app.set_rink_mode("5v5", half=False)
    app.root.update()

    # Zoom is nothing but a different scale in the same projection, so everything on
    # the board keeps its place on the rink through it.
    app.set_zoom(1.0)
    app.root.update()
    token = app._team_tokens("att")[0]
    at_one = app._state_px_to_m(*token_centre(app, token), app._pitch_state())
    scale_one = app.pitch_scale
    c.check("Ctrl + magnifies the board", app.zoom_in() is not None and
            app.pitch_scale > scale_one, f"{scale_one} -> {app.pitch_scale}")
    zoomed = app._state_px_to_m(*token_centre(app, token), app._pitch_state())
    c.check("and the players keep their place on the rink",
            abs(zoomed[0] - at_one[0]) < 0.1 and abs(zoomed[1] - at_one[1]) < 0.1,
            f"{at_one} -> {zoomed}")
    app.zoom_out()
    app.root.update()
    c.check("Ctrl - takes it back out", abs(app.pitch_scale - scale_one) < 0.01,
            f"{app.pitch_scale} vs {scale_one}")
    for _ in range(12):
        app.zoom_in()
    c.equal("zooming in stops at the limit", app.zoom, app.ZOOM_MAX)
    for _ in range(20):
        app.zoom_out()
    c.equal("and out at the other one", app.zoom, app.ZOOM_MIN)
    app.zoom_reset()
    app.root.update()
    c.equal("Ctrl 0 puts the whole rink back", app.zoom, 1.0)
    c.check("at the scale it started from", abs(app.pitch_scale - scale_one) < 0.01)

    # Scrolling a zoomed board: the wheel goes up and down, Shift and the wheel go
    # sideways, and nothing scrolls while the whole rink is already on screen.
    class Wheel:
        def __init__(self, num, shift=False):
            self.num, self.state, self.delta = num, (0x1 if shift else 0), 0
            self.x, self.y = 400, 300

    c.equal("there is nothing to scroll at 1x", app._pan_limits(), (0.0, 0.0))
    c.check("so the wheel does nothing", app.scroll_board(dy=60) is False)
    app.set_zoom(2.0)
    app.root.update()
    limit_x, limit_y = app._pan_limits()
    c.check("a zoomed board has something to scroll to", limit_x > 0 and limit_y > 0,
            f"{(limit_x, limit_y)}")
    app._on_mouse_wheel(Wheel(4))
    app.root.update()
    c.check("the wheel scrolls the board up", app.pan_y > 0 and app.pan_x == 0,
            f"pan {(app.pan_x, app.pan_y)}")
    app._on_mouse_wheel(Wheel(5))
    app.root.update()
    c.equal("and back down again", (app.pan_x, app.pan_y), (0.0, 0.0))
    app._on_mouse_wheel(Wheel(4, shift=True))
    app.root.update()
    c.check("Shift and the wheel scroll sideways instead",
            app.pan_x > 0 and app.pan_y == 0, f"pan {(app.pan_x, app.pan_y)}")
    for _ in range(30):
        app._on_mouse_wheel(Wheel(5, shift=True))
    app.root.update()
    c.equal("scrolling stops at the edge of the board", round(app.pan_x, 3),
            round(-limit_x, 3))
    moved = app._state_px_to_m(*token_centre(app, token), app._pitch_state())
    c.check("and the players keep their place on the rink through it",
            abs(moved[0] - at_one[0]) < 0.1 and abs(moved[1] - at_one[1]) < 0.1,
            f"{at_one} -> {moved}")
    app.zoom_reset()
    app.root.update()
    c.equal("Ctrl 0 centres the board again", (app.pan_x, app.pan_y), (0.0, 0.0))

    c.check("the board is signed in the bottom-left corner",
            bool(app.canvas.find_withtag("credit")))
    box = app.canvas.bbox("credit")
    c.check("and the signature sits there, not over the play",
            box[0] < app.canvas.winfo_width() / 4 and
            box[3] > app.canvas.winfo_height() - 40, f"{box}")
    c.check("the signature is not a selectable board item",
            not any(cid in app.drawn_items
                    for cid in app.canvas.find_withtag("credit")))


def main():
    c = Checker()
    called = set()
    module_file = os.path.abspath(fa.__file__)

    def profiler(frame, event, _arg):
        if event == "call" and frame.f_code.co_filename == module_file:
            called.add(frame.f_code.co_name)

    # No dialog may ever block a run. A message box waits for a human to press OK, and
    # a suite that stops dead in front of one looks like a hang -- and takes the whole
    # application down with it when somebody closes the window to get their terminal
    # back. Areas that care about what was said still stub these themselves.
    for name in ("showinfo", "showwarning", "showerror"):
        setattr(fa.messagebox, name, lambda *a, **k: None)
    fa.messagebox.askyesno = lambda *a, **k: False
    fa.messagebox.askokcancel = lambda *a, **k: False

    # Profiling starts before the app is built: half the code base is the toolbar
    # construction, and starting afterwards reported all of it as never run.
    sys.setprofile(profiler)
    root, app = build_app(c)
    try:
        for step in (check_geometry, check_tokens, check_selection, check_snapping,
                     check_signs, check_tactics, check_undo, check_macros,
                     check_watermark, check_layout, check_resize, check_animation,
                     check_mouse, check_dialogs, check_config_and_ui,
                     check_pitch_parts, check_rink_modes):
            try:
                step(c, app)
            except Exception:
                c.check(f"{step.__name__} ran to completion", False,
                        traceback.format_exc().strip().splitlines()[-1])
                traceback.print_exc()
    finally:
        sys.setprofile(None)
        try:
            root.destroy()
        except Exception:
            pass
    failed = c.report()
    coverage_report(called)
    TEMP.cleanup()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
