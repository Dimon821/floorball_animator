# floorball_studio.py
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, colorchooser
from tkinter import font as tkfont
from PIL import Image, ImageTk, ImageDraw, ImageChops
import math
import os
import io
import base64
import datetime
import json
import pathlib
import time

# ==========================================
# COMMAND PATTERN ABSTRACTIONS
# ==========================================

class Command:
    def execute(self):
        pass
    def undo(self):
        pass
    def serialize(self):
        return {}

class MoveTokensCommand(Command):
    """A move, together with any ghosts the move left behind.

    Ghosts are dropped at the moment a drag begins, outside the command system, so
    undoing the move used to slide the player back and leave its ghost stranded where
    nothing had happened. Carrying them here makes one undo take back the whole
    gesture, and redo put it back."""

    def __init__(self, app, label_moves, ghosts=None, record_step=True,
                 keep_attached=True):
        self.app = app
        self.label_moves = label_moves
        self.ghosts = list(ghosts or [])
        # Whether lines snapped to these players travel with them. A hand drag decides
        # this with Shift; a replayed macro or a formation keeps the old behaviour.
        self.keep_attached = keep_attached
        # Composed commands (a tactic, for instance) log themselves and would otherwise
        # write a second, duplicate line.
        self.record_step = record_step
        self.step_desc = self._describe()
        self.animation_record = None

    def _describe(self):
        # The rink shows tactical roles (LD, RW, T) once a formation is applied, while
        # the internal labels stay A1..A5 / D1..D5. The timeline should read the way
        # the board does, so it uses whatever the token actually displays.
        labels = sorted(self.label_moves, key=self.app._display_label)
        if not labels:
            return "Move"
        shown = [self.app._display_label(label) for label in labels]
        text = ", ".join(shown[:3]) + (f" +{len(shown) - 3}" if len(shown) > 3 else "")
        return f"Move {text}"

    def record(self):
        """Called once when the move is pushed: put it in the timeline and make it a
        keyframe, so a sequence of drags builds an animation as it goes."""
        if not self.record_step or not self.label_moves:
            return
        self.app.action_steps.append(self.step_desc)
        try:
            self.app.steps_listbox.insert(tk.END, self.step_desc)
        except Exception:
            pass
        self.animation_record = self.app._record_move_as_animation_step(
            self.label_moves, name=self.step_desc)

    def _forget(self):
        if self.step_desc in self.app.action_steps:
            index = self.app.action_steps.index(self.step_desc)
            self.app.action_steps.pop(index)
            try:
                self.app.steps_listbox.delete(index)
            except Exception:
                pass
        self.app._unrecord_animation_step(self.animation_record)

    def _remember(self):
        if not self.record_step:
            return
        self.app.action_steps.append(self.step_desc)
        try:
            self.app.steps_listbox.insert(tk.END, self.step_desc)
        except Exception:
            pass
        self.app._rerecord_animation_step(self.animation_record)

    def execute(self):
        for label, (dx, dy) in self.label_moves.items():
            sid = self.app._get_sid_by_label(label)
            if sid:
                self._move(sid, dx, dy)
        self._restore_ghosts()
        self._remember()

    def undo(self):
        self._remove_ghosts()
        for label, (dx, dy) in self.label_moves.items():
            sid = self.app._get_sid_by_label(label)
            if sid:
                self._move(sid, -dx, -dy)
        self._forget()

    def _remove_ghosts(self):
        for spec in self.ghosts:
            for token in list(self.app.tokens.values()):
                if token.get("is_ghost") and token.get("label") == spec.get("label"):
                    self.app._delete_token(token)
                    break

    def _restore_ghosts(self):
        """Redo: put back exactly the ghosts this move produced."""
        for spec in self.ghosts:
            if any(t.get("is_ghost") and t.get("label") == spec.get("label")
                   for t in self.app.tokens.values()):
                continue
            self.app._spawn_ghost(spec)

    def _move(self, sid, dx, dy):
        token = self.app.tokens.get(sid)
        if token:
            for item in self.app._token_items(token):
                self.app.canvas.move(item, dx, dy)
            if "text_ids" in token:
                for tid in token["text_ids"]:
                    self.app.canvas.move(tid, dx, dy)
            if not self.keep_attached:
                return
            self.app.move_attached_line_ends(token, dx, dy)

    def serialize(self):
        return {"type": "move_tokens", "moves": self.label_moves}

class ApplyTacticCommand(Command):
    """Putting a team into a formation, recorded as a single step.

    A plain MoveTokensCommand would carry the movement but nothing about *why* the
    players moved, so the timeline stayed silent and a saved macro held only
    anonymous pixel deltas. This keeps the formation, the offensive percentage and
    each player's role together, which is what makes the step readable in the
    timeline and replayable from JSON."""

    def __init__(self, app, team, formation, percent, label_moves, positions):
        self.app = app
        self.team = team
        self.formation = formation
        self.percent = percent
        self.label_moves = label_moves          # internal label -> (dx, dy)
        self.positions = positions              # internal label -> role, e.g. "LD"
        self.previous_positions = {}
        self.animation_record = None
        # Composed rather than subclassed so the movement keeps following attached
        # tactic lines exactly as a hand-drag would.
        # record_step=False: this command writes its own, richer timeline line and
        # keyframe; the inner move must not add a second one.
        self.move = MoveTokensCommand(app, label_moves, record_step=False)
        roles = ", ".join(positions[label] for label in sorted(positions))
        side = "Attack" if team == "att" else "Defence"
        self.step_desc = f"{side} {formation} {int(percent)}% [{roles}]"

    def _tokens(self):
        for label in self.positions:
            sid = self.app._get_sid_by_label(label)
            token = self.app.tokens.get(sid) if sid else None
            if token:
                yield label, token

    def execute(self):
        self.move.execute()
        for label, token in self._tokens():
            self.previous_positions[label] = token.get("position")
            self.app._set_token_position(token, self.positions[label])
        self.app.action_steps.append(self.step_desc)
        try:
            self.app.steps_listbox.insert(tk.END, self.step_desc)
        except Exception:
            pass
        # On redo, put the keyframe back where it was.
        self.app._rerecord_animation_step(self.animation_record)

    def record(self):
        """A formation change is a move of the whole team, so it joins the step being
        built like any other action -- the timeline line itself is written in
        execute()."""
        self.animation_record = self.app._record_move_as_animation_step(
            self.label_moves, name=self.step_desc)

    def undo(self):
        self.move.undo()
        for label, token in self._tokens():
            self.app._set_token_position(token, self.previous_positions.get(label))
        if self.step_desc in self.app.action_steps:
            index = self.app.action_steps.index(self.step_desc)
            self.app.action_steps.pop(index)
            try:
                self.app.steps_listbox.delete(index)
            except Exception:
                pass
        self.app._unrecord_animation_step(self.animation_record)

    def serialize(self):
        return {
            "type": "tactic",
            "team": self.team,
            "formation": self.formation,
            "percent": self.percent,
            "moves": self.label_moves,
            "positions": self.positions,
        }

class RotateDrawnCommand(Command):
    """Turn drawn items -- signs, lines, boxes -- about the centre of the selection.

    Polygons and lines are rotated vertex by vertex. Ovals, arcs, text and images have
    no rotatable geometry in Tk, so those are carried round the pivot instead of being
    spun, which keeps a mixed selection together."""

    def __init__(self, app, ids, degrees):
        self.app = app
        self.ids = list(ids)
        self.degrees = degrees
        # The pivot is recorded when the rotation runs and reused to undo it. A
        # rotated shape has a different bounding box, so recomputing the centre on the
        # way back turns about a different point and the item never lands where it
        # started -- the same trap that broke undo for players.
        self.pivot = None

    def _pivot(self):
        xs, ys = [], []
        for cid in self.ids:
            box = self.app.canvas.bbox(cid)
            if box:
                xs.extend((box[0], box[2]))
                ys.extend((box[1], box[3]))
        if not xs:
            return None
        return (sum(xs) / len(xs), sum(ys) / len(ys))

    def _turn(self, degrees, pivot=None):
        pivot = pivot or self._pivot()
        if pivot is None:
            return
        px, py = pivot
        radians = math.radians(degrees)
        cos_a, sin_a = math.cos(radians), math.sin(radians)

        def spin(x, y):
            dx, dy = x - px, y - py
            return px + dx * cos_a - dy * sin_a, py + dx * sin_a + dy * cos_a

        for cid in self.ids:
            try:
                kind = self.app.canvas.type(cid)
            except Exception:
                continue
            coords = self.app.canvas.coords(cid)
            if not coords:
                continue
            if kind in ("polygon", "line"):
                turned = []
                for i in range(0, len(coords) - 1, 2):
                    nx, ny = spin(coords[i], coords[i + 1])
                    turned.extend((nx, ny))
                self.app.canvas.coords(cid, *turned)
            else:
                box = self.app.canvas.bbox(cid)
                if not box:
                    continue
                cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
                nx, ny = spin(cx, cy)
                self.app.canvas.move(cid, nx - cx, ny - cy)

    def execute(self):
        # Recomputed on every execute, so a redo after the items have been moved
        # elsewhere still turns them about their own centre.
        self.pivot = self._pivot()
        self._turn(self.degrees, self.pivot)

    def undo(self):
        self._turn(-self.degrees, self.pivot)


class MoveDrawnCommand(Command):
    def __init__(self, app, id_moves):
        self.app = app
        self.id_moves = id_moves  # mapping canvas_id -> (dx,dy)

    def execute(self):
        for cid, (dx, dy) in self.id_moves.items():
            coords = self.app.canvas.coords(cid)
            if not coords:
                continue
            new_coords = []
            for i, v in enumerate(coords):
                if i % 2 == 0:
                    new_coords.append(v + dx)
                else:
                    new_coords.append(v + dy)
            self.app.canvas.coords(cid, *new_coords)

    def undo(self):
        for cid, (dx, dy) in self.id_moves.items():
            coords = self.app.canvas.coords(cid)
            if not coords:
                continue
            new_coords = []
            for i, v in enumerate(coords):
                if i % 2 == 0:
                    new_coords.append(v - dx)
                else:
                    new_coords.append(v - dy)
            self.app.canvas.coords(cid, *new_coords)

    def serialize(self):
        return {"type": "move_drawn", "moves": self.id_moves}

class Tooltip:
    """A small caption that appears under a widget on hover.

    Tk has no tooltip of its own, so this is a borderless Toplevel shown after a short
    delay and destroyed on leave. The delay stops captions flickering up while the
    pointer is only crossing the toolbar on its way somewhere else."""

    DELAY_MS = 450
    WRAP_PX = 260

    def __init__(self, widget, text, font=("TkDefaultFont", 8)):
        self.widget = widget
        self.text = text
        self.font = font
        self.window = None
        self.after_id = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self.after_id = self.widget.after(self.DELAY_MS, self._show)

    def _cancel(self):
        if self.after_id is not None:
            try:
                self.widget.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None

    def _show(self):
        # The text may be a callable when the caption depends on state that changes
        # after the toolbar is built -- the rink button relabels itself.
        text = self.text() if callable(self.text) else self.text
        if self.window is not None or not text:
            return
        try:
            x = self.widget.winfo_rootx() + 8
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        except Exception:
            return
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.wm_geometry(f"+{x}+{y}")
        tk.Label(self.window, text=text, justify=tk.LEFT, font=self.font,
                 bg="#212529", fg="#f8f9fa", relief=tk.FLAT, bd=0,
                 padx=8, pady=5, wraplength=self.WRAP_PX).pack()

    def _hide(self, _event=None):
        self._cancel()
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass
            self.window = None


class SetWatermarkCommand(Command):
    """Loading a watermark is an action like any other: it shows up in the timeline,
    it undoes, and it is written into the macro's command list.

    The command carries the serialised watermark (image included), so replaying the
    macro reproduces the logo without needing the file it came from."""

    def __init__(self, app, data, previous=None):
        self.app = app
        self.data = data
        self.previous = previous
        name = os.path.basename((data or {}).get("path") or "") or "image"
        self.step_desc = f"Watermark {name}" if data else "Watermark removed"

    def execute(self):
        self.app._restore_watermark(self.data)
        self.app.action_steps.append(self.step_desc)
        try:
            self.app.steps_listbox.insert(tk.END, self.step_desc)
        except Exception:
            pass

    def undo(self):
        self.app._restore_watermark(self.previous)
        if self.step_desc in self.app.action_steps:
            index = self.app.action_steps.index(self.step_desc)
            self.app.action_steps.pop(index)
            try:
                self.app.steps_listbox.delete(index)
            except Exception:
                pass

    def serialize(self):
        return {"type": "watermark", "watermark": self.data}


class DrawLineCommand(Command):
    def __init__(self, app, tool, x1, y1, x2, y2, extra_data=None):
        self.app = app
        self.tool = tool
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2
        self.extra_data = extra_data or {}
        self.line_ids = []
        self.animation_record = None
        self.step_desc = tool.capitalize()
        self.drawing_data = {"tool": self.tool, "x1": self.x1, "y1": self.y1, "x2": self.x2, "y2": self.y2, "extra": self.extra_data}

    def execute(self):
        self.line_ids = self.app.draw_tactical_line_canvas(self.tool, self.x1, self.y1, self.x2, self.y2, preview=False, extra_data=self.extra_data)
        # register drawn items in app.drawn_items
        for lid in self.line_ids:
            # Through _register_drawn_item, so a box or circle records that its colour
            # lives in outline= alone. Written straight into drawn_items it would fall
            # back to fill= and turn solid the first time anything was selected. The
            # group tag the drawing was given is carried over, not dropped.
            existing = self.app.drawn_items.get(lid) or {}
            self.app._register_drawn_item(lid, {"type": "tactic_line", "tool": self.tool,
                                                "group": existing.get("group"),
                                                "data": self.drawing_data,
                                                "color": self.app.line_color})
        self.app.drawings.append((self.line_ids, self.drawing_data))
        self.app.action_steps.append(self.step_desc)
        try:
            self.app.steps_listbox.insert(tk.END, self.step_desc)
        except Exception:
            pass
        # On redo, put the group entry back too.
        self.app._rerecord_animation_step(self.animation_record)

    def record(self):
        """An arrow belongs to the group it was drawn in, alongside the moves."""
        self.animation_record = self.app.record_action_in_group(self.step_desc)
        self.app.tag_items_with_group(self.line_ids, self.step_desc)

    def undo(self):
        for pid in list(self.line_ids):
            try:
                self.app.canvas.delete(pid)
            except Exception:
                pass
            if pid in self.app.drawn_items:
                del self.app.drawn_items[pid]
        
        self.app.drawings = [d for d in self.app.drawings if d[0] != self.line_ids]
        
        if self.step_desc in self.app.action_steps:
            idx = self.app.action_steps.index(self.step_desc)
            self.app.action_steps.pop(idx)
            try:
                self.app.steps_listbox.delete(idx)
            except Exception:
                pass
        self.app._unrecord_animation_step(self.animation_record)

    def serialize(self):
        return {"type": "draw", "tool": self.tool, "x1": self.x1, "y1": self.y1, "x2": self.x2, "y2": self.y2, "extra": self.extra_data}

class RotateTokensCommand(Command):
    def __init__(self, app, labels, degrees):
        self.app = app
        self.labels = list(labels)
        self.degrees = degrees

    def execute(self):
        self._turn(self.degrees)

    def undo(self):
        self._turn(-self.degrees)

    def _turn(self, degrees):
        for label in self.labels:
            sid = self.app._get_sid_by_label(label)
            token = self.app.tokens.get(sid) if sid else None
            if token:
                self.app._rotate_token(token, degrees)

    def serialize(self):
        return {"type": "rotate_tokens", "labels": self.labels, "degrees": self.degrees}

class GroupCommand(Command):
    def __init__(self, app, labels, is_ungroup=False):
        self.app = app
        self.labels = set(labels)
        self.is_ungroup = is_ungroup
        self.affected_group = set()

    def execute(self):
        sids = {self.app._get_sid_by_label(l) for l in self.labels if self.app._get_sid_by_label(l)}
        sids = {s for s in sids if s}
        if not sids: return
        self.affected_group = sids

        if self.is_ungroup:
            self.app.groups = [g for g in self.app.groups if not g.intersection(self.affected_group)]
        else:
            self.app.groups = [g for g in self.app.groups if not g.intersection(self.affected_group)]
            self.app.groups.append(self.affected_group)

    def undo(self):
        if self.is_ungroup:
            self.app.groups = [g for g in self.app.groups if not g.intersection(self.affected_group)]
            self.app.groups.append(self.affected_group)
        else:
            self.app.groups = [g for g in self.app.groups if g != self.affected_group]

    def serialize(self):
        return {"type": "group", "labels": list(self.labels), "is_ungroup": self.is_ungroup}

class LockCommand(Command):
    def __init__(self, app, labels, lock_state):
        self.app = app
        self.labels = labels
        self.lock_state = lock_state
        self.previous_states = {}

    def execute(self):
        for l in self.labels:
            sid = self.app._get_sid_by_label(l)
            if sid and sid in self.app.tokens:
                self.previous_states[sid] = self.app.tokens[sid]["locked"]
                self.app.tokens[sid]["locked"] = self.lock_state
        self.app.highlight_selected()

    def undo(self):
        for sid, state in self.previous_states.items():
            if sid in self.app.tokens:
                self.app.tokens[sid]["locked"] = state
        self.app.highlight_selected()

    def serialize(self):
        return {"type": "lock", "labels": self.labels, "lock_state": self.lock_state}

class DeleteTokensCommand(Command):
    """Taking players off the board, as something undo can put back.

    Deleting used to happen straight on the canvas, so a player removed by mistake
    was gone for good -- Ctrl+Z would step past it to whatever came before. What is
    kept here is everything needed to build the same player again: who they are, how
    they are drawn, and where they stood, in rink metres so the spot is still right
    if the window or the field has changed since."""

    def __init__(self, app, tokens):
        self.app = app
        self.specs = [app._token_spec(token) for token in tokens]
        self.specs = [spec for spec in self.specs if spec]

    def execute(self):
        for spec in self.specs:
            sid = self.app._get_sid_by_label(spec["label"])
            token = self.app.tokens.get(sid) if sid else None
            if token:
                self.app._delete_token(token)
        self.app._refresh_roster_counts()

    def undo(self):
        for spec in self.specs:
            self.app._restore_token(spec)
        self.app._refresh_roster_counts()

    def serialize(self):
        return {"type": "delete_tokens", "players": self.specs}


class HidePitchPartsCommand(Command):
    """Take a fixture of the rink itself off the board -- a goal, a face-off cross,
    the centre line, the boards -- or put it back.

    It cannot simply delete the canvas items: the rink is thrown away and redrawn
    from scratch on every resize, every rotation and every switch between a full and
    a half rink. What is remembered instead is which parts to leave out, and the
    drawing skips them from then on."""

    def __init__(self, app, keys, hide=True):
        self.app = app
        self.keys = set(keys)
        self.hide = hide
        self.changed = set()

    def execute(self):
        if self.hide:
            self.changed = self.keys - self.app.hidden_pitch_parts
            self.app.hidden_pitch_parts |= self.changed
        else:
            self.changed = self.keys & self.app.hidden_pitch_parts
            self.app.hidden_pitch_parts -= self.changed
        self.app.selected_pitch_parts.clear()
        self.app._apply_hidden_pitch_parts()
        self.app.highlight_selected()

    def undo(self):
        if self.hide:
            self.app.hidden_pitch_parts -= self.changed
        else:
            self.app.hidden_pitch_parts |= self.changed
        self.app._apply_hidden_pitch_parts()
        self.app.highlight_selected()

    def serialize(self):
        return {"type": "pitch_parts", "keys": sorted(self.keys), "hide": self.hide}


# ==========================================
# MAIN APPLICATION
# ==========================================

class FloorballTacticsApp:
    # One palette for the whole chrome. The toolbar sits on a light panel and the
    # rink keeps pure white, which separates the two without drawing a heavy border.
    C_PANEL = "#f1f3f5"
    C_SURFACE = "#ffffff"
    C_BORDER = "#dee2e6"
    C_TEXT = "#212529"
    C_MUTED = "#868e96"
    C_BTN = "#ffffff"
    C_BTN_HOVER = "#e7f5ff"
    C_ACCENT = "#228be6"
    C_ACCENT_FG = "#ffffff"

    # Players carry a white outline so they stay legible on the rink, over the
    # goal areas and on top of drawn lines.
    TOKEN_OUTLINE = "#ffffff"
    # A second, thinner ring drawn outside the white one, so the white edge itself has
    # a border and a light-coloured player does not dissolve into the rink.
    TOKEN_EDGE = "#000000"
    # The curve handle on a selected bend, kept distinct from the blue handles that
    # move or stretch a line.
    BEND_HANDLE_COLOR = "#f76707"

    # Every widget asked for "Segoe UI", which only exists on Windows. Elsewhere Tk
    # silently falls back to the "fixed" bitmap font, which is why the whole UI
    # rendered in blocky terminal type. Take the first family that is actually
    # installed instead, and fall back to whatever Tk itself defaults to.
    UI_FONT_CANDIDATES = ("Segoe UI", "Cantarell", "Ubuntu", "Noto Sans",
                          "DejaVu Sans", "Liberation Sans", "FreeSans",
                          "Nimbus Sans", "Nimbus Sans L", "Helvetica")

    @staticmethod
    def _pick_ui_font():
        installed = {name.lower(): name for name in tkfont.families()}
        for candidate in FloorballTacticsApp.UI_FONT_CANDIDATES:
            if candidate.lower() in installed:
                return installed[candidate.lower()]
        return tkfont.nametofont("TkDefaultFont").actual("family")

    def __init__(self, root):
        self.root = root
        self.root.title("Floorball Tactics Studio")
        self.root.geometry("1500x900")
        # The toolbar is pinned to two rows, so it cannot shed a row to cope with a
        # narrow window: below this width Tactics gets squeezed to a sliver and then
        # drops out entirely. Refuse the sizes that would break it. The floor rose from
        # 1240 when the Timeline box gained the animation list and its transport
        # buttons -- that width has to come from somewhere.
        self.root.minsize(1400, 700)
        self.root.configure(bg="#f8f9fa")
        self.UI_FONT = self._pick_ui_font()

        # Configure Modern TTK Styles
        self.style = ttk.Style()
        self.style.theme_use("clam")
        self.style.configure(".", background="#f8f9fa", foreground="#212529", font=(self.UI_FONT, 9))
        self.style.configure("TLabel", background="#ffffff", foreground="#212529")
        self.style.configure("TLabelframe", background="#ffffff", bordercolor="#dee2e6", relief="solid")
        self.style.configure("TLabelframe.Label", background="#ffffff", foreground="#343a40", font=(self.UI_FONT, 9, "bold"))

        # State Variables
        self.width = 1120
        self.height = 560
        self.tokens = {}        # token_id -> metadata (tokens share same object for text ids)
        self.drawn_items = {}   # canvas_id -> metadata for signs/lines
        self.watermark = None   # {path, image, mx, my, w_m, h_m} in rink metres
        self._watermark_photo = None

        # Animation: a list of keyframes, each a board snapshot in rink metres plus how
        # long the move *into* it takes. Step 0 is the opening slide.
        self.animation_steps = []
        self.animation_playhead = 0
        self.animation_playing = False
        self.animation_job = None
        self.anim_buttons = {}
        self.step_time_var = tk.DoubleVar(value=2.0)

        # Text labels and pictures placed on the board (the watermark is separate: one
        # of it, living underneath everything).
        self.board_images = {}          # canvas id -> {original, photo, w_px, h_px}
        self.text_size_var = tk.IntVar(value=14)
        self.color_theme = "Classic (black)"
        self.drawings = []      # list of (line_ids, drawing_data)
        self.action_steps = []  # textual step descriptions

        self.clipboard = []     # copy/cut buffer (serialized)
        self.copied_style = None
        self.paste_style_active = False
        self.dont_bother_again = False

        self.undo_stack = []
        self.redo_stack = []

        self.selected_tokens = []      # selected token shape ids
        self.selected_drawn = set()    # selected drawn canvas ids
        # Fixtures of the rink -- goals, face-off crosses, the centre line, the
        # boards -- can be selected and removed like anything else. They are held by
        # name rather than by canvas id because the rink is redrawn from scratch
        # whenever the window changes size or the board is rotated.
        self.selected_pitch_parts = set()
        self.hidden_pitch_parts = set()
        self.selection_rect = None
        self.selection_start = None
        # Where attached arrows sat when playback began; None means "not captured".
        self._attached_origins = None
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0

        self.drag_start_positions = {} 
        self.drag_data = {"x": 0, "y": 0}
        self.active_tool = None  
        self.temp_line_start = None
        self.current_preview_ids = []
        self.dragging_token_mode = False
        self.dragging_drawn_mode = False
        self.bend_control_point = None
        self.resize_mode = False
        self.resize_handle = None
        self.resize_start = None
        self.resize_anchor = None
        self.resize_initial_bounds = None
        self.resize_initial_coords = {}
        self.selection_overlay_ids = []
        self.selection_overlay_handles = []
        self.selection_overlay_handle_types = []
        self.line_edit_mode = False
        self.line_edit_handle = None
        self.line_edit_start = None
        self.line_edit_initial_coords = []

        self.GRID = 15
        self.grid_var = tk.BooleanVar(value=False)
        # Two independent choices: which field, and whether only one end of it is
        # drawn. The board opens on half a 5v5 rink, which is what most drills use.
        self.half_rink_var = tk.BooleanVar(value=True)
        self.rink_mode_var = tk.StringVar(value="5v5")
        self.snap_player_var = tk.BooleanVar(value=True)
        self.snap_angle_var = tk.BooleanVar(value=False)
        self.ghosting_var = tk.BooleanVar(value=False)
        self.goals_visible_var = tk.BooleanVar(value=True)
        self.player_size_var = tk.StringVar(value="14")
        self.sign_size_var = tk.StringVar(value="12")

        # Line style state
        self.line_color = "#000000"
        self.line_thick_var = tk.StringVar(value="2")
        self.line_type_var = tk.StringVar(value="Solid")

        # Roster color states
        self.att_color = "#000000"
        self.def_color = "#000000"
        # Signs used to borrow the line colour, so they could not be coloured apart
        # from the drawing tools.
        self.sign_color = "#000000"

        # Tactics: formation per team plus how far up the rink it is pushed.
        self.att_tactic_var = tk.StringVar(value="House")
        self.def_tactic_var = tk.StringVar(value="Dice")
        self.att_pct_var = tk.StringVar(value="70")
        self.def_pct_var = tk.StringVar(value="60")

        self.groups = []  
        self.tool_buttons = {}
        self.setting_buttons = {}
        self.sign_images = {}
        self.icon_cache = {}

        # view / layout state. These must be set BEFORE _load_config(), which
        # overrides them from the saved file -- assigning them afterwards silently
        # discarded the saved values, so menu and orientation choices never stuck.
        self.menu_two_rows = False
        self.menu_rows_mode = "two"    # the toolbar is a two-row bar by default
        self.menu_position = "top"     # top | bottom -- the bar never docks sideways
        self._menu_side = "top"
        self.rink_rotated = False  # False = default landscape, True = rotated 90deg vertical
        self._last_esc_time = 0.0

        # config path
        self.config_path = os.path.join(str(pathlib.Path.home()), ".floorball_tactics_config.json")
        self._load_config()

        # Build UI Structure
        self._setup_ui()
        self._draw_pitch()
        self._update_roster() 

        # Keyboard Shortcuts
        self._bind_keyboard_shortcuts()

    # ----------------------
    # Configuration
    # ----------------------
    def _bind_keyboard_shortcuts(self):
        bindings = [
            ("<Control-g>", lambda e: self.group_selected()),
            ("<Control-G>", lambda e: self.group_selected()),
            ("<Control-z>", self.undo),
            ("<Control-Z>", self.undo),
            ("<Control-Shift-Z>", self.redo),
            ("<Control-Shift-z>", self.redo),
            ("<Control-y>", self.redo),
            ("<Control-Y>", self.redo),
            ("<Escape>", self.cancel_active_tool),
            ("<Control-c>", lambda e: self.copy_selection()),
            ("<Control-C>", lambda e: self.copy_selection()),
            ("<Control-x>", lambda e: self.cut_selection()),
            ("<Control-X>", lambda e: self.cut_selection()),
            ("<Control-v>", lambda e: self.paste_clipboard()),
            ("<Control-V>", lambda e: self.paste_clipboard()),
            ("<Control-a>", lambda e: self.select_all()),
            ("<Control-A>", lambda e: self.select_all()),
            ("<Delete>", self._delete_key),
            ("<BackSpace>", self._remove_key),
            # Ctrl and the zoom keys. Tk reports the same key differently depending on
            # whether Shift is involved and which keyboard it is, so plus, equal and
            # KP_Add all have to be bound for "zoom in" to work everywhere.
            ("<Control-plus>", self.zoom_in),
            ("<Control-equal>", self.zoom_in),
            ("<Control-KP_Add>", self.zoom_in),
            ("<Control-minus>", self.zoom_out),
            ("<Control-underscore>", self.zoom_out),
            ("<Control-KP_Subtract>", self.zoom_out),
            ("<Control-0>", self.zoom_reset),
            ("<Control-KP_0>", self.zoom_reset),
        ]
        for sequence, callback in bindings:
            self.root.bind_all(sequence, callback)

    def _load_config(self):
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, "r") as f:
                    cfg = json.load(f)
                raw_line_color = cfg.get("line_color")
                self.line_color = "#000000" if raw_line_color in (None, "#212529") else (raw_line_color or self.line_color)
                self.line_thick_var = tk.StringVar(value=str(cfg.get("line_thick", self.line_thick_var.get())))
                self.line_type_var = tk.StringVar(value=cfg.get("line_type", self.line_type_var.get()))
                raw_att_color = cfg.get("att_color")
                self.att_color = "#000000" if raw_att_color in (None, "#212529") else (raw_att_color or self.att_color)
                raw_def_color = cfg.get("def_color")
                self.def_color = "#000000" if raw_def_color in (None, "#495057") else (raw_def_color or self.def_color)
                self.sign_color = cfg.get("sign_color", self.sign_color) or self.sign_color
                self.att_tactic_var = tk.StringVar(value=cfg.get("att_tactic", self.att_tactic_var.get()))
                self.def_tactic_var = tk.StringVar(value=cfg.get("def_tactic", self.def_tactic_var.get()))
                self.att_pct_var = tk.StringVar(value=str(cfg.get("att_pct", self.att_pct_var.get())))
                self.def_pct_var = tk.StringVar(value=str(cfg.get("def_pct", self.def_pct_var.get())))
                self.half_rink_var = tk.BooleanVar(value=cfg.get("half_rink", self.half_rink_var.get()))
                # Older config files name the fields the way the app used to, and the
                # oldest have nothing but the half/full flag; _read_rink_mode reads
                # both and hands back the field and the half separately.
                saved_mode, saved_half = self._read_rink_mode(cfg)
                self.rink_mode_var = tk.StringVar(value=saved_mode)
                self.half_rink_var.set(saved_half)
                self.grid_var = tk.BooleanVar(value=False)
                self.snap_player_var = tk.BooleanVar(value=cfg.get("snap_player", self.snap_player_var.get()))
                self.snap_angle_var = tk.BooleanVar(value=cfg.get("snap_angle", self.snap_angle_var.get()))
                self.ghosting_var = tk.BooleanVar(value=False)
                self.dont_bother_again = cfg.get("dont_bother_again", False)
                self.menu_two_rows = cfg.get("menu_two_rows", self.menu_two_rows)
                self.menu_rows_mode = cfg.get("menu_rows_mode", self.menu_rows_mode)
                saved_position = cfg.get("menu_position", self.menu_position)
                self.menu_position = saved_position if saved_position in ("top", "bottom") else "top"
                self.rink_rotated = cfg.get("rink_rotated", self.rink_rotated)
                # Only the name is stored: the colours themselves are already saved
                # individually, and a theme the app no longer ships falls back cleanly.
                saved_theme = cfg.get("color_theme", self.color_theme)
                if saved_theme in self.COLOR_THEMES:
                    self.color_theme = saved_theme
        except Exception:
            pass

    def _save_config(self):
        try:
            cfg = {
                "line_color": self.line_color,
                "line_thick": int(self.line_thick_var.get()),
                "line_type": self.line_type_var.get(),
                "att_color": self.att_color,
                "def_color": self.def_color,
                "sign_color": self.sign_color,
                "att_tactic": self.att_tactic_var.get(),
                "def_tactic": self.def_tactic_var.get(),
                "att_pct": self.att_pct_var.get(),
                "def_pct": self.def_pct_var.get(),
                "half_rink": self.half_rink_var.get(),
                "rink_mode": self.rink_mode,
                "grid": self.grid_var.get(),
                "snap_player": self.snap_player_var.get(),
                "snap_angle": self.snap_angle_var.get(),
                "ghosting": self.ghosting_var.get(),
                "dont_bother_again": self.dont_bother_again,
                "menu_two_rows": self.menu_two_rows,
                "menu_rows_mode": self.menu_rows_mode,
                "menu_position": self.menu_position,
                "rink_rotated": self.rink_rotated,
                "color_theme": self.color_theme
            }
            with open(self.config_path, "w") as f:
                json.dump(cfg, f, indent=2)
        except Exception as e:
            print("Failed to save config:", e)

    # ----------------------
    # Commands / Undo helpers
    # ----------------------
    def push_command(self, cmd, execute=True):
        if execute:
            cmd.execute()
        # Commands pushed with execute=False have already happened on the canvas, so
        # anything they need to log has to be done here rather than in execute().
        recorder = getattr(cmd, "record", None)
        if callable(recorder):
            recorder()
        # Which group this instruction belongs to. Saving uses it to tell a superseded
        # instruction from one that merely looks similar but happens later in the play.
        cmd.animation_group = max(0, len(self.animation_steps) - 1)
        self.undo_stack.append(cmd)
        self.redo_stack.clear()
        # The board has changed under it, so whatever playback remembered about the
        # arrows is stale.
        self._attached_origins = None
        self._sync_full_coords()
        self._save_config()

    def undo(self, event=None):
        if self.undo_stack:
            cmd = self.undo_stack.pop()
            cmd.undo()
            self.redo_stack.append(cmd)
            self.clear_selection()

    def redo(self, event=None):
        if self.redo_stack:
            cmd = self.redo_stack.pop()
            cmd.execute()
            self.undo_stack.append(cmd)
            self.clear_selection()

    # ----------------------
    # Utility helpers
    # ----------------------
    def _get_sid_by_label(self, label):
        for sid, data in self.tokens.items():
            if data.get("label") == label and data.get("shape_id") == sid:
                return sid
        return None

    def choose_line_color(self):
        color_code = colorchooser.askcolor(title="Choose Line Color", color=self.line_color)[1]
        if color_code:
            self.line_color = color_code
            try:
                self.btn_line_color.config(bg=self.line_color)
            except Exception:
                pass

    def choose_sign_color(self):
        color_code = colorchooser.askcolor(title="Choose Sign Color", color=self.sign_color)[1]
        if color_code:
            self.sign_color = color_code
            try:
                self.btn_sign_color.config(bg=self.sign_color)
            except Exception:
                pass
            self._recolor_selected_signs()
            self._save_config()

    def _register_drawn_item(self, cid, meta):
        """Record a drawn item along with the canvas options that carry its colour.
        A sign is an outline, a fill, or both depending on its shape, and knowing
        which lets the colour be restored later without changing the shape's look."""
        options = [option for option in ("fill", "outline")
                   if self._item_has_option(cid, option)]
        meta = dict(meta)
        meta["color_options"] = options or ["fill"]
        self.drawn_items[cid] = meta
        return meta

    def _item_has_option(self, cid, option):
        try:
            return bool(self.canvas.itemcget(cid, option))
        except Exception:
            return False

    def _recolor_selected_signs(self):
        """Apply the current sign colour to anything already placed in the selection --
        signs and text labels alike -- so the picker fixes a mark after the fact and not
        only before."""
        for cid in self._selected_drawn_ids():
            meta = self.drawn_items.get(cid)
            if not meta or meta.get("type") not in ("sign", "text"):
                continue
            if meta.get("decor"):
                # The holes in the ball are what make it read as a ball; painting them
                # the sign colour would fill it in solid.
                continue
            meta["color"] = self.sign_color
            for option in meta.get("color_options") or ("fill",):
                try:
                    self.canvas.itemconfig(cid, **{option: self.sign_color})
                except Exception:
                    pass

    def choose_att_color(self):
        color_code = colorchooser.askcolor(title="Choose Attack Team Color", color=self.att_color)[1]
        if color_code:
            self.att_color = color_code
            self.recolor_team("att", color_code)

    def choose_def_color(self):
        color_code = colorchooser.askcolor(title="Choose Defense Team Color", color=self.def_color)[1]
        if color_code:
            self.def_color = color_code
            self.recolor_team("def", color_code)

    def recolor_team(self, team, colour):
        """Repaint a team where it stands.

        This used to call _update_roster(), which deletes every token and respawns the
        default formation -- so picking a colour threw away the arrangement, the roles
        and the undo history along with it."""
        swatch = getattr(self, "btn_att_color" if team == "att" else "btn_def_color", None)
        if swatch is not None:
            try:
                swatch.config(bg=colour)
            except Exception:
                pass
        for token in self._team_tokens(team):
            token["color"] = colour
            skip = set(token.get("decor_ids", ())) | set(token.get("halo_ids", ()))
            for item in self._token_items(token):
                if item in skip:
                    continue
                try:
                    self.canvas.itemconfig(item, fill=colour)
                except Exception:
                    pass
        self._save_config()

    def _roster_count_changed(self, event=None):
        """Adding or removing players resizes the team in place, leaving the players
        already on the board exactly where they are."""
        for team, spinbox in (("att", getattr(self, "att_spinbox", None)),
                              ("def", getattr(self, "def_spinbox", None))):
            if spinbox is None:
                continue
            try:
                wanted = max(1, min(10, int(spinbox.get())))
            except Exception:
                continue
            if wanted != len(self._team_tokens(team)):
                self._set_team_count(team, wanted)

    def _roster_shape_changed(self, event=None):
        """Restyle a team without moving it: each player is redrawn in the new shape at
        the spot it already occupies."""
        for team, shape_var in (("att", getattr(self, "att_shape_var", None)),
                                ("def", getattr(self, "def_shape_var", None))):
            if shape_var is None:
                continue
            shape = shape_var.get()
            for token in self._team_tokens(team):
                if str(token.get("shape", "")).lower() == shape.lower():
                    continue
                box = self.canvas.bbox(token["shape_id"])
                if not box:
                    continue
                cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
                carried = {key: token.get(key) for key in
                           ("label", "team", "position", "color", "size", "locked",
                            "angle", "is_ghost", "ghost_of", "stipple")}
                self._delete_token(token)
                sid = self._create_token(cx, cy, carried["label"], shape=shape,
                                         color=carried["color"] or "black",
                                         size=carried["size"], team=carried["team"])
                fresh = self.tokens.get(sid)
                if fresh:
                    fresh.update({k: v for k, v in carried.items()
                                  if k not in ("label", "shape")})
                    fresh["shape"] = shape
                    self._set_token_position(fresh, carried["position"])
        self.highlight_selected()

    # ----------------------
    # Macros save / load
    # ----------------------
    def _board_snapshot(self):
        """Where every player actually stands, in rink metres.

        The command log alone cannot reproduce this: every move it records is a pixel
        delta, so replaying it onto a board whose players start elsewhere, or in a
        window of a different size, lands them in the wrong places. Metres are
        independent of window size, zoom and rink orientation."""
        state = self._pitch_state()
        players = []
        if state["scale"]:
            seen = set()
            for token in self.tokens.values():
                sid = token.get("shape_id")
                if sid is None or sid in seen:
                    continue
                seen.add(sid)
                centre = self._token_centre_px(token)
                if not centre:
                    continue
                mx, my = self._state_px_to_m(centre[0], centre[1], state)
                players.append({
                    "label": token.get("label"),
                    "team": self._token_team(token),
                    "position": token.get("position"),
                    "shape": token.get("shape"),
                    "color": token.get("color"),
                    "mx": round(mx, 3),
                    "my": round(my, 3),
                })
        return {
            "half_rink": bool(self.half_rink_var.get()),
            "rink_mode": self.rink_mode,
            "rink_rotated": bool(self.rink_rotated),
            "players": players,
            "watermark": self._watermark_snapshot(),
            # Which fixtures of the rink were taken off it. Part of the board, not of
            # the command log: a snapshot has to describe the rink as it looked.
            "hidden_pitch_parts": sorted(self.hidden_pitch_parts),
        }

    def _watermark_snapshot(self):
        """The watermark as JSON: the untouched original embedded as base64 PNG, plus
        its placement and the crop/background settings.

        The pixels travel with the macro rather than a path to them, so a tactic still
        carries its logo on another machine, or after the file has been moved. Crop and
        background removal stay as parameters, so they remain adjustable after a load."""
        wm = getattr(self, "watermark", None)
        if not wm or not wm.get("original"):
            return None
        try:
            buffer = io.BytesIO()
            # Lossless, deliberately. JPEG would cut a 430x470 crest from ~235 kB of
            # base64 to ~35 kB, but background removal keys on exact luminance: at
            # quality 90 the compression artefacts flipped 1.8% of the pixels across
            # the transparency threshold at the strictest setting, which shows up as
            # speckle round the logo. Size is bounded instead by WATERMARK_MAX_PX.
            image_format = "PNG"
            wm["original"].save(buffer, format="PNG", optimize=True)
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        except Exception:
            return None
        crop = wm.get("crop")
        return {
            "mx": round(wm["mx"], 3), "my": round(wm["my"], 3),
            "w_m": round(wm["w_m"], 3), "h_m": round(wm["h_m"], 3),
            "crop": [round(float(v), 2) for v in crop] if crop else None,
            "bg_tolerance": wm.get("bg_tolerance"),
            "bg_mode": wm.get("bg_mode"),
            "behind": bool(wm.get("behind", True)),
            "opacity": int(wm.get("opacity", 100)),
            "path": wm.get("path"),
            "image_format": image_format,
            "png_base64": encoded,
        }

    def _ask_for_replacement_image(self, missing_path):
        """The macro named an image it could not produce -- no embedded copy and no
        file at the saved path. Offer to point at another one rather than silently
        dropping the logo."""
        name = os.path.basename(missing_path) if missing_path else "the watermark image"
        try:
            wants_replacement = messagebox.askyesno(
                "Watermark image missing",
                f"This macro's watermark ({name}) could not be found.\n\n"
                "Would you like to choose another image to use instead?")
        except Exception:
            return None
        if not wants_replacement:
            return None
        path = filedialog.askopenfilename(title="Choose a replacement watermark",
                                          filetypes=self.IMAGE_FILETYPES)
        if not path:
            return None
        try:
            image = Image.open(path).convert("RGBA")
        except Exception as error:
            messagebox.showerror("Error", f"Could not open that image: {error}")
            return None
        longest = max(image.width, image.height)
        if longest > self.WATERMARK_MAX_PX:
            ratio = self.WATERMARK_MAX_PX / longest
            image = image.resize((max(1, int(image.width * ratio)),
                                  max(1, int(image.height * ratio))),
                                 Image.Resampling.LANCZOS)
        self._replacement_watermark_path = path
        return image

    def _restore_watermark(self, data):
        if not data:
            self.watermark = None
            self._render_watermark()
            return
        image = None
        encoded = data.get("png_base64")
        if encoded:
            try:
                image = Image.open(io.BytesIO(base64.b64decode(encoded))).convert("RGBA")
            except Exception:
                image = None
        if image is None:
            # Older files, or one saved before the image was embedded: fall back to the
            # path it came from.
            path = data.get("path")
            if path and os.path.exists(path):
                try:
                    image = Image.open(path).convert("RGBA")
                except Exception:
                    image = None
        chosen_path = data.get("path")
        if image is None:
            self._replacement_watermark_path = None
            image = self._ask_for_replacement_image(chosen_path)
            # A replacement is a different file, and saving again should say so.
            chosen_path = getattr(self, "_replacement_watermark_path", None) or chosen_path
        if image is None:
            self.watermark = None
            self._render_watermark()
            return
        self.watermark = {
            "path": chosen_path, "original": image,
            "crop": tuple(data["crop"]) if data.get("crop") else None,
            "bg_tolerance": data.get("bg_tolerance"), "bg_mode": data.get("bg_mode"),
            "behind": bool(data.get("behind", True)),
            "opacity": int(data.get("opacity", 100)),
            "mx": float(data.get("mx", 20.0)), "my": float(data.get("my", 10.0)),
            "w_m": float(data.get("w_m", self.WATERMARK_DEFAULT_W_M)),
            "h_m": float(data.get("h_m", 6.0)),
        }
        self._refresh_watermark_image()
        self._render_watermark()

    # ----------------------
    # The whole play, as data
    # ----------------------
    # Canvas options worth recording per item type. Everything about how a mark looks
    # travels with it, so a file reopens looking exactly like the board it was saved
    # from rather than being re-derived from the tool that happened to draw it.
    ITEM_STYLE_OPTIONS = {
        "line": ("fill", "width", "dash", "arrow", "arrowshape", "smooth", "capstyle"),
        "polygon": ("fill", "outline", "width", "dash", "smooth"),
        "oval": ("fill", "outline", "width", "dash"),
        "rectangle": ("fill", "outline", "width", "dash"),
        "arc": ("fill", "outline", "width", "style", "start", "extent"),
        "text": ("fill", "text", "font", "anchor", "justify"),
        "image": ("anchor",),
    }
    # Kept out of the file: canvas ids that mean nothing in another session, and the
    # live PIL objects behind a picture.
    ITEM_META_SKIP = ("full_coords", "photo", "original", "image")

    def _drawings_snapshot(self):
        """Every mark on the board, in rink metres.

        The command log alone cannot reproduce a play. It records the pixels a line
        was drawn between, which mean nothing in another window size, on another
        field, or at another zoom -- which is why reopening a file used to scatter the
        arrows. What is written here is the geometry itself, in the same metres the
        players are saved in, plus the style each item carries and the group it
        belongs to in the animation."""
        state = self._pitch_state()
        if not state.get("scale"):
            return []
        items = []
        for cid, meta in self.drawn_items.items():
            try:
                kind = self.canvas.type(cid)
                coords = self.canvas.coords(cid)
            except Exception:
                continue
            if not kind:
                continue
            # full_coords, not the live ones: an arrow part-way through being revealed
            # would otherwise be saved half drawn.
            geometry = meta.get("full_coords") or coords
            points = [self._state_px_to_m(geometry[i], geometry[i + 1], state)
                      for i in range(0, len(geometry) - 1, 2)]
            entry = {
                "kind": kind,
                "points_m": [[round(mx, 4), round(my, 4)] for mx, my in points],
                "style": self._item_style(cid, kind),
                "meta": {key: value for key, value in meta.items()
                         if key not in self.ITEM_META_SKIP
                         and isinstance(value, (str, int, float, bool, type(None), list))},
            }
            data = meta.get("data")
            if isinstance(data, dict):
                # The record a tool drew from, in metres too, so a bend keeps its
                # control point and an attached arrow keeps knowing which way it runs.
                entry["data"] = dict(data)
                for a, b in (("x1", "y1"), ("x2", "y2")):
                    if a in data and b in data:
                        mx, my = self._state_px_to_m(data[a], data[b], state)
                        entry["data"][a + "_m"], entry["data"][b + "_m"] = (round(mx, 4),
                                                                           round(my, 4))
                extra = data.get("extra") or {}
                if "cx" in extra and "cy" in extra:
                    mx, my = self._state_px_to_m(extra["cx"], extra["cy"], state)
                    entry["data"]["cx_m"], entry["data"]["cy_m"] = round(mx, 4), round(my, 4)
            if kind == "image":
                entry["image"] = self._board_image_snapshot(cid)
            items.append(entry)
        return items

    def _item_style(self, cid, kind):
        style = {}
        for option in self.ITEM_STYLE_OPTIONS.get(kind, ()):
            try:
                value = self.canvas.itemcget(cid, option)
            except Exception:
                continue
            if value not in ("", None):
                style[option] = value
        return style

    def _board_image_snapshot(self, cid):
        """A placed picture, pixels and all, so the file carries it the way a macro
        carries its watermark."""
        record = self.board_images.get(cid)
        if not record or not record.get("original"):
            return None
        try:
            buffer = io.BytesIO()
            record["original"].save(buffer, format="PNG", optimize=True)
        except Exception:
            return None
        state = self._pitch_state()
        return {"png_base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
                "path": record.get("path"),
                "w_m": round(record.get("w_px", 1) / (state.get("scale") or 1), 4),
                "h_m": round(record.get("h_px", 1) / (state.get("scale") or 1), 4)}

    def _restore_drawings(self, entries):
        """Put every saved mark back on the board, converting its metres to the pixels
        of the rink as it is drawn now."""
        state = self._pitch_state()
        if not state.get("scale") or not entries:
            return []
        for cid in list(self.drawn_items):
            try:
                self.canvas.delete(cid)
            except Exception:
                pass
        self.drawn_items.clear()
        self.board_images.clear()

        restored = []
        for entry in entries:
            kind = entry.get("kind")
            points = [self._state_m_to_px(mx, my, state)
                      for mx, my in entry.get("points_m") or ()]
            flat = [value for point in points for value in point]
            style = dict(entry.get("style") or {})
            meta = dict(entry.get("meta") or {})
            cid = None
            try:
                if kind == "image":
                    cid = self._restore_board_image(entry, points, state)
                elif kind == "text":
                    text = style.pop("text", meta.get("text", ""))
                    cid = self.canvas.create_text(*flat[:2], text=text,
                                                  tags=("sign",), **style)
                elif kind == "line" and len(flat) >= 4:
                    cid = self.canvas.create_line(*flat, tags=("tactic_line",), **style)
                elif kind == "polygon" and len(flat) >= 6:
                    cid = self.canvas.create_polygon(*flat, tags=("sign",), **style)
                elif kind in ("oval", "rectangle", "arc") and len(flat) >= 4:
                    maker = {"oval": self.canvas.create_oval,
                             "rectangle": self.canvas.create_rectangle,
                             "arc": self.canvas.create_arc}[kind]
                    cid = maker(*flat[:4], tags=("sign",), **style)
            except Exception:
                cid = None
            if cid is None:
                continue
            data = entry.get("data")
            if isinstance(data, dict):
                data = dict(data)
                for a, b in (("x1", "y1"), ("x2", "y2")):
                    if a + "_m" in data:
                        px, py = self._state_m_to_px(data.pop(a + "_m"),
                                                     data.pop(b + "_m"), state)
                        data[a], data[b] = px, py
                if "cx_m" in data:
                    px, py = self._state_m_to_px(data.pop("cx_m"), data.pop("cy_m"), state)
                    data.setdefault("extra", {}).update({"cx": px, "cy": py})
                meta["data"] = data
            if meta.get("group"):
                try:
                    self.canvas.addtag_withtag(meta["group"], cid)
                except Exception:
                    pass
            self._register_drawn_item(cid, meta)
            self.drawn_items[cid]["full_coords"] = list(self.canvas.coords(cid))
            restored.append(cid)
        return restored

    def _restore_board_image(self, entry, points, state):
        payload = entry.get("image") or {}
        encoded = payload.get("png_base64")
        if not encoded or not points:
            return None
        try:
            image = Image.open(io.BytesIO(base64.b64decode(encoded))).convert("RGBA")
        except Exception:
            return None
        scale = state.get("scale") or 1
        record = {"original": image, "path": payload.get("path"),
                  "w_px": max(1, payload.get("w_m", 1) * scale),
                  "h_px": max(1, payload.get("h_m", 1) * scale)}
        return self._draw_board_image(record, points[0][0], points[0][1])

    def _animation_snapshot(self):
        """The whole sequence: every group, what is in it, how long it runs and when it
        starts. The board of each group is already in metres, so a saved play reopens
        as the same play rather than as one long group with everything in it."""
        groups, elapsed = [], 0.0
        for index, step in enumerate(self.animation_steps):
            duration = float(step.get("duration", 1.0))
            groups.append({
                "index": index,
                "name": step.get("name") or f"Group {index}",
                "named": bool(step.get("named")),
                "closed": bool(step.get("closed")),
                # Group 0 is the board as it stands, so the clock starts at the end of
                # it: every later group begins where the one before finished.
                "starts_at": round(elapsed, 3),
                "duration": round(duration, 3),
                "ends_at": round(elapsed + (duration if index else 0.0), 3),
                "actions": list(step.get("actions") or []),
                "board": step.get("board"),
            })
            if index:
                elapsed += duration
        return {"fps": self.ANIMATION_FPS, "playhead": self.animation_playhead,
                "total_seconds": round(elapsed, 3), "groups": groups}

    def _restore_animation(self, payload):
        """Rebuild the timeline from a saved play."""
        if not isinstance(payload, dict):
            return False
        groups = payload.get("groups")
        if not isinstance(groups, list):
            return False
        self.animation_steps = []
        for entry in groups:
            if not isinstance(entry, dict):
                continue
            self.animation_steps.append({
                "name": entry.get("name") or f"Group {len(self.animation_steps)}",
                "named": bool(entry.get("named")),
                "closed": bool(entry.get("closed", True)),
                "duration": max(0.0, float(entry.get("duration", 1.0))),
                "actions": list(entry.get("actions") or []),
                "board": entry.get("board"),
            })
        self.animation_playhead = max(0, min(int(payload.get("playhead", 0)),
                                             max(0, len(self.animation_steps) - 1)))
        self._refresh_animation_list()
        return True

    def _attachments_snapshot(self, order):
        """Which arrows are snapped to which player, by position in the drawings list."""
        index_of = {cid: position for position, cid in enumerate(order)}
        found = {}
        seen = set()
        for token in self.tokens.values():
            sid = token.get("shape_id") if isinstance(token, dict) else None
            if sid is None or sid in seen or not token.get("label"):
                continue
            seen.add(sid)
            starts = [index_of[cid] for cid in token.get("attached_lines_start") or ()
                      if cid in index_of]
            ends = [index_of[cid] for cid in token.get("attached_lines_end") or ()
                    if cid in index_of]
            if starts or ends:
                found[token["label"]] = {"start": starts, "end": ends}
        return found

    def _restore_attachments(self, payload, restored):
        for label, entry in (payload or {}).items():
            sid = self._get_sid_by_label(label)
            token = self.tokens.get(sid) if sid else None
            if not token or not isinstance(entry, dict):
                continue
            token["attached_lines_start"] = [restored[i] for i in entry.get("start", [])
                                             if 0 <= i < len(restored)]
            token["attached_lines_end"] = [restored[i] for i in entry.get("end", [])
                                           if 0 <= i < len(restored)]

    def _restore_board(self, board):
        players = [p for p in (board.get("players") or []) if p.get("label")]
        # The rink itself, before anything is placed on it. Only when the file says
        # something about it, so an older macro leaves the board as it is.
        if "hidden_pitch_parts" in board:
            wanted = set(board.get("hidden_pitch_parts") or ())
            if wanted != self.hidden_pitch_parts:
                self.hidden_pitch_parts = wanted
                self._apply_hidden_pitch_parts()
        # Before the early return below: a macro may carry a watermark and no players.
        # Pushed as a command rather than applied directly, so loading a logo shows up
        # in the timeline and can be undone like anything else.
        # Nothing to say when the file has no watermark and the board has none either:
        # without this, loading an ordinary tactic wrote "Watermark removed" into the
        # timeline every time.
        if board.get("watermark") or (("watermark" in board) and self.watermark):
            self.push_command(SetWatermarkCommand(self, board.get("watermark"),
                                                  self._watermark_snapshot()))
        if not players:
            return
        # Restore the view first: the saved metres describe positions on that rink.
        # Which field, and whether it was the half of it, are two separate answers,
        # and a file written before that was true has only the old names to give.
        wanted_mode, wanted_half = self._read_rink_mode(board)
        self.set_rink_mode(wanted_mode, half=wanted_half)
        if bool(self.rink_rotated) != bool(board.get("rink_rotated", self.rink_rotated)):
            self.rotate_rink("vertical" if board.get("rink_rotated") else "horizontal")

        for team in ("att", "def"):
            wanted = [p for p in players if p.get("team") == team]
            if wanted:
                self._set_team_count(team, len(wanted))

        for entry in players:
            sid = self._get_sid_by_label(entry["label"])
            token = self.tokens.get(sid) if sid else None
            if not token:
                continue
            centre = self._token_centre_px(token)
            if not centre:
                continue
            nx, ny = self._rink_to_px(entry.get("mx", 0.0), entry.get("my", 0.0))
            dx = nx - centre[0]
            dy = ny - centre[1]
            for item in self._token_items(token) + list(token.get("text_ids", [])):
                try:
                    self.canvas.move(item, dx, dy)
                except Exception:
                    pass
            self._set_token_position(token, entry.get("position"))

    # ----------------------
    # Text and image objects
    # ----------------------
    TEXT_MIN_SIZE = 6
    TEXT_MAX_SIZE = 96
    IMAGE_DEFAULT_W_M = 8.0
    IMAGE_MIN_PX = 12

    def _ask_for_text(self, initial=""):
        """A small modal in the app's own style, rather than Tk's stock prompt."""
        window = tk.Toplevel(self.root)
        window.title("Text")
        window.transient(self.root)
        window.configure(bg=self.C_PANEL)
        tk.Label(window, text="Text to place on the board:", bg=self.C_PANEL,
                 fg=self.C_TEXT, font=(self.UI_FONT, 9)).pack(padx=14, pady=(12, 6))
        value = tk.StringVar(value=initial)
        entry = tk.Entry(window, textvariable=value, font=(self.UI_FONT, 10), width=34,
                         relief=tk.FLAT, highlightthickness=1,
                         highlightbackground=self.C_BORDER, bg=self.C_SURFACE)
        entry.pack(padx=14)
        entry.focus_set()
        result = {"text": None}

        def accept(_event=None):
            result["text"] = value.get().strip()
            window.destroy()

        buttons = tk.Frame(window, bg=self.C_PANEL)
        buttons.pack(padx=14, pady=12, fill=tk.X)
        cfg = {"font": (self.UI_FONT, 8), "relief": tk.FLAT, "bg": self.C_BTN,
               "fg": self.C_TEXT, "bd": 0, "highlightthickness": 1,
               "highlightbackground": self.C_BORDER, "padx": 10, "pady": 3,
               "cursor": "hand2"}
        tk.Button(buttons, text="Place", command=accept, **cfg).pack(side=tk.RIGHT, padx=3)
        tk.Button(buttons, text="Cancel", command=window.destroy, **cfg).pack(side=tk.RIGHT)
        entry.bind("<Return>", accept)
        window.bind("<Escape>", lambda _e: window.destroy())
        self._make_modal(window)
        self._wait_modal(window)
        return result["text"] or None

    def place_text_canvas(self, x, y, text=None, size=None):
        """Put a text label on the board. It is a drawn item like a sign, so it can be
        selected, moved, recoloured, rotated with the rest and deleted."""
        if text is None:
            text = self._ask_for_text()
        if not text:
            return []
        try:
            # One Size dial for the whole box: a new label is typed at the size shown
            # there, the same size a stamped sign would use.
            size = int(size if size is not None else self.sign_size_var.get())
        except Exception:
            size = 14
        size = max(self.TEXT_MIN_SIZE, min(self.TEXT_MAX_SIZE, size))
        cid = self.canvas.create_text(x, y, text=text, fill=self.sign_color,
                                      font=(self.UI_FONT, size, "bold"),
                                      tags=("sign", "board_text"))
        self._register_drawn_item(cid, {"type": "text", "sign_type": "text",
                                        "text": text, "size": size,
                                        "color": self.sign_color})
        description = f'Text "{text}"'
        self.record_action_in_group(description)
        self.tag_items_with_group([cid], description)
        return [cid]

    def _scale_board_text(self, cid, factor):
        meta = self.drawn_items.get(cid)
        if not meta or meta.get("type") != "text" or abs(factor - 1.0) < 1e-9:
            return
        size = max(self.TEXT_MIN_SIZE,
                   min(self.TEXT_MAX_SIZE, int(round(meta.get("size", 14) * factor))))
        if size == meta.get("size"):
            return
        meta["size"] = size
        try:
            self.canvas.itemconfig(cid, font=(self.UI_FONT, size, "bold"))
        except Exception:
            pass

    def _apply_sign_size(self, _event=None):
        """The Size field governs both kinds of mark: it restamps the selected signs at
        the new size and retypes the selected text labels to match, as well as setting
        the size for whatever is placed next."""
        try:
            size = max(6, min(60, int(self.sign_size_var.get())))
        except Exception:
            return
        # Keep the text field in step -- one Size, one look.
        try:
            self.text_size_var.set(max(self.TEXT_MIN_SIZE,
                                       min(self.TEXT_MAX_SIZE, size)))
        except Exception:
            pass

        removed, added = set(), set()
        done_groups = set()
        for cid in self._selected_drawn_ids():
            meta = self.drawn_items.get(cid)
            if not meta:
                continue
            if meta.get("type") == "text":
                meta["size"] = max(self.TEXT_MIN_SIZE, min(self.TEXT_MAX_SIZE, size))
                try:
                    self.canvas.itemconfig(cid, font=(self.UI_FONT, meta["size"], "bold"))
                except Exception:
                    pass
                continue
            if meta.get("type") != "sign":
                continue
            group = meta.get("group")
            if group in done_groups:
                continue
            done_groups.add(group)
            # A sign is drawn at a fixed size, so changing it means stamping a fresh one
            # in the same place and dropping the old.
            box = self.canvas.bbox(group) if group else self.canvas.bbox(cid)
            if not box:
                continue
            centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
            sign_type = meta.get("sign_type", "dot")
            for member in (self.canvas.find_withtag(group) if group else (cid,)):
                self.canvas.delete(member)
                self.drawn_items.pop(member, None)
                removed.add(member)
            self._replaying_sign = True
            try:
                added.update(self.place_sign_canvas(centre[0], centre[1], sign_type,
                                                    size=size))
            finally:
                self._replaying_sign = False

        if removed or added:
            # Keep everything else that was selected: restamping the signs must not
            # drop the text labels out of the selection alongside them.
            self.selected_drawn = (set(self.selected_drawn) - removed) | added
            self.highlight_selected()

    def _apply_text_size(self, _event=None):
        """The size field: retype the selected labels, and set the size for new ones."""
        try:
            size = max(self.TEXT_MIN_SIZE,
                       min(self.TEXT_MAX_SIZE, int(self.text_size_var.get())))
        except Exception:
            return
        for cid in self._selected_drawn_ids():
            meta = self.drawn_items.get(cid)
            if not meta or meta.get("type") != "text":
                continue
            meta["size"] = size
            try:
                self.canvas.itemconfig(cid, font=(self.UI_FONT, size, "bold"))
            except Exception:
                pass

    def add_board_image(self):
        """Load a picture onto the rink. Unlike the watermark -- of which there is one,
        living under everything -- these are ordinary objects: several at a time, on top
        of the rink, movable and resizable like anything else."""
        path = filedialog.askopenfilename(filetypes=self.IMAGE_FILETYPES)
        if not path:
            return None
        try:
            image = Image.open(path).convert("RGBA")
        except Exception as error:
            messagebox.showerror("Error", f"Could not open that image: {error}")
            return None
        longest = max(image.width, image.height)
        if longest > self.WATERMARK_MAX_PX:
            ratio = self.WATERMARK_MAX_PX / longest
            image = image.resize((max(1, int(image.width * ratio)),
                                  max(1, int(image.height * ratio))),
                                 Image.Resampling.LANCZOS)
        scale = self._pitch_state().get("scale") or 20
        width_px = max(self.IMAGE_MIN_PX, int(self.IMAGE_DEFAULT_W_M * scale))
        height_px = max(self.IMAGE_MIN_PX,
                        int(width_px * image.height / max(image.width, 1)))
        cx, cy = self._pitch_center_px()
        record = {"original": image, "path": path, "w_px": width_px, "h_px": height_px}
        cid = self._draw_board_image(record, cx, cy)
        description = f"Image {os.path.basename(path)}"
        self.record_action_in_group(description)
        self.tag_items_with_group([cid], description)
        self.clear_selection()
        self.selected_drawn = {cid}
        self.highlight_selected()
        self._draw_selection_overlay()
        return cid

    def _draw_board_image(self, record, x, y):
        photo = ImageTk.PhotoImage(
            record["original"].resize((max(1, int(record["w_px"])),
                                       max(1, int(record["h_px"]))),
                                      Image.Resampling.LANCZOS))
        cid = self.canvas.create_image(x, y, image=photo, tags=("sign", "board_image"))
        # Tk keeps no reference to a PhotoImage, so it must be held here or the picture
        # is garbage collected and the item draws as nothing at all.
        record["photo"] = photo
        self.board_images[cid] = record
        self._register_drawn_item(cid, {"type": "image", "sign_type": "image",
                                        "path": record.get("path")})
        return cid

    def _scale_board_image(self, cid, scale_x, scale_y):
        record = self.board_images.get(cid)
        if not record:
            return
        if abs(scale_x - 1.0) < 1e-9 and abs(scale_y - 1.0) < 1e-9:
            return
        record["w_px"] = max(self.IMAGE_MIN_PX, record["w_px"] * scale_x)
        record["h_px"] = max(self.IMAGE_MIN_PX, record["h_px"] * scale_y)
        coords = self.canvas.coords(cid)
        photo = ImageTk.PhotoImage(
            record["original"].resize((max(1, int(record["w_px"])),
                                       max(1, int(record["h_px"]))),
                                      Image.Resampling.LANCZOS))
        record["photo"] = photo
        try:
            self.canvas.itemconfig(cid, image=photo)
            if coords:
                self.canvas.coords(cid, *coords)
        except Exception:
            pass

    def reset_board(self):
        """Back to a clean board: default formations, nothing drawn, no history.

        It asks first -- there is no undo past a reset, because the history is part of
        what it clears."""
        if not messagebox.askyesno(
                "Reset the board?",
                "This clears the drawings and signs, the watermark, the timeline and "
                "the animation steps, and puts both teams back in their starting "
                "positions.\n\nIt cannot be undone. Continue?"):
            return

        self.stop_animation()
        self.animation_steps = []
        self.animation_playhead = 0
        self._refresh_animation_list()

        self.cancel_active_tool()
        self.clear_selection()
        for cid in list(self.drawn_items):
            try:
                self.canvas.delete(cid)
            except Exception:
                pass
        self.drawn_items.clear()
        self.drawings = []
        for tag in ("sign", "tactic_line", "ghost", "watermark"):
            try:
                self.canvas.delete(tag)
            except Exception:
                pass

        self.watermark = None
        self._watermark_photo = None
        self.board_images.clear()

        self.undo_stack.clear()
        self.redo_stack.clear()
        self.action_steps.clear()
        try:
            self.steps_listbox.delete(0, tk.END)
        except Exception:
            pass
        self.clipboard = []
        self.groups = []
        # A whole rink again, goals and crosses included.
        self.hidden_pitch_parts.clear()

        # _update_roster is the one place that rebuilds both teams from scratch, which
        # is exactly what is wanted here (and exactly why redraw_canvas must not call
        # it -- there it would wipe the board on every resize).
        self._update_roster()
        self.redraw_canvas()
        self._update_indicators()

    # ----------------------
    # Animation
    # ----------------------
    # 25 frames a second on screen and in the exported GIF: smooth enough for players
    # sliding across a rink, cheap enough that a long sequence still exports quickly.
    ANIMATION_FPS = 25
    PLAYHEAD_COLOR = "#e03131"

    def add_animation_step(self):
        """Freeze the board as the next keyframe.

        The very first Add Step records two: the board as it stands becomes step 0, the
        opening slide, so there is always something to move *from*."""
        duration = max(0.0, float(self.step_time_var.get()))
        snapshot = self._board_snapshot()
        if not self.animation_steps:
            self.animation_steps.append({"duration": duration, "board": snapshot,
                                         "name": "Start", "actions": [],
                                         "named": False, "closed": True})
            self.animation_playhead = 0
            self._refresh_animation_list()
            return
        # Closing the current group is the point of the button: whatever is recorded
        # next starts a new step instead of joining this one.
        if self.animation_steps:
            self.animation_steps[-1]["closed"] = True
        self.animation_steps.append({"duration": duration, "board": snapshot,
                                     "name": f"Step {len(self.animation_steps)}",
                                     "actions": [], "named": False})
        self.animation_playhead = len(self.animation_steps) - 1
        self._refresh_animation_list()

    def delete_animation_selection(self):
        """Delete whatever the timeline has picked.

        One button for both, because the tree holds both: a group row takes the whole
        group with it, an action row takes only that action out of its group. With
        nothing picked it falls back to the group the red marker is on, which is what
        the button used to do on its own."""
        tree = getattr(self, "anim_tree", None)
        selection = tree.selection() if tree is not None else ()
        item = selection[0] if selection else None
        if item and tree.parent(item):
            group_index = self._group_index_for(item)
            try:
                position = int(item.split("a")[-1])
            except ValueError:
                position = None
            if group_index is not None and position is not None:
                return self.delete_animation_action(group_index, position)
        return self.delete_animation_step()

    def delete_animation_action(self, group_index, position):
        """Take one action out of a group, and its drawing off the board with it.

        A move leaves nothing behind to delete -- where the players ended up is the
        group's own snapshot -- but an arrow, a sign or a label is a thing on the rink,
        and a timeline that still listed it after it was gone would be lying."""
        if not (0 <= group_index < len(self.animation_steps)):
            return
        group = self.animation_steps[group_index]
        actions = group.get("actions") or []
        if not (0 <= position < len(actions)):
            return
        description = actions.pop(position)
        if description.startswith("Remove "):
            # The row *is* the removal, so taking the row out puts the mark back into
            # the play rather than deleting anything.
            name = description[len("Remove "):]
            for cid, meta in list(self.drawn_items.items()):
                if (meta.get("anim_remove_group") == group_index
                        and self._drawn_label(cid) == name):
                    meta.pop("anim_remove_group", None)
            self.show_all_drawn_items()
            self._refresh_animation_list()
            return
        for cid, meta in list(self.drawn_items.items()):
            if (meta.get("anim_group") == group_index
                    and meta.get("anim_action") == description):
                try:
                    self.canvas.delete(cid)
                except Exception:
                    pass
                self.drawn_items.pop(cid, None)
                self.board_images.pop(cid, None)
        self._refresh_animation_list()

    def delete_animation_step(self):
        if not self.animation_steps:
            messagebox.showwarning("No groups", "There are no groups to delete.")
            return
        index = self.animation_playhead
        if 0 <= index < len(self.animation_steps):
            self.animation_steps.pop(index)
        # _renumber_animation_steps, not a blanket rename: a step called after the
        # action that made it ("Move LD, RW") must keep saying so. Renaming every
        # survivor to "Step n" made deleting one look like it had wiped the others.
        self._renumber_animation_steps()
        self.animation_playhead = max(0, min(self.animation_playhead,
                                             len(self.animation_steps) - 1))
        self._refresh_animation_list()

    def _display_label(self, label):
        """What the rink shows for this player: its tactical role once a formation has
        been applied, otherwise its internal label."""
        sid = self._get_sid_by_label(label)
        token = self.tokens.get(sid) if sid else None
        if token:
            return token.get("position") or token.get("label") or str(label)
        return str(label)

    def _px_delta_to_m(self, dx, dy):
        """A pixel offset expressed in rink metres, honouring the rink's orientation."""
        state = self._pitch_state()
        scale = state.get("scale") or 1.0
        if state.get("rotated"):
            return (-dy / scale, dx / scale)
        return (dx / scale, dy / scale)

    def _record_move_as_animation_step(self, label_moves, name=None):
        """Fold a completed move into the step being built.

        A step is a group of actions that happen *together*: everything recorded into
        one step plays simultaneously when the animation runs. Moves therefore
        accumulate into the current step rather than each starting a new one -- press
        **Add Step** to close the group and begin the next.

        When this is the first one, the board *before* the move is recorded as step 0 --
        the opening slide -- by winding the moved players back along their own deltas.
        Without it the sequence would begin at the end of the first move, with nothing
        to travel from."""
        if not label_moves:
            return None
        after = self._board_snapshot()
        record = {"created": [], "merged": None}

        if not self.animation_steps:
            # Group 0 is the board as it stands, not as it stood before this move.
            # Arranging the players is setup, not choreography: the animation begins
            # at the arrangement rather than playing back how it was reached.
            start = {"duration": max(0.1, float(self.step_time_var.get())),
                     "board": after, "name": "Group 0",
                     "actions": [name] if name else [],
                     "named": False, "closed": False}
            self.animation_steps.append(start)
            record["created"].append(start)
            self._renumber_animation_steps()
            self.animation_playhead = 0
            self._refresh_animation_list()
            return record

        current = self.animation_steps[-1]
        if current is None or current.get("closed"):
            step = {"duration": max(0.1, float(self.step_time_var.get())),
                    "board": after, "actions": [], "named": False,
                    "name": f"Step {len(self.animation_steps)}"}
            self.animation_steps.append(step)
            record["created"].append(step)
            current = step
        else:
            # Everything in this step moves at once, so the step holds where the board
            # ends up once all of its actions have been applied.
            record["merged"] = {"step": current, "before": self._step_state(current)}
            current["board"] = after

        if name:
            current.setdefault("actions", []).append(name)
            # Group 0 is the stage the animation begins at, so arranging the board
            # there is setup rather than choreography: it keeps its plain name, while
            # still listing what was done inside it.
            is_opening = self.animation_steps.index(current) == 0
            if not current.get("custom_name") and not is_opening:
                current["name"] = self._step_name_from_actions(current["actions"])
                current["named"] = True
        if record["merged"]:
            record["merged"]["after"] = self._step_state(current)

        self._renumber_animation_steps()
        self.animation_playhead = len(self.animation_steps) - 1
        self._refresh_animation_list()
        return record

    @staticmethod
    def _step_state(step):
        return {"board": step.get("board"),
                "actions": list(step.get("actions") or []),
                "name": step.get("name"),
                "named": step.get("named", False)}

    @staticmethod
    def _step_name_from_actions(actions):
        """One row for a group of simultaneous actions."""
        if not actions:
            return ""
        if len(actions) == 1:
            return actions[0]
        moves = [a[5:] for a in actions if a.startswith("Move ")]
        others = [a for a in actions if not a.startswith("Move ")]
        parts = []
        if moves:
            parts.append("Move " + ", ".join(moves))
        parts.extend(others)
        return " + ".join(parts)

    def _unrecord_animation_step(self, record):
        """Take back exactly what `_record_move_as_animation_step` did."""
        if not record:
            return
        merged = record.get("merged")
        if merged:
            merged["step"].update(merged["before"])
        for step in record.get("created", []):
            if step in self.animation_steps:
                self.animation_steps.remove(step)
        self._renumber_animation_steps()
        self.animation_playhead = max(0, len(self.animation_steps) - 1)
        self._refresh_animation_list()

    def _rerecord_animation_step(self, record):
        """Redo: put the group back the way the action left it."""
        if not record:
            return
        for step in record.get("created", []):
            if step not in self.animation_steps:
                self.animation_steps.append(step)
        merged = record.get("merged")
        if merged and merged.get("after"):
            merged["step"].update(merged["after"])
        self._renumber_animation_steps()
        self.animation_playhead = max(0, len(self.animation_steps) - 1)
        self._refresh_animation_list()

    def tag_items_with_group(self, ids, description=None):
        """Remember which group a drawing belongs to, so playback can bring it in with
        that group instead of having it sit on the rink from the first frame."""
        index = max(0, len(self.animation_steps) - 1)
        for cid in ids or ():
            meta = self.drawn_items.get(cid)
            if meta is None:
                continue
            meta["anim_group"] = index
            meta["anim_action"] = description
            coords = self.canvas.coords(cid)
            if coords:
                meta["full_coords"] = list(coords)

    def record_action_in_group(self, description):
        """Put a non-movement action -- an arrow, a sign, a label -- into the group
        being built, so the timeline shows everything that makes up a group and not
        only the players that moved."""
        if not description:
            return None
        record = {"created": [], "merged": None}
        if not self.animation_steps:
            group = {"duration": max(0.0, float(self.step_time_var.get())),
                     "board": self._board_snapshot(), "name": "Group 0",
                     "actions": [description], "named": False, "closed": False}
            self.animation_steps.append(group)
            record["created"].append(group)
        else:
            current = self.animation_steps[-1]
            if current.get("closed"):
                current = {"duration": max(0.0, float(self.step_time_var.get())),
                           "board": self._board_snapshot(),
                           "name": f"Group {len(self.animation_steps)}",
                           "actions": [], "named": False, "closed": False}
                self.animation_steps.append(current)
                record["created"].append(current)
            else:
                record["merged"] = {"step": current, "before": self._step_state(current)}
            current.setdefault("actions", []).append(description)
            if not current.get("custom_name") and self.animation_steps.index(current) > 0:
                current["name"] = self._step_name_from_actions(current["actions"])
                current["named"] = True
            if record["merged"]:
                record["merged"]["after"] = self._step_state(current)
        self._renumber_animation_steps()
        self.animation_playhead = len(self.animation_steps) - 1
        self._refresh_animation_list()
        return record

    def _renumber_animation_steps(self):
        """Keep the automatic names in step with the order. A step named after the
        action that made it ("Move A1", "Attack House 70% ...") keeps that name --
        renumbering must not throw away what the row actually says."""
        for position, step in enumerate(self.animation_steps):
            if step.get("named") or step.get("custom_name"):
                continue
            step["name"] = f"Group {position}"

    def move_animation_step(self, delta):
        """Move the selected step one place up or down the sequence."""
        if not self.animation_steps:
            messagebox.showwarning("No groups", "There are no groups to move.")
            return
        index = self.animation_playhead
        target = index + delta
        if not (0 <= index < len(self.animation_steps)) or \
                not (0 <= target < len(self.animation_steps)):
            return
        steps = self.animation_steps
        steps[index], steps[target] = steps[target], steps[index]
        self._renumber_animation_steps()
        self.animation_playhead = target
        # The order changed under it, so any playback in flight no longer means
        # anything -- start again rather than jumping mid-move.
        self.stop_animation(rewind=False)
        self._refresh_animation_list()

    def _anim_drag_start(self, event):
        """A group row is dragged to reorder the sequence; one of the action rows
        inside a group is dragged to move that action into another group."""
        item = self.anim_tree.identify_row(event.y)
        self._anim_drag_index = self._group_index_for(item)
        self._anim_drag_action = None
        if item and "a" in item[1:]:
            group_text, _, position = item[1:].partition("a")
            try:
                self._anim_drag_action = (int(group_text), int(position))
            except ValueError:
                self._anim_drag_action = None
        return None

    def _anim_drag_motion(self, event):
        """Reordering happens live for groups. An action is only re-homed on release,
        so it can be carried across the list without every row it passes claiming it."""
        if getattr(self, "_anim_drag_action", None) is not None:
            return None
        source = getattr(self, "_anim_drag_index", None)
        if source is None or not self.animation_steps:
            return None
        under = self._group_index_for(self.anim_tree.identify_row(event.y))
        if under is None:
            return None
        target = max(0, min(under, len(self.animation_steps) - 1))
        if target == source:
            return None
        step = self.animation_steps.pop(source)
        self.animation_steps.insert(target, step)
        self._anim_drag_index = target
        self.animation_playhead = target
        self._renumber_animation_steps()
        self._refresh_animation_list()
        return None

    def _anim_drag_end(self, event=None):
        dragged_action = getattr(self, "_anim_drag_action", None)
        if dragged_action is not None and event is not None:
            target = self._group_index_for(self.anim_tree.identify_row(event.y))
            self.move_action_to_group(dragged_action[0], dragged_action[1], target)
        elif getattr(self, "_anim_drag_index", None) is not None:
            self.stop_animation(rewind=False)
            self._refresh_animation_list()
        self._anim_drag_index = None
        self._anim_drag_action = None
        return None

    def move_action_to_group(self, source_index, position, target_index):
        """Move one action out of its group and into the group it was dropped on."""
        if target_index is None or source_index == target_index:
            return False
        if not (0 <= source_index < len(self.animation_steps)):
            return False
        if not (0 <= target_index < len(self.animation_steps)):
            return False
        source = self.animation_steps[source_index]
        actions = source.get("actions") or []
        if not (0 <= position < len(actions)):
            return False
        action = actions.pop(position)
        target = self.animation_steps[target_index]
        target.setdefault("actions", []).append(action)
        # Both ends get their heading rebuilt, unless they carry a name of their own --
        # dropping an action used to leave the group called after it.
        for index, group in ((source_index, source), (target_index, target)):
            if group.get("custom_name"):
                continue
            if index == 0 or not group.get("actions"):
                group["name"] = f"Group {index}"
                group["named"] = False
            else:
                group["name"] = self._step_name_from_actions(group["actions"])
                group["named"] = True
        # Whatever the action drew moves with it, so it appears in its new group.
        for meta in self.drawn_items.values():
            if meta.get("anim_action") == action and meta.get("anim_group") == source_index:
                meta["anim_group"] = target_index
        self.animation_playhead = target_index
        self._renumber_animation_steps()
        self._refresh_animation_list()
        return True

    def _refresh_animation_list(self):
        tree = getattr(self, "anim_tree", None)
        if tree is None:
            return
        # Remember which groups were folded, so a redraw does not spring them open.
        collapsed = {index for index, item in enumerate(tree.get_children(""))
                     if not tree.item(item, "open")}
        tree.delete(*tree.get_children(""))
        for index, group in enumerate(self.animation_steps):
            tags = ["group"]
            if index == self.animation_playhead:
                tags.append("playhead")     # the red row: playback starts here
            node = tree.insert("", tk.END, iid=f"g{index}",
                               text=f"{index}  {group['name']}",
                               values=(f"{group['duration']:.1f}s",),
                               open=index not in collapsed, tags=tuple(tags))
            for position, action in enumerate(group.get("actions") or []):
                tree.insert(node, tk.END, iid=f"g{index}a{position}",
                            text=f"   {action}", values=("",), tags=("action",))

    def _group_index_for(self, item):
        """The group a tree row belongs to -- the row itself, or its parent when the
        row is one of the actions inside a group."""
        if not item:
            return None
        if not item.startswith("g"):
            return None
        parent = self.anim_tree.parent(item)
        target = parent or item
        try:
            return int(target[1:].split("a")[0])
        except ValueError:
            return None

    def _on_anim_step_selected(self, _event=None):
        selection = self.anim_tree.selection()
        if not selection:
            return
        index = self._group_index_for(selection[0])
        if index is not None and index != self.animation_playhead:
            self.animation_playhead = index
            self._refresh_animation_list()

    def _rename_group(self, event=None):
        """Double-click a group to give it a name of your own."""
        item = self.anim_tree.identify_row(event.y) if event else None
        index = self._group_index_for(item) if item else self.animation_playhead
        if index is None or not (0 <= index < len(self.animation_steps)):
            return "break"
        group = self.animation_steps[index]

        window = tk.Toplevel(self.root)
        window.title("Group name")
        window.transient(self.root)
        window.configure(bg=self.C_PANEL)
        tk.Label(window, text=f"Name for group {index}:", bg=self.C_PANEL,
                 fg=self.C_TEXT, font=(self.UI_FONT, 9)).pack(padx=14, pady=(12, 6))
        value = tk.StringVar(value=group.get("name", ""))
        entry = tk.Entry(window, textvariable=value, font=(self.UI_FONT, 10), width=30,
                         relief=tk.FLAT, highlightthickness=1,
                         highlightbackground=self.C_BORDER, bg=self.C_SURFACE)
        entry.pack(padx=14)
        entry.focus_set()
        entry.select_range(0, tk.END)

        seconds = tk.StringVar(value=f"{group.get('duration', 2.0):.1f}")
        time_row = tk.Frame(window, bg=self.C_PANEL)
        time_row.pack(padx=14, pady=(8, 0), anchor="w")
        tk.Label(time_row, text="Seconds:", bg=self.C_PANEL, fg=self.C_TEXT,
                 font=(self.UI_FONT, 9)).pack(side=tk.LEFT)
        tk.Spinbox(time_row, from_=0.0, to=15.0, increment=0.1, width=5,
                   textvariable=seconds, font=(self.UI_FONT, 9)).pack(side=tk.LEFT, padx=6)

        def accept(_event=None):
            name = value.get().strip()
            if name:
                group["name"] = name
                # A name of your own is never overwritten by renumbering, nor by the
                # next action that joins this group.
                group["named"] = True
                group["custom_name"] = True
            try:
                group["duration"] = max(0.0, min(15.0, float(seconds.get())))
            except Exception:
                pass
            self._refresh_animation_list()
            window.destroy()

        buttons = tk.Frame(window, bg=self.C_PANEL)
        buttons.pack(padx=14, pady=12, fill=tk.X)
        cfg = {"font": (self.UI_FONT, 8), "relief": tk.FLAT, "bg": self.C_BTN,
               "fg": self.C_TEXT, "bd": 0, "highlightthickness": 1,
               "highlightbackground": self.C_BORDER, "padx": 10, "pady": 3,
               "cursor": "hand2"}
        tk.Button(buttons, text="OK", command=accept, **cfg).pack(side=tk.RIGHT, padx=3)
        tk.Button(buttons, text="Cancel", command=window.destroy,
                  **cfg).pack(side=tk.RIGHT)
        entry.bind("<Return>", accept)
        window.bind("<Escape>", lambda _e: window.destroy())
        self._make_modal(window)
        return "break"

    def set_group_time(self, index, seconds):
        """The time one group takes. Zero is allowed and means an instant cut."""
        if not (0 <= index < len(self.animation_steps)):
            return False
        try:
            self.animation_steps[index]["duration"] = max(0.0, min(15.0, float(seconds)))
        except Exception:
            return False
        self._refresh_animation_list()
        return True

    def _on_step_time_changed(self, _value=None):
        """The slider is the time for all steps: moving it retimes the whole sequence.
        A single step can still be given its own interval by double-clicking it."""
        duration = max(0.0, float(self.step_time_var.get()))
        for step in self.animation_steps:
            step["duration"] = duration
        self._refresh_animation_list()

    def _edit_step_time(self, _event=None):
        if not self.animation_steps:
            return
        index = self.animation_playhead
        if not (0 <= index < len(self.animation_steps)):
            return
        window = tk.Toplevel(self.root)
        window.title("Step time")
        window.transient(self.root)
        window.configure(bg=self.C_PANEL)
        tk.Label(window, text=f"Seconds for {self.animation_steps[index]['name']}:",
                 bg=self.C_PANEL, fg=self.C_TEXT, font=(self.UI_FONT, 9)).pack(padx=14, pady=(12, 6))
        value = tk.DoubleVar(value=self.animation_steps[index]["duration"])
        tk.Scale(window, from_=0.1, to=15.0, resolution=0.1, orient=tk.HORIZONTAL,
                 variable=value, length=240, bg=self.C_PANEL, fg=self.C_TEXT,
                 troughcolor=self.C_SURFACE, highlightthickness=0).pack(padx=14)

        def apply_and_close():
            self.animation_steps[index]["duration"] = max(0.1, float(value.get()))
            self._refresh_animation_list()
            window.destroy()

        tk.Button(window, text="OK", command=apply_and_close, font=(self.UI_FONT, 8),
                  relief=tk.FLAT, bg=self.C_BTN, fg=self.C_TEXT, bd=0,
                  highlightthickness=1, highlightbackground=self.C_BORDER,
                  padx=10, pady=3).pack(pady=12)

    def _animation_problem(self):
        """The reason the sequence cannot be played or exported, or None."""
        if len(self.animation_steps) < 2:
            return ("Nothing to animate",
                    "An animation needs at least two groups: the board as it stands, "
                    "and somewhere to move to.\n\nArrange the board, press Add Group, "
                    "then move things.")
        if sum(step.get("duration", 0) for step in self.animation_steps[1:]) <= 0:
            return ("No time set",
                    "Every group is set to zero seconds, so nothing would move.\n\n"
                    "Use the Time slider, or double-click a group to time it. A single "
                    "group at zero is fine -- it cuts straight to the next one.")
        return None

    def _step_positions(self, step):
        """label -> (mx, my) for one keyframe."""
        return {player["label"]: (player["mx"], player["my"])
                for player in (step.get("board") or {}).get("players", [])
                if player.get("label")}

    def _apply_animation_frame(self, from_step, to_step, fraction):
        """Put every player where it should be a `fraction` of the way between two
        keyframes, and bring in the drawings that belong to the group being entered.
        Positions are in rink metres, so this is correct at any window size and in
        either rink orientation."""
        try:
            to_index = self.animation_steps.index(to_step)
        except ValueError:
            to_index = None
        if getattr(self, "_attached_origins", None) is None:
            self._attached_origins = self._capture_attached_origins()
        start = self._step_positions(from_step)
        end = self._step_positions(to_step)
        for label, (sx, sy) in start.items():
            if label not in end:
                continue
            ex, ey = end[label]
            mx = sx + (ex - sx) * fraction
            my = sy + (ey - sy) * fraction
            sid = self._get_sid_by_label(label)
            token = self.tokens.get(sid) if sid else None
            if not token:
                continue
            centre = self._token_centre_px(token)
            if not centre:
                continue
            nx, ny = self._rink_to_px(mx, my)
            dx = nx - centre[0]
            dy = ny - centre[1]
            for item in self._token_items(token) + list(token.get("text_ids", [])):
                try:
                    self.canvas.move(item, dx, dy)
                except Exception:
                    pass

        # Where every player finishes this group, so an arrow that is still being drawn
        # can be laid out at its final length rather than sliding along behind them.
        finals = {}
        for label, (ex, ey) in end.items():
            sid = self._get_sid_by_label(label)
            if sid is not None:
                finals[sid] = self._rink_to_px(ex, ey)

        # The players have moved; the arrows snapped to them follow, and only then are
        # the drawings brought in -- the reveal reads the geometry the tracking has
        # just brought up to date.
        self._track_attached_lines(to_index, finals)
        if to_index is not None:
            self._apply_drawn_for_frame(to_index, fraction)

    def _capture_attached_origins(self):
        """Where every arrow snapped to a player sits before playback moves anything.

        Each frame is then worked out from the player's *total* travel since this
        moment. Nudging the arrow by one frame's delta instead would fight the reveal,
        which puts a drawing back to its recorded geometry on every frame, and the tip
        would fall further behind the player the longer the group ran."""
        origins = {}
        seen = set()
        for token in self.tokens.values():
            if not isinstance(token, dict):
                continue
            sid = token.get("shape_id")
            if sid is None or sid in seen:
                continue
            seen.add(sid)
            box = self.canvas.bbox(sid)
            if not box:
                continue
            centre = ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
            for at_end, key in ((False, "attached_lines_start"),
                                (True, "attached_lines_end")):
                for cid in token.get(key) or ():
                    coords = self.canvas.coords(cid)
                    if coords and len(coords) >= 4:
                        origins.setdefault(cid, []).append(
                            (sid, centre, at_end, list(coords)))
        return origins

    def _track_attached_lines(self, to_index=None, finals=None):
        """Keep every attached arrow pointing at its player.

        An arrow snapped to a player is aimed *at them*, so it has to stay aimed while
        they run: on the board a plain drag deliberately lets go of the arrow -- that
        is repositioning, not choreography -- but in the animation the tip must not be
        left behind.

        An arrow that is being drawn *in this group* is a different case. It is laid
        out at the geometry it will finish with, so it grows from its final tail
        position along its final path. Tracking it live instead made the whole arrow
        slide across the rink as it grew, because both ends were moving at once."""
        finals = finals or {}
        for cid, entries in (getattr(self, "_attached_origins", None) or {}).items():
            meta = self.drawn_items.get(cid)
            if meta is None:
                continue
            being_drawn = to_index is not None and meta.get("anim_group") == to_index
            for sid, centre, at_end, coords in entries:
                token = self.tokens.get(sid)
                box = self.canvas.bbox(sid) if token else None
                if being_drawn and sid in finals:
                    target = finals[sid]
                elif box:
                    target = ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
                else:
                    continue
                dx = target[0] - centre[0]
                dy = target[1] - centre[1]
                axis = self._drawing_axis(cid, centre)
                if not axis:
                    continue
                moved = self._stretched_points(coords, axis[0], axis[1], dx, dy)
                if moved is None:
                    continue
                try:
                    self.canvas.coords(cid, *moved)
                except Exception:
                    continue
                if meta.get("full_coords"):
                    meta["full_coords"] = list(moved)

    def _apply_drawn_for_frame(self, to_index, fraction):
        """Arrows, boxes, signs and labels take part in the animation: anything drawn in
        a later group is hidden until the animation reaches it, and a line belonging to
        the group being entered is drawn on over that group's time rather than simply
        appearing."""
        for cid, meta in list(self.drawn_items.items()):
            group = meta.get("anim_group")
            gone = meta.get("anim_remove_group")
            if gone is not None and (to_index > gone
                                     or (to_index == gone and fraction > 0)):
                # Removed by Backspace: it stays on the board while the play is being
                # built, and goes when the group that removes it comes up.
                try:
                    self.canvas.itemconfig(cid, state=tk.HIDDEN)
                except Exception:
                    pass
                continue
            if group is None:
                continue                      # drawn outside the animation: always on
            try:
                if group < to_index:
                    self.canvas.itemconfig(cid, state=tk.NORMAL)
                    self._restore_full_coords(cid, meta)
                elif group > to_index:
                    self.canvas.itemconfig(cid, state=tk.HIDDEN)
                else:
                    self._reveal_drawn_item(cid, meta, fraction)
            except Exception:
                pass

    def _sync_full_coords(self):
        """Re-read the full geometry of every drawing that takes part in the animation.

        full_coords is what playback draws a line back to once its group has fully
        opened, and what a stopped board is restored to. It is captured when the
        drawing is made, so anything that moved it afterwards -- a player dragging an
        attached arrow along, a window resize re-projecting the board -- left it
        describing a shape that no longer exists, and the next Play or Stop snapped
        the arrow back to its old length. Called wherever the board settles, never
        while the animation is running: mid-playback a line is deliberately part
        drawn, and recording that would make the shortening permanent."""
        if self.animation_job is not None or getattr(self, "_animation_cursor", None):
            return
        for cid, meta in list(self.drawn_items.items()):
            if meta.get("anim_group") is None:
                continue
            try:
                coords = self.canvas.coords(cid)
            except Exception:
                continue
            if coords:
                meta["full_coords"] = list(coords)

    def _restore_full_coords(self, cid, meta):
        full = meta.get("full_coords")
        if full and self.canvas.type(cid) in ("line", "polygon"):
            current = self.canvas.coords(cid)
            if len(current) != len(full) or current != full:
                self.canvas.coords(cid, *full)

    def _reveal_drawn_item(self, cid, meta, fraction):
        """A line is drawn on from its start; anything else appears as its group opens."""
        if fraction <= 0:
            self.canvas.itemconfig(cid, state=tk.HIDDEN)
            return
        self.canvas.itemconfig(cid, state=tk.NORMAL)
        full = meta.get("full_coords")
        if not full or self.canvas.type(cid) != "line" or len(full) < 4:
            return
        if fraction >= 1.0:
            self.canvas.coords(cid, *full)
            return
        # Walk the line's own length and stop where the fraction falls, so a curve
        # follows its curve rather than its chord.
        points = [(full[i], full[i + 1]) for i in range(0, len(full) - 1, 2)]
        points = self._curve_points(cid, points)
        spans = [math.hypot(b[0] - a[0], b[1] - a[1])
                 for a, b in zip(points, points[1:])]
        total = sum(spans)
        if total <= 0:
            return
        wanted = total * fraction
        drawn = [points[0]]
        for (a, b), span in zip(zip(points, points[1:]), spans):
            if wanted >= span:
                drawn.append(b)
                wanted -= span
                continue
            share = wanted / span if span else 0
            drawn.append((a[0] + (b[0] - a[0]) * share, a[1] + (b[1] - a[1]) * share))
            break
        if len(drawn) >= 2:
            self.canvas.coords(cid, *[value for point in drawn for value in point])

    def _curve_points(self, cid, points):
        """A bend as a run of points along the curve Tk actually draws.

        A bend is stored as start, control, end -- three points. Revealing it along
        those would draw a straight line to the control point and only then bend,
        which is not how the arrow looks. Sampling the quadratic through the control
        point gives the drawn shape, so it appears the way it was drawn."""
        if len(points) != 3:
            return points
        try:
            if not self.canvas.itemcget(cid, "smooth") in ("1", "true", "bezier"):
                return points
        except Exception:
            return points
        (x0, y0), (cx, cy), (x1, y1) = points
        samples = []
        steps = 24
        for step in range(steps + 1):
            t = step / steps
            inv = 1.0 - t
            samples.append((inv * inv * x0 + 2 * inv * t * cx + t * t * x1,
                            inv * inv * y0 + 2 * inv * t * cy + t * t * y1))
        return samples

    def show_all_drawn_items(self):
        """Put every drawing back on the board at full length -- what the rink looks
        like when the animation is not running."""
        # The board is at rest, so the next run captures where the arrows are then,
        # not where they were before this one.
        self._attached_origins = None
        for cid, meta in list(self.drawn_items.items()):
            try:
                self.canvas.itemconfig(cid, state=tk.NORMAL)
                self._restore_full_coords(cid, meta)
            except Exception:
                pass

    def play_animation(self):
        problem = self._animation_problem()
        if problem:
            messagebox.showwarning(*problem)
            return
        if self.animation_playing:
            return
        self.animation_playing = True
        # Resume from the playhead: whatever red row is showing is where it starts.
        # Sitting on the final step -- where Add Step leaves it -- means there is
        # nothing after it to move to, so that rewinds to the opening slide instead of
        # finishing the moment it starts.
        if getattr(self, "_animation_cursor", None) is None:
            start_index = max(0, self.animation_playhead)
            if start_index >= len(self.animation_steps) - 1:
                start_index = 0
            self._animation_cursor = (start_index, 0.0)
        self._animation_tick()

    def _animation_tick(self):
        if not self.animation_playing:
            return
        index, elapsed = self._animation_cursor
        if index >= len(self.animation_steps) - 1:
            self.stop_animation(rewind=False)
            return
        step = self.animation_steps[index + 1]
        duration = float(step.get("duration", 1.0))
        # A group set to zero seconds is a cut: jump straight to it.
        fraction = 1.0 if duration <= 0 else min(1.0, elapsed / duration)
        self._apply_animation_frame(self.animation_steps[index], step, fraction)
        self.animation_playhead = index if fraction < 1.0 else index + 1
        self._refresh_animation_list()

        if fraction >= 1.0:
            self._animation_cursor = (index + 1, 0.0)
        else:
            self._animation_cursor = (index, elapsed + 1.0 / self.ANIMATION_FPS)
        self.animation_job = self.root.after(int(1000 / self.ANIMATION_FPS),
                                             self._animation_tick)

    def pause_animation(self):
        """Stop where it is; Play carries on from the same spot."""
        self.animation_playing = False
        if self.animation_job is not None:
            try:
                self.root.after_cancel(self.animation_job)
            except Exception:
                pass
            self.animation_job = None

    def stop_animation(self, rewind=True):
        """Stop and go back to the opening slide."""
        self.pause_animation()
        self._animation_cursor = None
        if rewind and self.animation_steps:
            self._apply_animation_frame(self.animation_steps[0],
                                        self.animation_steps[0], 0.0)
            self.animation_playhead = 0
        # Last, not first: the rewind frame above hides anything belonging to a later
        # group, and a stopped board is a static board with everything on it.
        self.show_all_drawn_items()
        self._refresh_animation_list()

    # What Export can write. A video needs a frame writer -- imageio if it is
    # installed, otherwise OpenCV -- while the stills and the GIF only need Pillow and
    # the canvas capture. The four-character code is what OpenCV wants for the codec.
    VIDEO_FORMATS = {
        "MP4":  (".mp4",  "mp4v"),
        "WebM": (".webm", "VP80"),
        "AVI":  (".avi",  "MJPG"),
        "MOV":  (".mov",  "mp4v"),
    }
    IMAGE_FORMATS = {"PNG": ".png", "JPEG": ".jpg"}
    MOTION_FORMATS = ("GIF",) + tuple(VIDEO_FORMATS)

    def export_animation(self):
        """The one Export entry point: pick a format, and for stills the groups to
        save, then write them."""
        window = tk.Toplevel(self.root)
        window.title("Export")
        window.transient(self.root)
        window.configure(bg=self.C_PANEL)
        window.resizable(False, False)

        chosen = tk.StringVar(value="GIF")
        body = tk.Frame(window, bg=self.C_PANEL)
        body.pack(padx=14, pady=12, fill=tk.BOTH, expand=True)

        left = tk.Frame(body, bg=self.C_PANEL)
        left.pack(side=tk.LEFT, anchor="n", padx=(0, 18))
        tk.Label(left, text="Format", bg=self.C_PANEL, fg=self.C_TEXT,
                 font=(self.UI_FONT, 9, "bold")).pack(anchor="w")
        tk.Label(left, text="Moving", bg=self.C_PANEL, fg=self.C_MUTED,
                 font=(self.UI_FONT, 8)).pack(anchor="w", pady=(6, 0))
        for name in self.MOTION_FORMATS:
            tk.Radiobutton(left, text=name, value=name, variable=chosen,
                           bg=self.C_PANEL, fg=self.C_TEXT, anchor="w",
                           selectcolor=self.C_SURFACE,
                           font=(self.UI_FONT, 9)).pack(anchor="w")
        tk.Label(left, text="Still", bg=self.C_PANEL, fg=self.C_MUTED,
                 font=(self.UI_FONT, 8)).pack(anchor="w", pady=(6, 0))
        for name in self.IMAGE_FORMATS:
            tk.Radiobutton(left, text=name, value=name, variable=chosen,
                           bg=self.C_PANEL, fg=self.C_TEXT, anchor="w",
                           selectcolor=self.C_SURFACE,
                           font=(self.UI_FONT, 9)).pack(anchor="w")

        right = tk.Frame(body, bg=self.C_PANEL)
        right.pack(side=tk.LEFT, anchor="n", fill=tk.BOTH, expand=True)
        tk.Label(right, text="Groups to save", bg=self.C_PANEL, fg=self.C_TEXT,
                 font=(self.UI_FONT, 9, "bold")).pack(anchor="w")
        hint = tk.Label(right, bg=self.C_PANEL, fg=self.C_MUTED, justify="left",
                        font=(self.UI_FONT, 8), wraplength=230,
                        text="A moving export always runs the whole sequence.")
        hint.pack(anchor="w", pady=(2, 4))
        listbox = tk.Listbox(right, selectmode=tk.EXTENDED, height=8, width=30,
                             exportselection=False, font=(self.UI_FONT, 9))
        listbox.pack(fill=tk.BOTH, expand=True)
        names = self._group_export_names()
        for index, name in enumerate(names):
            listbox.insert(tk.END, name)
        listbox.selection_set(0, tk.END)

        def formats_changed(*_):
            still = chosen.get() in self.IMAGE_FORMATS
            listbox.config(state=tk.NORMAL if still else tk.DISABLED)
            hint.config(text=("One image per group you pick."
                              if still
                              else "A moving export always runs the whole sequence."))
        chosen.trace_add("write", formats_changed)
        formats_changed()

        result = {}

        def go():
            result["format"] = chosen.get()
            result["groups"] = [int(i) for i in listbox.curselection()] or [0]
            window.destroy()

        buttons = tk.Frame(window, bg=self.C_PANEL)
        buttons.pack(fill=tk.X, padx=14, pady=(0, 12))
        tk.Button(buttons, text="Export", command=go, font=(self.UI_FONT, 9),
                  relief=tk.FLAT, bg=self.C_ACCENT, fg=self.C_ACCENT_FG, bd=0,
                  padx=14, pady=3).pack(side=tk.RIGHT, padx=(6, 0))
        tk.Button(buttons, text="Cancel", command=window.destroy,
                  font=(self.UI_FONT, 9), relief=tk.FLAT, bg=self.C_BTN,
                  fg=self.C_TEXT, bd=0, padx=14, pady=3).pack(side=tk.RIGHT)

        self._make_modal(window)
        self._wait_modal(window)
        if result:
            self.run_export(result["format"], result["groups"])

    def _group_export_names(self):
        """One line per group for the export list, or the live board when there are no
        groups yet -- a still of what is on screen is worth exporting either way."""
        if not self.animation_steps:
            return ["Board as it stands"]
        return [f"{index}. {step.get('name') or f'Group {index}'}"
                for index, step in enumerate(self.animation_steps)]

    def run_export(self, fmt="GIF", groups=None):
        """Write the export. Split from the dialog so it can be driven directly."""
        if fmt in self.IMAGE_FORMATS:
            # Not `groups or [0]`: an empty list is a caller saying "none", which is a
            # thing to refuse rather than quietly turn into group 0.
            return self._export_stills(fmt, [0] if groups is None else groups)
        if fmt in self.MOTION_FORMATS:
            return self._export_motion(fmt)
        return False

    def _export_motion(self, fmt):
        """The whole sequence, as a GIF or a video."""
        problem = self._animation_problem()
        if problem:
            messagebox.showwarning(*problem)
            return False
        extension = ".gif" if fmt == "GIF" else self.VIDEO_FORMATS[fmt][0]
        path = filedialog.asksaveasfilename(
            defaultextension=extension,
            filetypes=[(fmt, f"*{extension}")])
        if not path:
            return False

        self.pause_animation()
        restore = self._board_snapshot()
        try:
            frames = self._render_animation_frames()
            if frames is None:
                return False
            if not frames:
                messagebox.showwarning("Nothing to export", "No frames were produced.")
                return False
            if fmt == "GIF":
                frames[0].save(path, save_all=True, append_images=frames[1:],
                               duration=int(1000 / self.ANIMATION_FPS), loop=0,
                               optimize=True)
            elif not self._write_video(path, frames, self.VIDEO_FORMATS[fmt][1]):
                return False
            messagebox.showinfo(
                "Exported",
                f"Saved {len(frames)} frames to\n{path}\n\n"
                "Note: a watermark image is not included -- the canvas capture Tk "
                "provides covers shapes and text only.")
            return True
        except Exception as error:
            messagebox.showerror("Export failed", f"Could not write the {fmt}:\n{error}")
            return False
        finally:
            self._restore_board(restore)
            self.show_all_drawn_items()
            self.canvas.update()

    def _render_animation_frames(self):
        """Walk the board through the whole sequence, capturing every frame.

        None means the board could not be captured at all, which is a different thing
        from a sequence that produced no frames."""
        frames = []
        for index in range(len(self.animation_steps) - 1):
            step = self.animation_steps[index + 1]
            count = max(1, int(round(float(step.get("duration", 1.0))
                                     * self.ANIMATION_FPS)))
            for frame in range(count):
                self._apply_animation_frame(self.animation_steps[index], step,
                                            (frame + 1) / count)
                self.canvas.update()
                captured = self._capture_canvas()
                if captured is None:
                    messagebox.showerror(
                        "Export failed",
                        "The board could not be captured. Exporting needs Ghostscript "
                        "installed (the 'gs' command).")
                    return None
                frames.append(captured)
        return frames

    def _write_video(self, path, frames, codec):
        """Frames to a video file, through whichever writer this machine has.

        imageio first because it names codecs rather than four-character tags, then
        OpenCV, which ships its own FFmpeg on most installs. Neither is a dependency
        of the application: without them the GIF and the stills still work."""
        size = (frames[0].width, frames[0].height)
        try:
            import imageio.v2 as imageio            # noqa: PLC0415
        except Exception:
            imageio = None
        if imageio is not None:
            try:
                with imageio.get_writer(path, fps=self.ANIMATION_FPS) as writer:
                    for frame in frames:
                        writer.append_data(self._frame_array(frame))
                return True
            except Exception:
                pass                                # fall through to OpenCV
        try:
            import cv2                              # noqa: PLC0415
        except Exception:
            messagebox.showerror(
                "Export failed",
                "Writing a video needs either imageio or OpenCV.\n\n"
                "Install one of them:\n"
                "    pip install imageio imageio-ffmpeg\n"
                "    pip install opencv-python\n\n"
                "GIF, PNG and JPEG export work without either.")
            return False
        writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*codec),
                                 self.ANIMATION_FPS, size)
        if not writer.isOpened():
            writer.release()
            messagebox.showerror(
                "Export failed",
                f"This machine's video encoder would not open a {codec} stream.\n\n"
                "Try another format, or export a GIF.")
            return False
        for frame in frames:
            # PIL is RGB, OpenCV wants BGR.
            writer.write(self._frame_array(frame)[:, :, ::-1])
        writer.release()
        return True

    @staticmethod
    def _frame_array(image):
        """A captured frame as the array both video writers expect. numpy is imported
        here rather than at the top: nothing else in the application needs it."""
        import numpy                                # noqa: PLC0415
        return numpy.asarray(image.convert("RGB"))

    def _export_stills(self, fmt, groups):
        """One image per chosen group: the board as it stands when that group is done."""
        extension = self.IMAGE_FORMATS[fmt]
        wanted = sorted({index for index in groups
                         if 0 <= index < max(1, len(self.animation_steps))})
        if not wanted:
            messagebox.showwarning("Nothing to export", "Pick at least one group.")
            return False
        path = filedialog.asksaveasfilename(defaultextension=extension,
                                            filetypes=[(fmt, f"*{extension}")])
        if not path:
            return False

        stem, _ = os.path.splitext(path)
        self.pause_animation()
        restore = self._board_snapshot()
        written = []
        try:
            for index in wanted:
                if self.animation_steps:
                    step = self.animation_steps[index]
                    self._apply_animation_frame(self.animation_steps[max(index - 1, 0)],
                                                step, 1.0)
                self.canvas.update()
                captured = self._capture_canvas()
                if captured is None:
                    messagebox.showerror(
                        "Export failed",
                        "The board could not be captured. Exporting needs Ghostscript "
                        "installed (the 'gs' command).")
                    return False
                # One group keeps the name that was typed; several are numbered, so a
                # sequence of stills lands in order in the folder.
                target = path if len(wanted) == 1 else f"{stem}_{index:02d}{extension}"
                if fmt == "JPEG":
                    captured = captured.convert("RGB")
                captured.save(target, fmt)
                written.append(target)
            messagebox.showinfo(
                "Exported",
                f"Saved {len(written)} image(s):\n" + "\n".join(written[:8]) +
                ("\n..." if len(written) > 8 else "") +
                "\n\nNote: a watermark image is not included -- the canvas capture Tk "
                "provides covers shapes and text only.")
            return True
        except Exception as error:
            messagebox.showerror("Export failed", f"Could not write the image:\n{error}")
            return False
        finally:
            self._restore_board(restore)
            self.show_all_drawn_items()
            self.canvas.update()

    def _capture_canvas(self):
        """The canvas as a PIL image, via PostScript. Tk cannot hand over a bitmap of
        a canvas directly, and screen grabs need an unobstructed window."""
        try:
            width = self.canvas.winfo_width()
            height = self.canvas.winfo_height()
            postscript = self.canvas.postscript(colormode="color", x=0, y=0,
                                                width=width, height=height,
                                                pagewidth=width - 1,
                                                pageheight=height - 1)
            image = Image.open(io.BytesIO(postscript.encode("utf-8")))
            image.load(scale=1)
            return image.convert("RGB")
        except Exception:
            return None

    def _compact_commands(self, records):
        """Drop instructions that no longer say anything, and merge the ones that
        repeat. Returns the shortened list and how many entries went.

        `records` pairs each serialised instruction with the animation group it was
        recorded in. The group is what makes this safe: two identical instructions in
        different groups are two moments of the play, while two in the same group are
        one moment recorded twice, and only the last of them was ever seen.

        Three things go:
          * a formation superseded by a later formation for the same team in the
            same group -- applying the first was undone by the second before anyone
            saw it;
          * moves of players that have since been taken off the board, which replay
            as no-ops;
          * consecutive moves within one group, which are folded into a single
            displacement per player."""
        live = {token.get("label") for token in self.tokens.values()}
        kept = []
        for entry, group in records:
            entry = dict(entry)
            if entry.get("type") == "move_tokens":
                moves = {label: delta
                         for label, delta in (entry.get("moves") or {}).items()
                         if label in live}
                if not moves:
                    continue                    # every player in it has gone
                entry["moves"] = moves
            kept.append([entry, group])

        # A later formation for the same team in the same group wins.
        seen_tactics = set()
        survivors = []
        for entry, group in reversed(kept):
            if entry.get("type") == "tactic":
                key = (group, entry.get("team"))
                if key in seen_tactics:
                    continue
                seen_tactics.add(key)
            survivors.append([entry, group])
        survivors.reverse()

        # Consecutive moves in one group are one move.
        merged = []
        for entry, group in survivors:
            if (merged and entry.get("type") == "move_tokens"
                    and merged[-1][0].get("type") == "move_tokens"
                    and merged[-1][1] == group):
                target = merged[-1][0]["moves"]
                for label, (dx, dy) in entry["moves"].items():
                    old_dx, old_dy = target.get(label, (0, 0))
                    target[label] = (old_dx + dx, old_dy + dy)
                continue
            merged.append([entry, group])

        return [entry for entry, _ in merged], len(records) - len(merged)

    def save_macro(self):
        if not self.undo_stack and not self.tokens:
            messagebox.showinfo("Empty", "Nothing on the board to save.")
            return
        filepath = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON Files", "*.json")])
        if filepath:
            records = [(cmd.serialize(), getattr(cmd, "animation_group", 0))
                       for cmd in self.undo_stack if hasattr(cmd, "serialize")]
            commands = [entry for entry, _ in records]
            # Offered, never imposed: a recording is a log of what was done, and some
            # people want it kept that way. The question is only asked when there is
            # something to gain.
            tidied, removed = self._compact_commands(records)
            if removed and messagebox.askyesno(
                    "Tidy the recording?",
                    f"{removed} of the {len(commands)} recorded instructions are "
                    "superseded by later ones in the same animation group, or refer "
                    "to players that are no longer on the board.\n\n"
                    "Leave them out of the file?\n\n"
                    "The play is saved either way. The command log is history, not "
                    "what the file is rebuilt from, so tidying it cannot change what "
                    "comes back -- it only makes the record easier to read."):
                commands = tidied
            board = self._board_snapshot()
            # A watermark entry embeds the whole image, so only the last one is kept --
            # the earlier ones are superseded anyway -- and the board's copy is dropped
            # when a command already carries it. Otherwise a couple of edits would
            # write the same few hundred kB into the file three or four times over.
            watermark_at = [i for i, entry in enumerate(commands)
                            if entry.get("type") == "watermark"]
            if watermark_at:
                keep = watermark_at[-1]
                commands = [entry for i, entry in enumerate(commands)
                            if entry.get("type") != "watermark" or i == keep]
                board.pop("watermark", None)
            # Version 3 is the play itself, not a recipe for re-enacting it: where
            # every mark sits in rink metres, the whole timeline with each group's
            # duration and the second it starts at, and the board each group ends on.
            # The command log is kept alongside it as the history of how the play was
            # built -- but nothing about reopening the file depends on replaying it any
            # more, which is what used to scatter the arrows and collapse every group
            # into one.
            order = list(self.drawn_items)
            data = {
                "version": 3,
                "app": "Floorball Tactics Studio",
                "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "board": board,
                "drawings": self._drawings_snapshot(),
                "attachments": self._attachments_snapshot(order),
                "animation": self._animation_snapshot(),
                "commands": commands,
            }
            with open(filepath, "w") as f:
                json.dump(data, f, indent=4)
            messagebox.showinfo("Success", "Macro saved successfully!")

    def load_macro(self):
        filepath = filedialog.askopenfilename(filetypes=[("JSON Files", "*.json")])
        if not filepath:
            return
        try:
            with open(filepath, "r") as f:
                data = json.load(f)

            # Version 1 files were a bare list of commands; version 2 adds the board;
            # version 3 adds the drawings and the timeline, and is loaded from those
            # rather than by replaying anything.
            if isinstance(data, dict):
                commands, board = data.get("commands", []), data.get("board")
            else:
                commands, board = data, None

            if isinstance(data, dict) and int(data.get("version", 0)) >= 3:
                return self._load_play(data)

            for cmd_data in commands:
                ctype = cmd_data.get("type")
                if ctype == "move_tokens":
                    self.push_command(MoveTokensCommand(self, cmd_data["moves"]))
                elif ctype == "draw":
                    self.push_command(DrawLineCommand(self, cmd_data["tool"], cmd_data["x1"], cmd_data["y1"], cmd_data["x2"], cmd_data["y2"], cmd_data.get("extra")))
                elif ctype == "group":
                    self.push_command(GroupCommand(self, cmd_data["labels"], cmd_data.get("is_ungroup", False)))
                elif ctype == "lock":
                    self.push_command(LockCommand(self, cmd_data["labels"], cmd_data["lock_state"]))
                elif ctype == "rotate_tokens":
                    self.push_command(RotateTokensCommand(self, cmd_data.get("labels", []),
                                                          cmd_data.get("degrees", 45)))
                elif ctype == "tactic":
                    self.push_command(ApplyTacticCommand(
                        self, cmd_data.get("team"), cmd_data.get("formation", ""),
                        cmd_data.get("percent", 50), cmd_data.get("moves", {}),
                        cmd_data.get("positions", {})))
                elif ctype == "watermark":
                    self.push_command(SetWatermarkCommand(
                        self, cmd_data.get("watermark"), self._watermark_snapshot()))
                elif ctype == "delete_tokens":
                    for spec in cmd_data.get("players", []):
                        sid = self._get_sid_by_label(spec.get("label"))
                        token = self.tokens.get(sid) if sid else None
                        if token:
                            self._delete_token(token)
                    self._refresh_roster_counts()
                elif ctype == "pitch_parts":
                    self.push_command(HidePitchPartsCommand(
                        self, cmd_data.get("keys", []), cmd_data.get("hide", True)))

            # Applied last so the recorded positions win over anything the replayed
            # commands did with their pixel deltas.
            if board:
                self._restore_board(board)

        except Exception as e:
            messagebox.showerror("Error", f"Failed to load macro:\n{e}")

    def _load_play(self, data):
        """Open a version 3 file: the board, the marks, the timeline, as saved.

        Nothing is replayed. The command log in the file is history, kept so a play
        can still be read as a list of what was done, but the play that comes back is
        the one that was saved -- same positions, same arrows, same groups, same
        times."""
        try:
            self.stop_animation()
        except Exception:
            pass
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.action_steps.clear()
        try:
            self.steps_listbox.delete(0, tk.END)
        except Exception:
            pass
        self.animation_steps = []
        self.animation_playhead = 0

        board = data.get("board") or {}
        # The rink first: the metres in the file describe positions on that field.
        if board:
            self._restore_board(board)
        restored = self._restore_drawings(data.get("drawings") or [])
        self._restore_attachments(data.get("attachments") or {}, restored)
        self._restore_animation(data.get("animation") or {})
        for entry in data.get("commands") or ():
            description = entry.get("type")
            if description:
                self.action_steps.append(str(description))
                try:
                    self.steps_listbox.insert(tk.END, str(description))
                except Exception:
                    pass
        self.show_all_drawn_items()
        self._refresh_animation_list()
        self._update_indicators()
        return True

    # ----------------------
    # UI / icons
    # ----------------------
    def _create_action_icon(self, name):
        asset_map = {
            "pass": "standard_arrow_wo_bg.png",
            "shot": "dashed_arrow_wo_bg.png",
            "dribble": "wiggel_arrow_wo_bg.png",
            "run": "double_arrow_wo_bg.png",
        }
        asset_name = asset_map.get(name)
        if asset_name:
            assets_dir = pathlib.Path(__file__).resolve().parent / "img" / "arrows"
            asset_path = assets_dir / asset_name
            try:
                img = Image.open(asset_path).convert("RGBA")
                img = img.resize((28, 18), Image.Resampling.LANCZOS)
                return ImageTk.PhotoImage(img)
            except Exception:
                pass

        img = Image.new("RGBA", (20, 14), (255,255,255,0))
        d = ImageDraw.Draw(img)
        if name == "pass":
            d.line([2,7,14,7], fill="#000000", width=2)
            d.polygon([16,7,12,4,12,10], fill="#000000")
        elif name == "shot":
            d.line([2,7,16,7], fill="#000000", width=3)
            d.polygon([16,7,12,3,12,11], fill="#000000")
        elif name == "dribble":
            pts = [(2,9),(6,5),(10,9),(14,5),(18,9)]
            d.line(pts, fill="#000000", width=2, joint="curve")
            d.polygon([18,9,14,6,14,12], fill="#000000")
        elif name == "run":
            d.line([2,7,14,7], fill="#000000", width=2)
            d.polygon([14,7,10,3,10,11], fill="#000000")
        return ImageTk.PhotoImage(img)

    def _create_sign_pictogram(self, stype):
        img = Image.new("RGBA", (24, 20), (240, 240, 240, 0))
        draw = ImageDraw.Draw(img)
        st_lower = stype.lower()
        if st_lower == "goal":
            draw.rectangle([10, 4, 18, 16], outline="black", fill="black")
            draw.rectangle([4, 2, 10, 18], outline="black", width=1)
        elif st_lower == "x":
            draw.line([4, 4, 20, 16], fill="black", width=2)
            draw.line([4, 16, 20, 4], fill="black", width=2)
        elif st_lower in ("ball", "dot", "circle"):
            draw.ellipse([8, 6, 16, 14], fill="black", outline="black")
        elif st_lower == "square":
            draw.rectangle([4, 2, 20, 18], outline="black", width=2)
        elif st_lower == "triangle":
            draw.polygon([(12, 2), (4, 18), (20, 18)], outline="black", width=2)
        elif st_lower == "plus":
            draw.line([12, 2, 12, 18], fill="black", width=2)
            draw.line([4, 10, 20, 10], fill="black", width=2)
        return ImageTk.PhotoImage(img)

    # ----------------------
    # Tool selection
    # ----------------------
    def set_tool(self, tool_name):
        if self.active_tool == tool_name:
            self.active_tool = None
        else:
            self.active_tool = tool_name
        self.bend_control_point = None
        self._update_indicators()
        mode_desc = f"Active Tool: {self.active_tool.capitalize()}" if self.active_tool else "Active Mode: Select & Move"
        self.root.title(f"Floorball Tactics Studio - {mode_desc}")

    # ----------------------
    # UI construction
    # ----------------------
    def _setup_styles(self):
        """Theme the ttk widgets to match the palette. clam is used because the
        default themes ignore most colour options on Linux."""
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Toolbar.TLabelframe", background=self.C_PANEL,
                        bordercolor=self.C_BORDER, relief="solid", borderwidth=1)
        style.configure("Toolbar.TLabelframe.Label", background=self.C_PANEL,
                        foreground=self.C_MUTED, font=(self.UI_FONT, 8, "bold"))
        style.configure("Toolbar.TCombobox", fieldbackground=self.C_SURFACE,
                        background=self.C_SURFACE, bordercolor=self.C_BORDER,
                        arrowcolor=self.C_TEXT, foreground=self.C_TEXT)

    def _attach_hover(self, button):
        """Light hover feedback. Skips the colour swatches, whose background is the
        chosen colour rather than a surface, and never fights the active highlight."""
        if getattr(button, "_is_swatch", False):
            return

        def on_enter(_event):
            if button.cget("bg") != self.C_ACCENT:
                button._rest_bg = button.cget("bg")
                button.config(bg=self.C_BTN_HOVER)

        def on_leave(_event):
            if button.cget("bg") == self.C_BTN_HOVER:
                button.config(bg=getattr(button, "_rest_bg", self.C_BTN))

        button.bind("<Enter>", on_enter, add="+")
        button.bind("<Leave>", on_leave, add="+")

    def _polish_buttons(self, widget):
        for child in widget.winfo_children():
            if isinstance(child, tk.Button):
                self._attach_hover(child)
            self._polish_buttons(child)

    def _setup_ui(self):
        menubar = tk.Menu(self.root)
        edit_menu = tk.Menu(menubar, tearoff=0)
        edit_menu.add_command(label="Copy", command=self.copy_selection, accelerator="Ctrl+C")
        edit_menu.add_command(label="Cut", command=self.cut_selection, accelerator="Ctrl+X")
        edit_menu.add_command(label="Paste", command=self.paste_clipboard, accelerator="Ctrl+V")
        edit_menu.add_separator()
        edit_menu.add_command(label="Select All", command=self.select_all, accelerator="Ctrl+A")
        menubar.add_cascade(label="Edit", menu=edit_menu)

        view_menu = tk.Menu(menubar, tearoff=0)
        view_menu.add_command(label="Rotate Rink Horizontal", command=lambda: self.rotate_rink("horizontal"))
        view_menu.add_command(label="Rotate Rink Vertical", command=lambda: self.rotate_rink("vertical"))
        view_menu.add_separator()
        view_menu.add_command(label="Zoom In\tCtrl +", command=self.zoom_in)
        view_menu.add_command(label="Zoom Out\tCtrl -", command=self.zoom_out)
        view_menu.add_command(label="Reset Zoom\tCtrl 0", command=self.zoom_reset)
        menubar.add_cascade(label="View", menu=view_menu)

        menu_menu = tk.Menu(menubar, tearoff=0)
        menu_menu.add_command(label="Toggle Menu Rows (1/2)", command=self.toggle_menu_rows)

        rows_menu = tk.Menu(menu_menu, tearoff=0)
        self.menu_rows_mode_var = tk.StringVar(value=self.menu_rows_mode)
        for mode, lbl in (("auto", "Auto (split when it does not fit)"),
                          ("one", "Always one row"),
                          ("two", "Always two rows")):
            rows_menu.add_radiobutton(label=lbl, value=mode, variable=self.menu_rows_mode_var,
                                      command=lambda m=mode: self.set_menu_rows_mode(m))
        menu_menu.add_cascade(label="Menu Rows", menu=rows_menu)

        position_menu = tk.Menu(menu_menu, tearoff=0)
        self.menu_position_var = tk.StringVar(value=self.menu_position)
        for pos in ("top", "bottom"):
            position_menu.add_radiobutton(label=pos.capitalize(), value=pos,
                                          variable=self.menu_position_var,
                                          command=lambda p=pos: self.set_menu_position(p))
        menu_menu.add_cascade(label="Menu Position", menu=position_menu)

        menu_menu.add_separator()
        menu_menu.add_command(label="Preferences...", command=self.open_preferences)
        menubar.add_cascade(label="Menu", menu=menu_menu)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Save Macro...", command=self.save_macro)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        menubar.add_cascade(label="File", menu=file_menu)

        self.root.config(menu=menubar)

        self._setup_styles()

        self.top_bar = tk.Frame(self.root, bg=self.C_PANEL, bd=0,
                                highlightthickness=1, highlightbackground=self.C_BORDER)
        self.top_bar.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(8, 6))

        self.top_inner = tk.Frame(self.top_bar, bg=self.C_PANEL, padx=6, pady=6)
        self.top_inner.pack(fill=tk.X)

        # The section frames below are children of top_inner, not of a row, because
        # Tk only lets a widget be packed into its own parent or a descendant of it.
        # Parenting them to top_row1 made "pack(in_=top_row2)" -- a sibling -- fail
        # silently, which is why half the toolbar vanished in two-row mode. Keeping
        # them on top_inner also lets a section be packed beside the rows rather than
        # inside one, which is how the Timeline box spans both.
        self.top_rows = tk.Frame(self.top_inner, bg=self.C_PANEL)
        self.top_rows.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.top_row1 = tk.Frame(self.top_rows, bg=self.C_PANEL)
        self.top_row2 = tk.Frame(self.top_rows, bg=self.C_PANEL)
        self.menu_rows = [self.top_row1, self.top_row2]
        self.top_row1.pack(fill=tk.X)

        # One button length everywhere. Tk measures `width` in characters for a
        # text-only button but in *pixels* once the button carries an image, so the
        # icon buttons get the pixel equivalent of the same character width.
        # 10 = the longest remaining label ("Rotate Sel"/"Copy Style").
        self.BTN_W = 10
        self._btn_font = tkfont.Font(family=self.UI_FONT, size=8)
        self.BTN_W_PX = self._btn_font.measure("0") * self.BTN_W

        gray_btn_cfg = {"font": (self.UI_FONT, 8), "relief": tk.FLAT, "cursor": "hand2",
                        "padx": 4, "pady": 3, "bg": self.C_BTN, "fg": self.C_TEXT,
                        "activebackground": self.C_BTN_HOVER, "activeforeground": self.C_TEXT,
                        "bd": 0, "highlightthickness": 1,
                        "highlightbackground": self.C_BORDER, "highlightcolor": self.C_BORDER,
                        "width": self.BTN_W}
        icon_btn_cfg = dict(gray_btn_cfg, width=self.BTN_W_PX)
        # Colour pickers are swatches, not labelled buttons: the colour is the
        # label, so they stay small and carry no text.
        swatch_cfg = dict(gray_btn_cfg, width=3)

        snapping_frame = ttk.LabelFrame(self.top_inner, style="Toolbar.TLabelframe", text=" Board Settings ", padding=5)
        snapping_frame.pack(side=tk.LEFT, padx=3, pady=2, fill=tk.Y)
        self._snapping_frame = snapping_frame
        
        setting_grid = tk.Frame(snapping_frame, bg=self.C_PANEL)
        setting_grid.pack(fill=tk.BOTH, expand=True)

        def toggle_setting(var, name):
            if name == "rotate_rink":
                self.toggle_rink_orientation()
                return
            if name == "rink_mode":
                # Not a toggle but a rotation through the three fields, which is why
                # it is the one button here that carries no on/off highlight.
                self.cycle_rink_mode()
                return
            if name == "half_rink":
                # A toggle, but one that has to redraw the board rather than only
                # relight itself, because it changes how much rink there is.
                self.toggle_half_rink()
                return
            var.set(not var.get())
            if name == "ghosting" and not var.get():
                # Switching ghosting off with trails all over the rink and no way to
                # sweep them up was the awkward part: off now means gone.
                self.clear_ghosts()
            self._update_indicators()
            if name == "goals":
                self.redraw_canvas()
            elif name == "grid":
                self.toggle_grid_visuals()

        settings_list = [
            (self.RINK_BUTTON_LABELS[self.rink_mode], None, "rink_mode"),
            ("Rink: Half", self.half_rink_var, "half_rink"),
            ("Goals", self.goals_visible_var, "goals"),
            ("Snap Plr", self.snap_player_var, "snap_player"),
            ("Snap Ang", self.snap_angle_var, "snap_angle"),
            ("Snap Grd", self.grid_var, "grid"),
            ("Ghosting", self.ghosting_var, "ghosting"),
            ("Rotate", None, "rotate_rink")
        ]

        # Five columns of the global 10-character button would demand a lot from a row
        # that also has to hold General, Roster and Tactics. These buttons drop the
        # fixed width and take it from the equal-weight columns instead, so they still
        # all render at one length while asking for less.
        setting_btn_cfg = {k: v for k, v in gray_btn_cfg.items() if k != "width"}

        # A 2x4 grid: the uniform equal-weight columns give every button the same
        # length whether or not the last cell is filled.
        for idx, (label_txt, var_ref, name_key) in enumerate(settings_list):
            r, c = divmod(idx, 4)
            btn = tk.Button(setting_grid, text=label_txt, command=lambda v=var_ref, k=name_key: toggle_setting(v, k), **setting_btn_cfg)
            btn.grid(row=r, column=c, padx=3, pady=2, sticky="ew")
            self.setting_buttons[name_key] = btn
        for col in range(4):
            setting_grid.columnconfigure(col, weight=1, uniform="setting")

        roster_frame = ttk.LabelFrame(self.top_inner, style="Toolbar.TLabelframe", text=" Roster ", padding=5)
        roster_frame.pack(side=tk.LEFT, padx=3, pady=2, fill=tk.Y)
        self._roster_frame = roster_frame

        # A grid, so both team rows line up exactly: same label width, same count box,
        # same shape bar, same swatch. The player size sits in a column of its own,
        # spanning the two rows, because it belongs to neither team in particular.
        roster_grid = tk.Frame(roster_frame, bg=self.C_PANEL)
        roster_grid.pack(fill=tk.BOTH, expand=True)

        def team_row(row, caption, count_command, shape_value, color_command):
            tk.Label(roster_grid, text=caption, bg=self.C_PANEL,
                     font=(self.UI_FONT, 8, "bold"), width=3, anchor="w").grid(
                row=row, column=0, sticky="w", pady=2)
            count = tk.Spinbox(roster_grid, from_=1, to=10, width=2,
                               command=count_command, font=(self.UI_FONT, 8))
            count.delete(0, tk.END)
            count.insert(0, "5")
            count.grid(row=row, column=1, padx=1, pady=2)
            shape = tk.StringVar(value=shape_value)
            ttk.Combobox(roster_grid, textvariable=shape,
                         values=["Square", "Circle", "X", "Triangle", "Plus"],
                         width=8, font=(self.UI_FONT, 8),
                         style="Toolbar.TCombobox").grid(row=row, column=2,
                                                        padx=2, pady=2, sticky="ew")
            swatch = tk.Button(roster_grid, text="", command=color_command, **swatch_cfg)
            swatch._is_swatch = True
            swatch.grid(row=row, column=3, padx=1, pady=2)
            return count, shape, swatch

        (self.att_spinbox, self.att_shape_var,
         self.btn_att_color) = team_row(0, "Atk:", self._roster_count_changed,
                                        "Square", self.choose_att_color)
        self.btn_att_color.config(bg=self.att_color)
        (self.def_spinbox, self.def_shape_var,
         self.btn_def_color) = team_row(1, "Def:", self._roster_count_changed,
                                        "Circle", self.choose_def_color)
        self.btn_def_color.config(bg=self.def_color)

        for shape_var in (self.att_shape_var, self.def_shape_var):
            shape_var.trace_add("write", lambda *_a: self._roster_shape_changed())

        size_cell = tk.Frame(roster_grid, bg=self.C_PANEL)
        size_cell.grid(row=0, column=4, rowspan=2, padx=(8, 0), sticky="ns")
        tk.Label(size_cell, text="Size", bg=self.C_PANEL,
                 font=(self.UI_FONT, 8, "bold")).pack()
        self.player_size_spinbox = tk.Spinbox(size_cell, from_=6, to=60, width=3,
                                              textvariable=self.player_size_var,
                                              command=self._resize_selected_players,
                                              font=(self.UI_FONT, 8))
        self.player_size_spinbox.pack(pady=(1, 0))
        # `command` only fires for the little arrows. Typing a number and pressing
        # Return, or clicking away, has to work as well.
        self.player_size_spinbox.bind("<Return>", self._resize_selected_players)
        self.player_size_spinbox.bind("<FocusOut>", self._resize_selected_players)
        roster_grid.columnconfigure(2, weight=1)


        signs_frame = ttk.LabelFrame(self.top_inner, style="Toolbar.TLabelframe", text=" Shapes ", padding=5)
        signs_frame.pack(side=tk.LEFT, padx=3, pady=2, fill=tk.Y)
        self._signs_frame = signs_frame
        # One grid of three uniform columns, so every button in the box is the same
        # width whatever its label -- including Text and Image, and including the row
        # that carries the text-size field in its third cell.
        sign_grid = tk.Frame(signs_frame, bg=self.C_PANEL)
        sign_grid.pack(fill=tk.BOTH, expand=True)
        sign_btn_cfg = {k: v for k, v in gray_btn_cfg.items() if k != "width"}
        for idx, stype in enumerate(["Goal", "X", "Ball", "Square", "Triangle", "Plus"]):
            btn = tk.Button(sign_grid, text=stype,
                            command=lambda t=stype: self.set_tool(f"sign_{t.lower()}"),
                            **sign_btn_cfg)
            btn.grid(row=idx // 3, column=idx % 3, padx=3, pady=2, sticky="ew")
            self.tool_buttons[f"sign_{stype.lower()}"] = btn

        # Text is a tool -- click the board to place a label. Image is not: it opens a
        # picker and drops the picture in the middle, already selected to be dragged.
        text_btn = tk.Button(sign_grid, text="Text", command=lambda: self.set_tool("text"),
                             **sign_btn_cfg)
        text_btn.grid(row=2, column=0, padx=3, pady=2, sticky="ew")
        self.tool_buttons["text"] = text_btn
        tk.Button(sign_grid, text="Image", command=self.add_board_image,
                  **sign_btn_cfg).grid(row=2, column=1, padx=3, pady=2, sticky="ew")

        # Size and colour take the third cell of the last row -- one dial for every
        # shape in this box, text included, instead of a separate row above and a
        # second text-only field beside it.
        size_cell = tk.Frame(sign_grid, bg=self.C_PANEL)
        size_cell.grid(row=2, column=2, padx=3, pady=2, sticky="ew")
        tk.Label(size_cell, text="Size", bg=self.C_PANEL, fg=self.C_TEXT,
                 font=(self.UI_FONT, 8, "bold")).pack(side=tk.LEFT)
        self.sign_size_spinbox = tk.Spinbox(size_cell, from_=6, to=60, width=3,
                                            textvariable=self.sign_size_var,
                                            command=self._apply_sign_size,
                                            font=(self.UI_FONT, 8))
        self.sign_size_spinbox.pack(side=tk.LEFT, padx=(3, 0), fill=tk.X, expand=True)
        self.sign_size_spinbox.bind("<Return>", self._apply_sign_size)
        self.sign_size_spinbox.bind("<FocusOut>", self._apply_sign_size)
        self.btn_sign_color = tk.Button(size_cell, text="", command=self.choose_sign_color,
                                        **swatch_cfg)
        self.btn_sign_color.config(bg=self.sign_color)
        self.btn_sign_color._is_swatch = True
        self.btn_sign_color.pack(side=tk.LEFT, padx=(3, 0))
        for column in range(3):
            sign_grid.columnconfigure(column, weight=1, uniform="sign")

        tactics_frame = ttk.LabelFrame(self.top_inner, style="Toolbar.TLabelframe", text=" Tactics  \u2014  0% = own goal, 100% = opponent goal ", padding=5)
        tactics_frame.pack(side=tk.LEFT, padx=3, pady=2, fill=tk.Y)
        self._tactics_frame = tactics_frame

        def tactics_row(parent, caption, pct_var, tactic_var, team):
            row = tk.Frame(parent, bg=self.C_PANEL)
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=caption, bg=self.C_PANEL, font=(self.UI_FONT, 8, "bold"),
                     width=4, anchor="w").pack(side=tk.LEFT)
            tk.Spinbox(row, from_=0, to=100, increment=5, width=4, textvariable=pct_var,
                       font=(self.UI_FONT, 8)).pack(side=tk.LEFT, padx=1)
            tk.Label(row, text="%", bg=self.C_PANEL, font=(self.UI_FONT, 8)).pack(side=tk.LEFT)
            ttk.Combobox(row, textvariable=tactic_var, values=list(self.FORMATIONS.keys()),
                         width=9, state="readonly", font=(self.UI_FONT, 8), style="Toolbar.TCombobox").pack(side=tk.LEFT, padx=2)
            tk.Button(row, text="Apply", command=lambda: self.apply_tactic(team),
                      **gray_btn_cfg).pack(side=tk.LEFT, padx=3, expand=True, fill=tk.X)

        tactics_row(tactics_frame, "Atk:", self.att_pct_var, self.att_tactic_var, "att")
        tactics_row(tactics_frame, "Def:", self.def_pct_var, self.def_tactic_var, "def")

        actions_frame = ttk.LabelFrame(self.top_inner, style="Toolbar.TLabelframe", text=" Drawing Tools ", padding=5)
        actions_frame.pack(side=tk.LEFT, padx=3, pady=2, fill=tk.Y)
        self._actions_frame = actions_frame

        act_row1 = tk.Frame(actions_frame, bg=self.C_PANEL)
        act_row1.pack(fill=tk.X, pady=2)
        act_row2 = tk.Frame(actions_frame, bg=self.C_PANEL)
        act_row2.pack(fill=tk.X, pady=2)
        act_row3 = tk.Frame(actions_frame, bg=self.C_PANEL)
        act_row3.pack(fill=tk.X, pady=2)
        
        # Same metrics as gray_btn_cfg (only the colours differ) so it measures the
        # same as every other button in the row.
        select_btn = tk.Button(act_row1, text="Select", command=self.cancel_active_tool, **dict(gray_btn_cfg, bg="#cce5ff", activebackground="#b8d9ff", fg="#004085"))
        select_btn.pack(side=tk.LEFT, padx=3, expand=True, fill=tk.X)
        self.tool_buttons["select"] = select_btn

        self.icon_pass = self._create_action_icon("pass")
        self.icon_shot = self._create_action_icon("shot")
        self.icon_dribble = self._create_action_icon("dribble")
        self.icon_run = self._create_action_icon("run")

        pass_btn = tk.Button(act_row1, text="Pass", image=self.icon_pass, compound=tk.LEFT, command=lambda: self.set_tool("pass"), **icon_btn_cfg)
        pass_btn.image = self.icon_pass
        pass_btn.pack(side=tk.LEFT, padx=3, expand=True, fill=tk.X)
        self.tool_buttons["pass"] = pass_btn

        shot_btn = tk.Button(act_row1, text="Shot", image=self.icon_shot, compound=tk.LEFT, command=lambda: self.set_tool("shot"), **icon_btn_cfg)
        shot_btn.image = self.icon_shot
        shot_btn.pack(side=tk.LEFT, padx=3, expand=True, fill=tk.X)
        self.tool_buttons["shot"] = shot_btn

        drib_btn = tk.Button(act_row1, text="Dribble", image=self.icon_dribble, compound=tk.LEFT, command=lambda: self.set_tool("dribble"), **icon_btn_cfg)
        drib_btn.image = self.icon_dribble
        drib_btn.pack(side=tk.LEFT, padx=3, expand=True, fill=tk.X)
        self.tool_buttons["dribble"] = drib_btn

        run_btn = tk.Button(act_row1, text="Run", image=self.icon_run, compound=tk.LEFT, command=lambda: self.set_tool("run"), **icon_btn_cfg)
        run_btn.image = self.icon_run
        run_btn.pack(side=tk.LEFT, padx=3, expand=True, fill=tk.X)
        self.tool_buttons["run"] = run_btn

        line_btn = tk.Button(act_row2, text="Line", command=lambda: self.set_tool("line"), **gray_btn_cfg)
        line_btn.pack(side=tk.LEFT, padx=3, expand=True, fill=tk.X)
        self.tool_buttons["line"] = line_btn

        bend_btn = tk.Button(act_row2, text="Bend", command=lambda: self.set_tool("bend"), **gray_btn_cfg)
        bend_btn.pack(side=tk.LEFT, padx=3, expand=True, fill=tk.X)
        self.tool_buttons["bend"] = bend_btn

        box_btn = tk.Button(act_row2, text="Box", command=lambda: self.set_tool("box"), **gray_btn_cfg)
        box_btn.pack(side=tk.LEFT, padx=3, expand=True, fill=tk.X)
        self.tool_buttons["box"] = box_btn

        rect_btn = tk.Button(act_row2, text="Rect", command=lambda: self.set_tool("rectangle"), **gray_btn_cfg)
        rect_btn.pack(side=tk.LEFT, padx=3, expand=True, fill=tk.X)
        self.tool_buttons["rectangle"] = rect_btn

        circ_btn = tk.Button(act_row2, text="Circle", command=lambda: self.set_tool("circle"), **gray_btn_cfg)
        circ_btn.pack(side=tk.LEFT, padx=3, expand=True, fill=tk.X)
        self.tool_buttons["circle"] = circ_btn

        oval_btn = tk.Button(act_row2, text="Oval", command=lambda: self.set_tool("oval"), **gray_btn_cfg)
        oval_btn.pack(side=tk.LEFT, padx=3, expand=True, fill=tk.X)
        self.tool_buttons["oval"] = oval_btn

        rotate_btn = tk.Button(act_row3, text="Rotate Sel", command=self.rotate_selected, **gray_btn_cfg)
        rotate_btn.pack(side=tk.LEFT, padx=3, pady=1, expand=True, fill=tk.X)

        copy_style_btn = tk.Button(act_row3, text="Copy Style", command=self.toggle_copy_paste_style, **gray_btn_cfg)
        copy_style_btn.pack(side=tk.LEFT, padx=3, expand=True, fill=tk.X)
        self.tool_buttons["copy_style"] = copy_style_btn
        self.copy_style_btn = copy_style_btn

        set_default_btn = tk.Button(act_row3, text="Default", command=self.set_as_default_popup, **gray_btn_cfg)
        set_default_btn.pack(side=tk.LEFT, padx=3, expand=True, fill=tk.X)
        self.tool_buttons["set_default"] = set_default_btn

        delete_btn = tk.Button(act_row3, text="Delete", command=self.delete_selection,
                               **gray_btn_cfg)
        delete_btn.pack(side=tk.LEFT, padx=3, expand=True, fill=tk.X)
        self.tool_buttons["delete"] = delete_btn

        line_style_sub = tk.Frame(act_row3, bg=self.C_PANEL)
        line_style_sub.pack(side=tk.LEFT, padx=2)
        
        tk.Label(line_style_sub, text="Type:", bg=self.C_PANEL, font=(self.UI_FONT, 7)).pack(side=tk.LEFT)
        line_style_options = ["Solid", "Dashed", "Dotted", "Pass", "Shot", "Dribble", "Run"]
        ttk.Combobox(line_style_sub, textvariable=self.line_type_var, values=line_style_options, width=6, font=(self.UI_FONT, 7), style="Toolbar.TCombobox").pack(side=tk.LEFT, padx=1, expand=True, fill=tk.X)
        
        tk.Label(line_style_sub, text="Thick:", bg=self.C_PANEL, font=(self.UI_FONT, 7)).pack(side=tk.LEFT)
        ttk.Combobox(line_style_sub, textvariable=self.line_thick_var, values=["1", "2", "3", "4", "5"], width=2, font=(self.UI_FONT, 7), style="Toolbar.TCombobox").pack(side=tk.LEFT, padx=1, expand=True, fill=tk.X)
        
        tk.Label(line_style_sub, text="Clr", bg=self.C_PANEL, font=(self.UI_FONT, 7)).pack(side=tk.LEFT)
        self.btn_line_color = tk.Button(line_style_sub, text="", command=self.choose_line_color, **swatch_cfg)
        self.btn_line_color.config(bg=self.line_color)
        self.btn_line_color._is_swatch = True
        self.btn_line_color.pack(side=tk.LEFT, padx=1)

        align_frame = ttk.LabelFrame(self.top_inner, style="Toolbar.TLabelframe", text=" Align & Distribute ", padding=5)
        align_frame.pack(side=tk.LEFT, padx=3, pady=2, fill=tk.Y)
        self._align_frame = align_frame

        a_col1 = tk.Frame(align_frame, bg=self.C_PANEL)
        a_col1.pack(side=tk.LEFT, padx=2, fill=tk.BOTH, expand=True)
        align_btn_cfg = dict(gray_btn_cfg)
        tk.Button(a_col1, text="Align H", command=lambda: self.align_tokens("horizontal"), **align_btn_cfg).grid(row=0, column=0, padx=3, pady=2, sticky="ew")
        tk.Button(a_col1, text="Align V", command=lambda: self.align_tokens("vertical"), **align_btn_cfg).grid(row=0, column=1, padx=3, pady=2, sticky="ew")
        tk.Button(a_col1, text="Dist H", command=self.distribute_horizontally, **align_btn_cfg).grid(row=1, column=0, padx=3, pady=2, sticky="ew")
        tk.Button(a_col1, text="Dist V", command=self.distribute_vertically, **align_btn_cfg).grid(row=1, column=1, padx=3, pady=2, sticky="ew")

        tk.Button(a_col1, text="Group", command=self.group_selected, **align_btn_cfg).grid(row=2, column=0, padx=3, pady=2, sticky="ew")
        tk.Button(a_col1, text="Lock", command=self.lock_selected, **align_btn_cfg).grid(row=2, column=1, padx=3, pady=2, sticky="ew")
        for col in range(2):
            a_col1.columnconfigure(col, weight=1)

        # Timeline: the action log on the left, the animation steps beside it, and the
        # transport buttons in a column on the right. It spans both toolbar rows.
        timeline_frame = ttk.LabelFrame(self.top_inner, style="Toolbar.TLabelframe", text=" Timeline ", padding=5)
        timeline_frame.pack(side=tk.LEFT, padx=3, pady=2, fill=tk.Y)
        self._timeline_frame = timeline_frame

        # The two lists are stacked rather than side by side: this box spans both
        # toolbar rows, so it has height to spare and width it cannot spare.
        t_sub = tk.Frame(timeline_frame, bg=self.C_PANEL)
        t_sub.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # A tree, not a list: a group is a heading in bold that can be collapsed, with
        # the actions it contains underneath. A Listbox has one font for every row and
        # no notion of a parent, so neither bold headings nor folding were possible.
        anim_column = tk.Frame(t_sub, bg=self.C_PANEL)
        anim_column.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=2)

        style = ttk.Style()
        style.configure("Timeline.Treeview", background=self.C_SURFACE,
                        fieldbackground=self.C_SURFACE, foreground=self.C_TEXT,
                        font=(self.UI_FONT, 8), rowheight=17, borderwidth=0)
        style.layout("Timeline.Treeview", [("Timeline.Treeview.treearea",
                                            {"sticky": "nswe"})])

        self.anim_tree = ttk.Treeview(anim_column, style="Timeline.Treeview",
                                      columns=("time",), show="tree", height=8,
                                      selectmode="browse")
        # Narrow by request, wide by expansion: the timeline takes whatever the two
        # rows of boxes leave over, so asking for less here keeps Tactics intact.
        self.anim_tree.column("#0", width=120, minwidth=90, stretch=True)
        self.anim_tree.column("time", width=40, minwidth=36, anchor="e", stretch=False)
        self.anim_tree.tag_configure("group", font=(self.UI_FONT, 8, "bold"))
        self.anim_tree.tag_configure("playhead", background=self.PLAYHEAD_COLOR,
                                     foreground="#ffffff")
        self.anim_tree.tag_configure("action", foreground=self.C_MUTED)
        self.anim_tree.pack(fill=tk.BOTH, expand=True, pady=(1, 2))
        # The old action log lives on off-screen: commands still record and un-record
        # their lines through it, and those lines are what name the groups above.
        self.steps_listbox = tk.Listbox(t_sub)
        self.anim_tree.bind("<<TreeviewSelect>>", self._on_anim_step_selected)
        self.anim_tree.bind("<Double-Button-1>", self._rename_group)
        # Drag and drop to reorder.
        self.anim_tree.bind("<ButtonPress-1>", self._anim_drag_start)
        self.anim_tree.bind("<B1-Motion>", self._anim_drag_motion)
        self.anim_tree.bind("<ButtonRelease-1>", self._anim_drag_end)

        time_row = tk.Frame(anim_column, bg=self.C_PANEL)
        time_row.pack(fill=tk.X)
        tk.Label(time_row, text="Time", bg=self.C_PANEL, fg=self.C_TEXT,
                 font=(self.UI_FONT, 7)).pack(side=tk.LEFT)
        self.step_time_scale = tk.Scale(time_row, from_=0.0, to=10.0, resolution=0.1,
                                        orient=tk.HORIZONTAL, variable=self.step_time_var,
                                        bg=self.C_PANEL, fg=self.C_TEXT,
                                        troughcolor=self.C_SURFACE, highlightthickness=0,
                                        font=(self.UI_FONT, 7), showvalue=True, length=90,
                                        command=self._on_step_time_changed)
        self.step_time_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(3, 0))

        # The slider retimes every step at once; this field is the selected step's own
        # interval, so a single step can be lengthened without hunting for the
        # double-click.
        # No separate per-group time field: double-clicking a group opens one dialog
        # carrying both its name and its seconds, which is where the two belong.

        anim_buttons = tk.Frame(timeline_frame, bg=self.C_PANEL)
        anim_buttons.pack(side=tk.LEFT, fill=tk.Y, padx=(4, 0))
        transport_cfg = {k: v for k, v in gray_btn_cfg.items() if k != "width"}
        # One button per row, one column, uniform width: every transport button ends up
        # the same length whatever its label.
        for row, (label, command) in enumerate((
                ("Play", self.play_animation),
                ("Pause", self.pause_animation),
                ("Stop", self.stop_animation),
                ("Add Group", self.add_animation_step),
                ("Delete", self.delete_animation_selection),
                ("Up", lambda: self.move_animation_step(-1)),
                ("Down", lambda: self.move_animation_step(1)))):
            button = tk.Button(anim_buttons, text=label, command=command, **transport_cfg)
            button.grid(row=row, column=0, sticky="ew", padx=1, pady=1)
            self.anim_buttons[label] = button
        anim_buttons.columnconfigure(0, weight=1)

        # General: the undo/redo and macro-file actions that used to live inside
        # Timeline & Macros. Sits first in the top row, right beside the timeline.
        general_frame = ttk.LabelFrame(self.top_inner, style="Toolbar.TLabelframe", text=" General ", padding=5)
        general_frame.pack(side=tk.LEFT, padx=3, pady=2, fill=tk.Y)
        self._general_frame = general_frame

        g_grid = tk.Frame(general_frame, bg=self.C_PANEL)
        g_grid.pack(fill=tk.BOTH, expand=True)
        general_btn_cfg = {k: v for k, v in gray_btn_cfg.items() if k != "width"}

        tk.Button(g_grid, text="Undo", command=self.undo, **general_btn_cfg).grid(row=0, column=0, padx=3, pady=2, sticky="ew")
        tk.Button(g_grid, text="Redo", command=self.redo, **general_btn_cfg).grid(row=0, column=1, padx=3, pady=2, sticky="ew")
        tk.Button(g_grid, text="Save", command=self.save_macro, **general_btn_cfg).grid(row=0, column=2, padx=3, pady=2, sticky="ew")
        tk.Button(g_grid, text="Export", command=self.export_animation, **general_btn_cfg).grid(row=0, column=3, padx=3, pady=2, sticky="ew")
        tk.Button(g_grid, text="Load", command=self.load_macro, **general_btn_cfg).grid(row=1, column=0, padx=3, pady=2, sticky="ew")
        tk.Button(g_grid, text="Watermark", command=self.add_watermark, **general_btn_cfg).grid(row=1, column=1, padx=3, pady=2, sticky="ew")
        tk.Button(g_grid, text="Reset", command=self.reset_board, **general_btn_cfg).grid(row=1, column=2, padx=3, pady=2, sticky="ew")
        tk.Button(g_grid, text="Prefs", command=self.open_preferences, **general_btn_cfg).grid(row=1, column=3, padx=3, pady=2, sticky="ew")
        for col in range(4):
            g_grid.columnconfigure(col, weight=1, uniform="general")

        self.canvas_container = tk.Frame(self.root, bg=self.C_SURFACE, bd=0,
                                        highlightthickness=1, highlightbackground=self.C_BORDER)
        self.canvas_container.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        self.canvas = tk.Canvas(self.canvas_container, width=self.width, height=self.height, bg="#ffffff", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=4, pady=4, anchor="center")

        self.canvas.bind("<ButtonPress-1>", self.on_canvas_press)
        self.canvas.bind("<B1-Motion>", self.on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_canvas_release)
        self.canvas.bind("<Button-3>", self.show_context_menu)
        self.canvas.bind("<Configure>", self.on_canvas_resize)
        # The wheel: X11 sends buttons 4/5 up and down and 6/7 sideways, everything
        # else sends <MouseWheel> with a delta. Shift turns a vertical wheel sideways.
        # Buttons 6 and 7 are the horizontal wheel; older Tk builds refuse to bind
        # them at all, so every sequence is tried on its own and a refusal is fine.
        for sequence in ("<MouseWheel>", "<Shift-MouseWheel>",
                         "<Button-4>", "<Button-5>", "<Button-6>", "<Button-7>",
                         "<Shift-Button-4>", "<Shift-Button-5>"):
            try:
                self.canvas.bind(sequence, self._on_mouse_wheel)
            except Exception:
                pass
        self.root.bind("<Configure>", self.on_window_resize)

        self._update_indicators()
        self._polish_buttons(self.top_bar)
        self._attach_tooltips()

        self._apply_menu_position()
        self._reflow_menu(force=True)

    # What each toolbar button does, keyed on its label. Attached after the toolbar is
    # built rather than at each call site, so the captions stay together in one place
    # and a new button only needs a line here.
    BUTTON_TOOLTIPS = {
        # General
        "Undo": "Undo the last action (Ctrl+Z).",
        "Redo": "Redo the action you just undid (Ctrl+Y).",
        "Save": "Save the board and its timeline to a macro file.",
        "Load": "Open a saved macro, restoring the players, drawings and watermark.",
        "Watermark": "Load a logo onto the rink, then place, crop and fade it in the "
                     "editor that opens.",
        "Prefs": "Preferences: default colours, sizes and board options.",
        "Export": "Export the play: GIF, MP4, WebM, AVI or MOV, or a PNG/JPEG of "
                  "whichever groups you pick.",
        "Reset": "Clear the board back to the starting formations, with no drawings, "
                 "watermark, timeline or animation steps. Asks first.",
        # Animation transport
        "Add Group": "Close the current group and start the next one. Everything in a "
                     "group happens at the same time when the animation plays.",
        "Play": "Play the animation from the red group.",
        "Pause": "Pause where it is. Play carries on from the same point.",
        "Stop": "Stop and return to group 0.",
        "Delete": "Delete what is picked in the timeline: a group with everything in "
                  "it, or a single action out of its group.",
        "Up": "Move the selected group one place earlier in the sequence.",
        "Down": "Move the selected group one place later in the sequence.",
        # Board settings
        "Rink: 5v5": "Large field, 40 x 20 m, five a side. Click for 4v4.",
        "Rink: 4v4": "Small field, 27 x 15 m, four a side. Click for 3v3.",
        "Rink: 3v3": "3v3 field, 22 x 11 m, three a side. Click for 5v5.",
        "Rink: Half": "Draw one end of the field instead of all of it. Works on "
                      "every field, and leaves the number of players alone.",
        "Goals": "Show or hide the goals.",
        "Snap Plr": "Snap line ends and the ball onto nearby players.",
        "Snap Ang": "Snap drawn lines to 45 degree angles.",
        "Snap Grd": "Snap to the background grid.",
        "Ghosting": "Leave a faded copy behind when a player is moved.",
        "Rotate": "Turn the whole rink between landscape and portrait.",
        # Tactics
        "Apply": "Move this team into the chosen formation, at the chosen depth.",
        # Align & distribute
        "Align H": "Line the selected players up on one horizontal line.",
        "Align V": "Line the selected players up on one vertical line.",
        "Dist H": "Space the selected players evenly from left to right.",
        "Dist V": "Space the selected players evenly from top to bottom.",
        "Group": "Group the selection so it moves as one (Ctrl+G).",
        "Lock": "Lock the selection so it cannot be moved or deleted.",
        # Signs
        "Goal": "Stamp a goal. Always the same size as the goals on the rink.",
        "X": "Stamp an X marker.",
        "Ball": "Stamp the ball. With Snap Plr on it clicks onto the nearest "
                "player's edge.",
        "Square": "Stamp a square marker.",
        "Triangle": "Stamp a triangle marker.",
        "Plus": "Stamp a plus marker.",
        "Text": "Place a text label: click the board, then type. Uses the Size dial "
                "and the colour beside it.",
        "Image": "Put a picture on the board. Drag it to move it, or drag a corner "
                 "handle to scale it.",
        # Drawing tools
        "Select": "Select and move players, signs and lines. Drag to box-select.",
        "Pass": "Draw a pass: a straight arrow.",
        "Shot": "Draw a shot.",
        "Dribble": "Draw a dribble: a wavy run with the ball.",
        "Run": "Draw a run without the ball.",
        "Line": "Draw a plain straight line.",
        "Bend": "Draw a curve: click the start, the bend, then the end. It takes the "
                "line type that is armed, so a pass, shot, dribble or run can be bent.",
        "Box": "Draw a square outline.",
        "Rect": "Draw a rectangle outline.",
        "Circle": "Draw a circle.",
        "Oval": "Draw an oval.",
        "Rotate Sel": "Turn the selection 45 degrees, players and signs alike.",
        "Copy Style": "Copy the colour and shape of one player, then click others to "
                      "paste it.",
        "Default": "Save the current colours and sizes as the defaults for new boards.",
        "Delete": "Delete everything selected -- players, signs, lines, text and "
                  "pictures. Same as the Delete key.",
    }

    SWATCH_TOOLTIPS = {
        "btn_att_color": "Colour of the attacking players.",
        "btn_def_color": "Colour of the defending players.",
        "btn_sign_color": "Colour used for new signs, and for any signs selected now.",
        "btn_line_color": "Colour used for new lines.",
    }

    def _attach_tooltips(self, container=None):
        """Hang a caption on every toolbar button whose label is described above."""
        for attribute, text in self.SWATCH_TOOLTIPS.items():
            widget = getattr(self, attribute, None)
            if widget is not None:
                Tooltip(widget, text, font=(self.UI_FONT, 8))

        def walk(parent):
            for child in parent.winfo_children():
                if isinstance(child, (tk.Button, tk.Checkbutton)):
                    label = child.cget("text")
                    if label.startswith("Rink: "):
                        # The field button relabels itself as the rink is cycled, so
                        # the caption is looked up when it is shown, not when it is
                        # bound. Rink: Half is looked up the same way for free.
                        Tooltip(child, lambda w=child: self.BUTTON_TOOLTIPS.get(w.cget("text")),
                                font=(self.UI_FONT, 8))
                    elif self.BUTTON_TOOLTIPS.get(label):
                        Tooltip(child, self.BUTTON_TOOLTIPS[label], font=(self.UI_FONT, 8))
                walk(child)

        walk(container if container is not None else self.top_bar)

    def _update_indicators(self):
        for name, btn in self.tool_buttons.items():
            try:
                # No active tool *is* the select tool: with nothing else armed the
                # board is in select-and-move mode, so Select lights up like any other
                # tool rather than being the one that never looks chosen.
                active = (name == self.active_tool
                          or (name == "select" and self.active_tool is None))
                if active:
                    btn.config(bg=self.C_ACCENT, fg=self.C_ACCENT_FG)
                else:
                    btn.config(bg=self.C_BTN, fg=self.C_TEXT)
            except Exception:
                pass

        for name, btn in self.setting_buttons.items():
            var_map = {
                "half_rink": self.half_rink_var,
                "goals": self.goals_visible_var,
                "snap_player": self.snap_player_var,
                "snap_angle": self.snap_angle_var,
                "grid": self.grid_var,
                "ghosting": self.ghosting_var
            }
            is_active = bool(name in var_map and var_map[name].get())
            if name == "rink_mode":
                # The label is the field you are on, and the tooltip says what the
                # next click switches to.
                try:
                    btn.config(text=self.RINK_BUTTON_LABELS[self.rink_mode])
                except Exception:
                    pass
            if is_active:
                try:
                    btn.config(bg=self.C_ACCENT, fg=self.C_ACCENT_FG)
                except Exception:
                    pass
            else:
                try:
                    btn.config(bg=self.C_BTN, fg=self.C_TEXT)
                except Exception:
                    pass

    def cancel_active_tool(self, event=None):
        is_escape = False
        if event is not None:
            ks = getattr(event, "keysym", None)
            if ks == "Escape" or getattr(event, "keycode", None) == 27 or ks is None:
                is_escape = True

        if is_escape:
            now = time.time()
            time_since = now - getattr(self, "_last_esc_time", 0.0)
            self._last_esc_time = now

            if self.paste_style_active:
                self._deactivate_paste_style()
                return "break"
            if self.active_tool or self.selection_rect or self.selected_tokens or self.selected_drawn:
                self.active_tool = None
                self.bend_control_point = None
                for pid in self.current_preview_ids:
                    try:
                        self.canvas.delete(pid)
                    except Exception:
                        pass
                self.current_preview_ids = []
                if self.selection_rect:
                    try:
                        self.canvas.delete(self.selection_rect)
                    except Exception:
                        pass
                    self.selection_rect = None
                    self.selection_start = None
                self.clear_selection()
                self._update_indicators()
                return "break"

            if time_since < 1.2:
                self._show_save_exit_dialog()
                return "break"
            return "break"
        else:
            for pid in self.current_preview_ids:
                try:
                    self.canvas.delete(pid)
                except Exception:
                    pass
            self.current_preview_ids = []
            self.temp_line_start = None
            self.bend_control_point = None

            if self.selection_rect:
                try:
                    self.canvas.delete(self.selection_rect)
                except Exception:
                    pass
                self.selection_rect = None
                self.selection_start = None

            self.active_tool = None
            self._update_indicators()
            self.root.title("Floorball Tactics Studio - Active Mode: Select & Move")

    def _show_save_exit_dialog(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Quick Actions")
        dlg.geometry("360x110")
        tk.Label(dlg, text="Choose an action:").pack(pady=6)
        btnframe = tk.Frame(dlg)
        btnframe.pack(pady=6)
        def do_save():
            try:
                self.save_macro()
            except Exception:
                pass
            dlg.destroy()
        def do_exit():
            dlg.destroy()
            self.root.quit()
        def do_cancel():
            dlg.destroy()
        tk.Button(btnframe, text="Save", command=do_save, width=10).pack(side=tk.LEFT, padx=8)
        tk.Button(btnframe, text="Exit", command=do_exit, width=10).pack(side=tk.LEFT, padx=8)
        tk.Button(btnframe, text="Cancel", command=do_cancel, width=10).pack(side=tk.LEFT, padx=8)

    def _deactivate_paste_style(self):
        self.paste_style_active = False
        try:
            self.copy_style_btn.config(bg="#228be6", fg="#ffffff", text="Copy Style")
        except Exception:
            pass

    def toggle_copy_paste_style(self):
        if not self.paste_style_active:
            self.copied_style = {
                "line_color": self.line_color,
                "line_thick": int(self.line_thick_var.get()),
                "line_type": self.line_type_var.get(),
                "att_color": self.att_color,
                "def_color": self.def_color
            }
            self.paste_style_active = True
            try:
                self.copy_style_btn.config(bg="#99d3ff", fg="#004085", text="Paste Style")
            except Exception:
                pass
            messagebox.showinfo("Style Copied", "Style copied. Click a token to apply (ESC or press button to cancel).")
        else:
            self._deactivate_paste_style()

    def _apply_copied_style_to_token(self, token):
        if not self.copied_style or not token:
            return
        new_color = token.get("color", "black")
        label = token.get("label","")
        if label.startswith("A"):
            new_color = self.copied_style.get("att_color", new_color)
        elif label.startswith("D"):
            new_color = self.copied_style.get("def_color", new_color)
        else:
            new_color = self.copied_style.get("att_color", new_color)
        token["color"] = new_color
        sid = token.get("shape_id")
        try:
            st = token.get("shape","").lower()
            if st == "ball":
                self.canvas.itemconfig(sid, fill=new_color, outline=self.TOKEN_EDGE)
            elif st in ("x", "plus"):
                # Every stroke of the X carries the colour -- except the halo and the
                # dark ring underneath it, which are what make it legible.
                keep = (set(token.get("decor_ids", ())) | set(token.get("halo_ids", ()))
                        | set(token.get("text_ids", ())))
                for k, v in list(self.tokens.items()):
                    if v is token and isinstance(k, int) and k not in keep:
                        try:
                            self.canvas.itemconfig(k, fill=new_color)
                        except Exception:
                            pass
            else:
                try:
                    self.canvas.itemconfig(sid, fill=new_color)
                except Exception:
                    pass
            if "text_ids" in token:
                for tid in token["text_ids"]:
                    try:
                        self.canvas.itemconfig(tid, fill="white")
                    except Exception:
                        pass
        except Exception:
            pass

    # ----------------------
    # Menu layout
    # ----------------------
    # Timeline comes last so it sits on the right, whether it is spanning beside the
    # rows or has fallen back into one of them.
    MENU_SECTION_ATTRS = ("_general_frame", "_snapping_frame", "_roster_frame",
                          "_tactics_frame", "_align_frame", "_signs_frame",
                          "_actions_frame", "_timeline_frame")

    # Two rows is a guarantee, not a preference: the toolbar never grows a third row.
    MENU_HARD_TWO_ROWS = True

    # The requested two-row arrangement. Used verbatim whenever the toolbar lands on
    # two rows, instead of letting the width-balancer choose the split.
    MENU_TWO_ROW_GROUPS = (("_general_frame", "_snapping_frame", "_roster_frame",
                            "_tactics_frame"),
                           ("_align_frame", "_signs_frame", "_actions_frame"))

    # Toolbar wraps onto as many rows as it needs, up to this many, so it fills the
    # width without ever overflowing it.
    MENU_MAX_ROWS = 7

    def _menu_sections(self):
        return [getattr(self, name) for name in self.MENU_SECTION_ATTRS
                if getattr(self, name, None) is not None]

    SECTION_GAP = 8          # horizontal room a section needs beyond its content

    def _menu_row_frames(self, count):
        """Row frames are created on demand; the first two are the original
        top_row1/top_row2 so the rest of the class can still refer to them."""
        while len(self.menu_rows) < count:
            self.menu_rows.append(tk.Frame(self.top_rows, bg=self.C_PANEL))
        return self.menu_rows[:count]

    @staticmethod
    def _split_evenly(widths, count):
        """Partition into `count` consecutive runs so that the *widest* run is as
        narrow as possible, returned as index ranges.

        Minimising the widest run is the right objective because that is what has to
        fit the window. A greedy running-total split looks similar but front-loads
        the early rows and dumps whatever is left into the last one -- that is how
        the toolbar ended up with a 1262px row inside a 900px window."""
        count = max(1, min(count, len(widths)))
        n = len(widths)
        prefix = [0] * (n + 1)
        for index, width in enumerate(widths):
            prefix[index + 1] = prefix[index] + width

        infinity = float("inf")
        # best[k][i]: narrowest possible widest-run when the first i sections are
        # divided into k rows.
        best = [[infinity] * (n + 1) for _ in range(count + 1)]
        cut = [[0] * (n + 1) for _ in range(count + 1)]
        best[0][0] = 0
        for k in range(1, count + 1):
            for i in range(k, n + 1):
                for j in range(k - 1, i):
                    if best[k - 1][j] == infinity:
                        continue
                    candidate = max(best[k - 1][j], prefix[i] - prefix[j])
                    if candidate < best[k][i]:
                        best[k][i], cut[k][i] = candidate, j

        runs, i = [], n
        for k in range(count, 0, -1):
            j = cut[k][i]
            runs.append((j, i))
            i = j
        return list(reversed(runs))

    def _fixed_two_rows(self, sections):
        """The explicit two-row grouping, filtered to the sections actually in play
        (the timeline is absent when it is spanning beside the rows)."""
        available = set(sections)
        rows = []
        for group in self.MENU_TWO_ROW_GROUPS:
            row = [getattr(self, name) for name in group
                   if getattr(self, name, None) in available]
            if row:
                rows.append(row)
        placed = {s for row in rows for s in row}
        leftover = [s for s in sections if s not in placed]
        if leftover:
            rows[0] = leftover + rows[0] if rows else [leftover]
        return rows if len(rows) == 2 else None

    def _plan_menu_rows(self, sections, budget):
        """Fewest rows whose widest row still fits the budget. This is what keeps the
        toolbar inside the window at any size instead of running off the edge."""
        widths = [max(s.winfo_reqwidth(), 1) + self.SECTION_GAP for s in sections]
        limit = min(self.MENU_MAX_ROWS, len(sections))
        if self.menu_rows_mode == "one":
            counts = [1]
        elif self.menu_rows_mode == "two":
            fixed = self._fixed_two_rows(sections)
            if fixed and self.MENU_HARD_TWO_ROWS:
                # No width test: two rows is guaranteed. The sections are sized to
                # fit instead, and the row still stretches to the window.
                return fixed
            counts = list(range(2, max(limit, 2) + 1))
        else:
            # Allowed to go all the way to one section per row: below that nothing
            # can help, because a single section is already wider than the window.
            counts = list(range(1, min(self.MENU_MAX_ROWS, len(sections)) + 1))
        for count in counts:
            if count == 2:
                fixed = self._fixed_two_rows(sections)
                if fixed:
                    widest = max(sum(max(s.winfo_reqwidth(), 1) + self.SECTION_GAP for s in row)
                                 for row in fixed)
                    if widest <= budget or count == counts[-1]:
                        return fixed
                    continue
            runs = self._split_evenly(widths, count)
            widest = max(sum(widths[a:b]) for a, b in runs)
            if widest <= budget or count == counts[-1]:
                return [sections[a:b] for a, b in runs]
        return [sections]

    def _compute_menu_plan(self):
        """Work out the layout without touching any widgets, so a resize that changes
        nothing costs nothing."""
        sections = self._menu_sections()
        if not sections:
            return None, []
        budget = self.root.winfo_width() - 2 * self.SECTION_GAP - 24
        if budget <= 1:
            budget = sum(max(s.winfo_reqwidth(), 1) for s in sections)

        # The Timeline box always floats beside the rows, spanning their full
        # height. It only rejoins the flow when it could not be given a column at
        # all -- a single-row bar, or a window too narrow to hold it plus anything
        # else, where spanning would leave no usable width for the rest.
        if self._timeline_frame in sections and self.menu_rows_mode != "one":
            others = [s for s in sections if s is not self._timeline_frame]
            timeline_width = max(self._timeline_frame.winfo_reqwidth(), 1) + self.SECTION_GAP
            widest_other = max((max(s.winfo_reqwidth(), 1) for s in others), default=0)
            if others and timeline_width + widest_other <= budget:
                return self._timeline_frame, self._plan_menu_rows(others, budget - timeline_width)

        return None, self._plan_menu_rows(sections, budget)

    def _layout_menu(self, two_rows=None):
        """Pack the toolbar. Sections flow across the rows and each row expands to
        fill the width, so the bar always spans the window without overflowing."""
        if two_rows is not None:
            self.menu_rows_mode = "two" if two_rows else "one"
        spanning, plan = self._compute_menu_plan()
        if not plan:
            return
        self._apply_menu_plan(spanning, plan)

    def _apply_menu_plan(self, spanning, plan):
        self.menu_two_rows = len(plan) > 1

        for row in self.menu_rows:
            for child in list(row.pack_slaves()):
                child.pack_forget()
            row.pack_forget()
        for child in list(self.top_inner.pack_slaves()):
            if child is not self.top_rows:
                child.pack_forget()
        self.top_rows.pack_forget()

        if spanning is not None:
            # Packed before top_rows so it claims its requested width first; the rows
            # expand into what is left rather than the other way round.
            # expand as well as fill: the rows are narrower than the window, and the
            # timeline takes the slack so the toolbar fills the row rather than
            # trailing off into empty panel. side=RIGHT puts it at the far end of the
            # toolbar, past both rows of boxes.
            spanning.pack(in_=self.top_inner, side=tk.RIGHT, padx=4, pady=3,
                          fill=tk.BOTH, expand=True)
        self.top_rows.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        for row_frame, row_sections in zip(self._menu_row_frames(len(plan)), plan):
            row_frame.pack(fill=tk.BOTH, expand=True)
            for section in row_sections:
                # expand spreads the leftover width across the row, so every row ends
                # flush with the others -- justified, with the slack in the gaps.
                section.pack(in_=row_frame, side=tk.LEFT, padx=4, pady=3,
                             fill=tk.BOTH, expand=True)
        self._menu_plan_key = self._plan_key(spanning, plan)

    @staticmethod
    def _plan_key(spanning, plan):
        return (spanning is not None, tuple(tuple(id(s) for s in row) for row in plan))

    def _reflow_menu(self, force=False):
        """Recompute the flow and only repack when it actually changed, so dragging a
        window edge does not rebuild the toolbar on every pixel."""
        try:
            spanning, plan = self._compute_menu_plan()
        except Exception:
            return
        if not plan:
            return
        if force or self._plan_key(spanning, plan) != getattr(self, "_menu_plan_key", None):
            self._apply_menu_plan(spanning, plan)

    def toggle_menu_rows(self):
        # An explicit toggle also pins the mode, otherwise auto would undo it on the
        # next resize.
        self.menu_rows_mode = "two" if not self.menu_two_rows else "one"
        if hasattr(self, "menu_rows_mode_var"):
            self.menu_rows_mode_var.set(self.menu_rows_mode)
        self._reflow_menu(force=True)
        self._save_config()

    def set_menu_rows_mode(self, mode):
        if mode not in ("auto", "one", "two"):
            return
        self.menu_rows_mode = mode
        if hasattr(self, "menu_rows_mode_var"):
            self.menu_rows_mode_var.set(mode)
        self._reflow_menu(force=True)
        self._save_config()

    def set_menu_position(self, position):
        if position not in ("top", "bottom"):
            return
        self.menu_position = position
        if hasattr(self, "menu_position_var"):
            self.menu_position_var.set(position)
        self._apply_menu_position()
        self._save_config()

    def _apply_menu_position(self):
        """Re-dock the toolbar. Both the bar and the canvas are re-packed because
        pack order, not just the side, decides which one ends up against the edge."""
        self._menu_side = self.menu_position
        try:
            self.top_bar.pack_forget()
            self.canvas_container.pack_forget()
        except Exception:
            return

        if self._menu_side == "bottom":
            self.top_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=8)
            self.canvas_container.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=(8, 0))
        else:
            self.top_bar.pack(side=tk.TOP, fill=tk.X, padx=10, pady=8)
            self.canvas_container.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        self._reflow_menu(force=True)

    # ----------------------
    # Preferences
    # ----------------------
    # ----------------------
    # Colour themes
    # ----------------------
    # att/def are the two teams, line is drawn arrows, sign is markers and text.
    # The colour-blind sets come from the Okabe-Ito palette, which is built to stay
    # distinguishable under all three common forms of colour blindness -- the usual
    # red-versus-green board is exactly the pairing those readers cannot separate.
    COLOR_THEMES = {
        "Classic (black)": {
            "att": "#000000", "def": "#000000", "line": "#000000", "sign": "#000000",
            "note": "Everything in black, as on a printed sheet."},
        "Red vs Blue": {
            "att": "#c92a2a", "def": "#1864ab", "line": "#212529", "sign": "#212529",
            "note": "The familiar two-team look."},
        "Blue vs Green": {
            "att": "#1864ab", "def": "#2b8a3e", "line": "#212529", "sign": "#212529",
            "note": "Cooler pairing, still high contrast on white."},
        "Slate vs Amber": {
            "att": "#343a40", "def": "#e8590c", "line": "#495057", "sign": "#343a40",
            "note": "Muted, easy on the eye for long sessions."},
        "Colour-blind: Blue / Orange": {
            "att": "#0072b2", "def": "#e69f00", "line": "#000000", "sign": "#000000",
            "note": "Okabe-Ito. Safe for red-green colour blindness (all types)."},
        "Colour-blind: Blue / Vermillion": {
            "att": "#0072b2", "def": "#d55e00", "line": "#000000", "sign": "#000000",
            "note": "Okabe-Ito. Strong separation in brightness as well as hue."},
        "Colour-blind: Teal / Magenta": {
            "att": "#009e73", "def": "#cc79a7", "line": "#000000", "sign": "#000000",
            "note": "Okabe-Ito. Works for blue-yellow colour blindness too."},
        "Nijmegen Flames": {
            "att": "#e8262b", "def": "#4c4c4e", "line": "#000000", "sign": "#000000",
            "note": "Club colours on the players only; arrows and marks stay black."},
        "Nijmegen Hot Shots": {
            "att": "#e8262b", "def": "#111111", "line": "#111111", "sign": "#e8262b",
            "note": "Club colours: the logo's red on black."},
    }

    def apply_color_theme(self, name):
        """Switch every colour at once, on the board as well as for what comes next."""
        theme = self.COLOR_THEMES.get(name)
        if not theme:
            return False
        self.color_theme = name
        self.att_color = theme["att"]
        self.def_color = theme["def"]
        self.line_color = theme["line"]
        self.sign_color = theme["sign"]

        for attribute, colour in (("btn_att_color", self.att_color),
                                  ("btn_def_color", self.def_color),
                                  ("btn_line_color", self.line_color),
                                  ("btn_sign_color", self.sign_color)):
            swatch = getattr(self, attribute, None)
            if swatch is not None:
                try:
                    swatch.config(bg=colour)
                except Exception:
                    pass

        # Repaint what is already out there, rather than only affecting new pieces.
        for token in self._all_tokens():
            colour = self.att_color if self._token_team(token) == "att" else self.def_color
            token["color"] = colour
            skip = set(token.get("decor_ids", ())) | set(token.get("halo_ids", ()))
            for item in self._token_items(token):
                if item in skip:
                    continue
                try:
                    self.canvas.itemconfig(item, fill=colour)
                except Exception:
                    pass
        for cid, meta in list(self.drawn_items.items()):
            if meta.get("decor") or meta.get("type") == "image":
                continue
            colour = self.sign_color if meta.get("type") in ("sign", "text") else self.line_color
            meta["color"] = colour
            for option in meta.get("color_options") or ("fill",):
                try:
                    self.canvas.itemconfig(cid, **{option: colour})
                except Exception:
                    pass

        self._save_config()
        return True

    def _settings_snapshot(self):
        """Everything the Preferences dialog can change, as it stands right now."""
        return {
            "rink_mode": self.rink_mode,
            "half_rink": self.half_rink,
            "goals": bool(self.goals_visible_var.get()),
            "snap_player": bool(self.snap_player_var.get()),
            "snap_angle": bool(self.snap_angle_var.get()),
            "grid": bool(self.grid_var.get()),
            "ghosting": bool(self.ghosting_var.get()),
            "menu_rows_mode": self.menu_rows_mode,
            "menu_position": self.menu_position,
            "color_theme": self.color_theme,
            "att_color": self.att_color,
            "def_color": self.def_color,
            "line_color": self.line_color,
            "sign_color": self.sign_color,
        }

    def _restore_settings(self, snapshot):
        """Put the board back the way `_settings_snapshot` found it."""
        if not snapshot:
            return
        self.set_rink_mode(snapshot["rink_mode"],
                           half=snapshot.get("half_rink", self.half_rink))
        for name, variable in (("goals", self.goals_visible_var),
                               ("snap_player", self.snap_player_var),
                               ("snap_angle", self.snap_angle_var),
                               ("grid", self.grid_var),
                               ("ghosting", self.ghosting_var)):
            variable.set(snapshot[name])
        if snapshot["color_theme"] != self.color_theme:
            self.apply_color_theme(snapshot["color_theme"])
        # The individual pickers may have moved a colour without changing the theme.
        self.att_color = snapshot["att_color"]
        self.def_color = snapshot["def_color"]
        self.line_color = snapshot["line_color"]
        self.sign_color = snapshot["sign_color"]
        for attribute, colour in (("btn_att_color", self.att_color),
                                  ("btn_def_color", self.def_color),
                                  ("btn_line_color", self.line_color),
                                  ("btn_sign_color", self.sign_color)):
            swatch = getattr(self, attribute, None)
            if swatch is not None:
                try:
                    swatch.config(bg=colour)
                except Exception:
                    pass
        if snapshot["menu_rows_mode"] != self.menu_rows_mode:
            self.set_menu_rows_mode(snapshot["menu_rows_mode"])
        if snapshot["menu_position"] != self.menu_position:
            self.set_menu_position(snapshot["menu_position"])
        self.toggle_grid_visuals()
        self._update_indicators()
        self.redraw_canvas()
        self._save_config()

    def open_preferences(self):
        before = self._settings_snapshot()
        win = tk.Toplevel(self.root)
        win.title("Preferences")
        win.transient(self.root)
        win.resizable(False, False)

        def heading(text, row):
            tk.Label(win, text=text, font=(self.UI_FONT, 9, "bold")).grid(
                row=row, column=0, columnspan=2, sticky="w", padx=10, pady=(10, 2))
            return row + 1

        row = heading("Board", 0)
        # The rink is a choice of three fields rather than a switch, so it gets a row
        # of radio buttons instead of a checkbox. The half rink is a separate
        # question -- every field has a half -- so it is a checkbox beside them.
        rink_row = tk.Frame(win)
        rink_row.grid(row=row, column=0, columnspan=2, sticky="w", padx=18)
        tk.Label(rink_row, text="Rink").pack(side=tk.LEFT, padx=(0, 6))
        for mode in self.RINK_ORDER:
            length, width = self.RINK_SIZES[mode]
            tk.Radiobutton(rink_row, text=f"{self.RINK_LABELS[mode]} "
                                          f"({length:g}×{width:g} m)",
                           value=mode, variable=self.rink_mode_var,
                           command=lambda m=mode: self.set_rink_mode(m)).pack(side=tk.LEFT)
        row += 1
        # The variable is already flipped by the time the command runs, so the new
        # state is read back off it rather than being worked out here.
        tk.Checkbutton(win, text="Half rink (one end of the field)",
                       variable=self.half_rink_var, anchor="w",
                       command=lambda: self.set_half_rink(self.half_rink_var.get())).grid(
            row=row, column=0, columnspan=2, sticky="w", padx=18)
        row += 1
        for text, var in (("Show goals", self.goals_visible_var),
                          ("Snap players", self.snap_player_var),
                          ("Snap angles", self.snap_angle_var),
                          ("Snap to grid", self.grid_var),
                          ("Ghosting", self.ghosting_var)):
            tk.Checkbutton(win, text=text, variable=var, anchor="w").grid(
                row=row, column=0, columnspan=2, sticky="w", padx=18)
            row += 1

        row = heading("Menu", row)
        position_var = tk.StringVar(value=self.menu_position)
        tk.Label(win, text="Position:").grid(row=row, column=0, sticky="w", padx=18, pady=2)
        ttk.Combobox(win, textvariable=position_var, values=["top", "bottom"],
                     width=10, state="readonly").grid(row=row, column=1, sticky="w", padx=10, pady=2)
        row += 1
        rows_var = tk.StringVar(value=self.menu_rows_mode)
        tk.Label(win, text="Rows:").grid(row=row, column=0, sticky="w", padx=18, pady=2)
        ttk.Combobox(win, textvariable=rows_var, values=["auto", "one", "two"],
                     width=10, state="readonly").grid(row=row, column=1, sticky="w", padx=10, pady=2)
        row += 1

        row = heading("Colours", row)
        theme_var = tk.StringVar(value=self.color_theme)
        tk.Label(win, text="Theme:").grid(row=row, column=0, sticky="w", padx=18, pady=2)
        theme_box = ttk.Combobox(win, textvariable=theme_var,
                                 values=list(self.COLOR_THEMES.keys()),
                                 width=22, state="readonly")
        theme_box.grid(row=row, column=1, sticky="w", padx=10, pady=2)
        row += 1
        theme_note = tk.Label(win, text=self.COLOR_THEMES.get(self.color_theme, {})
                              .get("note", ""), font=(self.UI_FONT, 8), fg=self.C_MUTED,
                              wraplength=260, justify=tk.LEFT)
        theme_note.grid(row=row, column=0, columnspan=2, sticky="w", padx=18)
        row += 1

        def on_theme_chosen(_event=None):
            name = theme_var.get()
            theme_note.config(text=self.COLOR_THEMES.get(name, {}).get("note", ""))
            self.apply_color_theme(name)

        theme_box.bind("<<ComboboxSelected>>", on_theme_chosen)

        for text, command in (("Attackers", self.choose_att_color),
                              ("Defenders", self.choose_def_color),
                              ("Lines", self.choose_line_color),
                              ("Signs", self.choose_sign_color)):
            tk.Button(win, text=text, command=command, width=self.BTN_W).grid(
                row=row, column=0, sticky="w", padx=18, pady=2)
            row += 1

        btns = tk.Frame(win)
        btns.grid(row=row, column=0, columnspan=2, pady=10)

        def apply_and_close():
            self.set_menu_rows_mode(rows_var.get())
            self.set_menu_position(position_var.get())
            self.toggle_grid_visuals()
            self._update_indicators()
            self.redraw_canvas()
            self._save_config()
            win.destroy()

        def cancel():
            """Put back everything the dialog changed.

            The checkboxes are bound straight to the board's own variables and the
            theme picker applies as it is chosen, so the settings are already live by
            the time this button is pressed -- Cancel has to undo them rather than
            simply close the window."""
            self._restore_settings(before)
            win.destroy()

        tk.Button(btns, text="Save & Close", command=apply_and_close, width=self.BTN_W).pack(side=tk.LEFT, padx=6)
        tk.Button(btns, text="Cancel", command=cancel, width=self.BTN_W).pack(side=tk.LEFT, padx=6)
        win.protocol("WM_DELETE_WINDOW", cancel)

    def _update_roster(self, event=None):
        for token_id in list(self.tokens.keys()):
            data = self.tokens[token_id]
            try:
                self.canvas.delete(token_id)
            except Exception:
                pass
        self.tokens.clear()
        self.groups.clear()
        self.clear_selection()
        
        self.undo_stack.clear()
        self.redo_stack.clear()

        try:
            num_att = int(self.att_spinbox.get())
        except Exception:
            num_att = 5
        try:
            num_def = int(self.def_spinbox.get())
        except Exception:
            num_def = 5

        att_shape = self.att_shape_var.get()
        def_shape = self.def_shape_var.get()

        player_size = max(6, int(self.player_size_var.get()))

        for i in range(1, num_att + 1):
            x = self.width * 0.3
            y = self.height * (i / (num_att + 1))
            self._create_token(x, y, f"A{i}", shape=att_shape, color=self.att_color, size=player_size, team="att")

        for i in range(1, num_def + 1):
            x = self.width * 0.7
            y = self.height * (i / (num_def + 1))
            self._create_token(x, y, f"D{i}", shape=def_shape, color=self.def_color, size=player_size, team="def")

        # The ball starts on the centre spot. Using the middle of the canvas put it
        # in the middle of the board, which is only the centre spot on a full rink.
        if getattr(self, "pitch_scale", None):
            ball_x, ball_y = self._faceoff_point_px()
        else:
            ball_x, ball_y = self.width * 0.5, self.height * 0.5
        self._create_token(ball_x, ball_y, "B", shape="ball", color="black", size=player_size)

    def _create_token(self, x, y, label, shape="circle", color="black", outline=TOKEN_OUTLINE, stipple="", size=None, team=None):
        base_size = 14 if size is None else max(6, int(size))
        shape_lower = shape.lower()
        shape_ids = []
        s = base_size

        rect_kwargs = {"fill": color, "outline": outline, "width": 3, "tags": ("token",)}
        if stipple: rect_kwargs["stipple"] = stipple

        line_kwargs = {"fill": color, "width": 3, "tags": ("token",)}
        if stipple: line_kwargs["stipple"] = stipple
        # X and plus players have no fill to carry an outline, so the outline is a
        # wider stroke laid underneath. All halos go down before any of the coloured
        # strokes, otherwise the second halo would paint over the first stroke.
        halo_kwargs = {"fill": outline, "width": line_kwargs["width"] + 6, "tags": ("token",)}
        # Order outwards is player, thin dark edge, white outline. A Tk outline is
        # centred on its path and each layer is drawn over the one below, so the widths
        # decide what stays visible: the dark edge sits *on* the shape's own border
        # (1px of it showing), and the white ring underneath is wide enough to clear it
        # by 3px. No fill on the rings, or they would show through a ghosted player.
        rect_kwargs["outline"] = self.TOKEN_EDGE
        rect_kwargs["width"] = 2
        halo_shape_kwargs = {"fill": "", "outline": outline,
                             "width": rect_kwargs["width"] + 4, "tags": ("token",)}
        # X and plus players have no fill to carry an outline, so both rings are wider
        # strokes laid underneath, widest first.
        edge_line_kwargs = {"fill": self.TOKEN_EDGE,
                            "width": line_kwargs["width"] + 2, "tags": ("token",)}
        decor_ids = []
        halo_ids = []

        def filled(create, *coords):
            """White ring, then the dark edge and fill on top of it."""
            halo_id = create(*coords, **halo_shape_kwargs)
            decor_ids.append(halo_id)
            halo_ids.append(halo_id)
            shape_ids.append(create(*coords, **rect_kwargs))

        if shape_lower == "square":
            # A polygon rather than a rectangle: Tk rectangles are axis-aligned and
            # cannot be rotated, so Rotate Sel could never turn a square player.
            filled(self.canvas.create_polygon, x-s, y-s, x+s, y-s, x+s, y+s, x-s, y+s)
        elif shape_lower == "ball":
            b = max(4, base_size // 2)
            filled(self.canvas.create_oval, x-b, y-b, x+b, y+b)
        elif shape_lower == "triangle":
            filled(self.canvas.create_polygon, x, y-s, x-s, y+s, x+s, y+s)
        elif shape_lower in ("x", "plus"):
            strokes = [(x-s, y-s, x+s, y+s), (x-s, y+s, x+s, y-s)] if shape_lower == "x" \
                else [(x-s, y, x+s, y), (x, y-s, x, y+s)]
            # All halos go down before any of the coloured strokes, otherwise the
            # second halo would paint over the first stroke.
            for stroke in strokes:
                halo_id = self.canvas.create_line(*stroke, **halo_kwargs)
                decor_ids.append(halo_id)
                halo_ids.append(halo_id)
            for stroke in strokes:
                decor_ids.append(self.canvas.create_line(*stroke, **edge_line_kwargs))
            for stroke in strokes:
                shape_ids.append(self.canvas.create_line(*stroke, **line_kwargs))
        else:
            filled(self.canvas.create_oval, x-s, y-s, x+s, y+s)

        # The dark ring travels with the token like any other piece of it, so it joins
        # shape_ids -- but at the end, because shape_ids[0] is the token's identity and
        # the colour changes that key off it must land on the coloured shape. It was
        # created first all the same, which is what puts it underneath.
        shape_ids = shape_ids + decor_ids

        # The first id stays the token's identity for label lookups, undo and macros;
        # the rest used to be created and then dropped on the floor, which is why the
        # second stroke of an X or plus never followed the token anywhere.
        shape_id = shape_ids[0]

        base_font_size = max(6, int(base_size * 0.57))
        text_offsets = []
        text_ids = []
        if shape_lower != "ball":
            for ox, oy in [(-1, -1), (1, -1), (-1, 1), (1, 1), (-1, 0), (1, 0), (0, -1), (0, 1)]:
                otid = self.canvas.create_text(
                    x + ox, y + oy,
                    text=label,
                    fill="black",
                    font=(self.UI_FONT, base_font_size, "bold"),
                    tags=("token",)
                )
                text_ids.append(otid)
                text_offsets.append((ox, oy))

            main_otid = self.canvas.create_text(
                x, y,
                text=label,
                fill="white",
                font=(self.UI_FONT, base_font_size, "bold"),
                tags=("token",)
            )
            text_ids.append(main_otid)
            text_offsets.append((0, 0))

        token = {
            "shape_id": shape_id,
            "shape_ids": shape_ids,
            "decor_ids": decor_ids,
            "halo_ids": halo_ids,
            "text_ids": text_ids,
            "text_offsets": text_offsets,
            "shape": shape,
            "label": label,
            # "label" stays the unique internal id (A1..A5 / D1..D5) that undo,
            # macros and _get_sid_by_label all key on; "position" is the tactical
            # role shown on the token, so a formation can rename what the user sees
            # without breaking those lookups or colliding across the two teams.
            "position": None,
            "team": team,
            "color": color,
            "locked": False,
            "size": base_size,
            "font_size": base_font_size,
            "starting_pos": (x, y),
            "ghost_count": 0,
            "angle": 0,
            "attached_lines_start": [], 
            "attached_lines_end": [],
            "outline": outline,
            "stipple": stipple
        }

        for extra_id in shape_ids:
            self.tokens[extra_id] = token
        self.tokens[shape_id] = token
        for tid in text_ids:
            self.tokens[tid] = token
            
        return shape_id

    def lock_selected(self):
        labels = [self.tokens[sid]["label"] for sid in self.selected_tokens if sid in self.tokens]
        if labels:
            self.push_command(LockCommand(self, labels, lock_state=True))

    def unlock_selected(self):
        labels = [self.tokens[sid]["label"] for sid in self.selected_tokens if sid in self.tokens]
        if labels:
            self.push_command(LockCommand(self, labels, lock_state=False))

    def group_selected(self):
        labels = [self.tokens[sid]["label"] for sid in self.selected_tokens if sid in self.tokens]
        if len(labels) < 2:
            messagebox.showwarning("Warning", "Select at least 2 tokens to group.")
            return
        self.push_command(GroupCommand(self, labels, is_ungroup=False))
        messagebox.showinfo("Group", f"Successfully grouped {len(labels)} tokens together!")

    def ungroup_selected(self):
        labels = [self.tokens[sid]["label"] for sid in self.selected_tokens if sid in self.tokens]
        if labels:
            self.push_command(GroupCommand(self, labels, is_ungroup=True))
            messagebox.showinfo("Ungroup", f"Ungrouped selected tokens.")

    def distribute_horizontally(self):
        if len(self.selected_tokens) < 2:
            messagebox.showwarning("Warning", "Select at least 2 tokens to distribute.")
            return
        unique_tokens = []
        seen = set()
        for sid in self.selected_tokens:
            token = self.tokens.get(sid)
            if token and token["shape_id"] not in seen:
                seen.add(token["shape_id"])
                unique_tokens.append(token)
        
        if len(unique_tokens) < 2:
            return
            
        def get_center_x(token):
            coords = self.canvas.coords(token["shape_id"])
            return (coords[0] + coords[2]) / 2 if coords else 0
            
        sorted_tokens = sorted(unique_tokens, key=get_center_x)
        c_left = get_center_x(sorted_tokens[0])
        c_right = get_center_x(sorted_tokens[-1])
        
        n = len(sorted_tokens)
        spacing = (c_right - c_left) / (n - 1) if n > 1 else 0
            
        label_moves = {}
        for i, token in enumerate(sorted_tokens):
            coords = self.canvas.coords(token["shape_id"])
            if coords:
                cx = (coords[0] + coords[2]) / 2
                target_cx = c_left + i * spacing
                dx = target_cx - cx
                if abs(dx) > 0.0001:
                    label_moves[token["label"]] = (dx, 0)
        
        if label_moves:
            self.push_command(MoveTokensCommand(self, label_moves))

    def distribute_vertically(self):
        if len(self.selected_tokens) < 2:
            messagebox.showwarning("Warning", "Select at least 2 tokens to distribute.")
            return
        unique_tokens = []
        seen = set()
        for sid in self.selected_tokens:
            token = self.tokens.get(sid)
            if token and token["shape_id"] not in seen:
                seen.add(token["shape_id"])
                unique_tokens.append(token)

        if len(unique_tokens) < 2:
            return

        def get_center_y(token):
            coords = self.canvas.coords(token["shape_id"])
            return (coords[1] + coords[3]) / 2 if coords else 0

        sorted_tokens = sorted(unique_tokens, key=get_center_y)
        c_top = get_center_y(sorted_tokens[0])
        c_bottom = get_center_y(sorted_tokens[-1])

        n = len(sorted_tokens)
        spacing = (c_bottom - c_top) / (n - 1) if n > 1 else 0

        label_moves = {}
        for i, token in enumerate(sorted_tokens):
            coords = self.canvas.coords(token["shape_id"])
            if coords:
                cy = (coords[1] + coords[3]) / 2
                target_cy = c_top + i * spacing
                dy = target_cy - cy
                if abs(dy) > 0.0001:
                    label_moves[token["label"]] = (0, dy)

        if label_moves:
            self.push_command(MoveTokensCommand(self, label_moves))

    def align_tokens(self, alignment_type):
        if not self.selected_tokens:
            return
        unique_tokens = []
        seen = set()
        for sid in self.selected_tokens:
            token = self.tokens.get(sid)
            if token and token["shape_id"] not in seen:
                seen.add(token["shape_id"])
                unique_tokens.append(token)
        if not unique_tokens:
            return
            
        coords_list = [self.canvas.coords(t["shape_id"]) for t in unique_tokens]
        valid_coords = [c for c in coords_list if c]
        if not valid_coords:
            return
            
        label_moves = {}
        if alignment_type == "horizontal":
            avg_cy = sum((c[1] + c[3])/2 for c in valid_coords) / len(valid_coords)
            for t, c in zip(unique_tokens, coords_list):
                if not c: continue
                cy = (c[1] + c[3]) / 2
                dy = avg_cy - cy
                if abs(dy) > 1e-6:
                    label_moves[t["label"]] = (0, dy)
        elif alignment_type == "vertical":
            avg_cx = sum((c[0] + c[2]) / 2 for c in valid_coords) / len(valid_coords)
            for t, c in zip(unique_tokens, coords_list):
                if not c: continue
                cx = (c[0] + c[2]) / 2
                dx = avg_cx - cx
                if abs(dx) > 1e-6:
                    label_moves[t["label"]] = (dx, 0)
                    
        if label_moves:
            self.push_command(MoveTokensCommand(self, label_moves))

    def get_grid_snapped_point(self, x, y):
        if not self.grid_var.get():
            return x, y
        return round(x / self.GRID) * self.GRID, round(y / self.GRID) * self.GRID

    def get_snap_point(self, x, y):
        if not self.snap_player_var.get():
            return x, y

        snap_threshold = 35.0
        def find_best_point(tokens):
            best_pt = (x, y)
            min_dist = float('inf')
            for token in tokens:
                # The same square the ball snaps to, so a line end and a ball land on
                # the same place for every shape.
                box = self._token_snap_box(token)
                if not box:
                    continue
                x1_t, y1_t, x2_t, y2_t = box
                cx = (x1_t + x2_t) / 2
                cy = (y1_t + y2_t) / 2
                for px, py in [(cx, y1_t), (cx, y2_t), (x1_t, cy), (x2_t, cy), (cx, cy)]:
                    dist = math.hypot(px - x, py - y)
                    if dist < min_dist and dist <= snap_threshold:
                        min_dist = dist
                        best_pt = (px, py)
            return best_pt if min_dist != float('inf') else None

        ghost_tokens = [token for token in self.tokens.values() if isinstance(token, dict) and token.get("shape_id") and token.get("is_ghost")]
        ghost_point = find_best_point(ghost_tokens)
        if ghost_point is not None:
            return ghost_point

        real_tokens = [token for token in self.tokens.values() if isinstance(token, dict) and token.get("shape_id") and not token.get("is_ghost")]
        real_point = find_best_point(real_tokens)
        if real_point is not None:
            return real_point

        return x, y

    BALL_SNAP_MARGIN = 26.0     # how far outside a player the ball still clicks on

    def _token_box(self, token):
        """The extent of everything the token is drawn from.

        bbox(shape_id) is not it: for a plus that is the first stroke alone (a flat
        36x8 box), and for the filled shapes it varies with the stroke width."""
        boxes = [self.canvas.bbox(item) for item in self._token_items(token)]
        boxes = [box for box in boxes if box]
        if not boxes:
            return None
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    def _token_snap_box(self, token):
        """The square a player snaps against: its centre, plus its nominal size in
        every direction. Derived from the size rather than the drawing, so a circle, a
        square, a triangle, an X and a plus of the same size all snap identically --
        they used to differ by up to 12px because each shape paints a different box."""
        box = self._token_box(token)
        if not box:
            return None
        cx, cy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
        half = max(4.0, float(token.get("size", 14)))
        return (cx - half, cy - half, cx + half, cy + half)

    def get_ball_snap_point(self, x, y):
        """Snap the ball onto the middle of the nearest player edge.

        Part of Snap Players, so the toggle governs it. Only the four edge midpoints
        count, never the centre: a ball sitting on a player's midriff reads as the
        player standing on it, while one parked against an edge reads as the player
        having it on their stick."""
        if not self.snap_player_var.get():
            return x, y
        best_point, best_dist = None, float("inf")
        seen = set()
        for token in self.tokens.values():
            if not isinstance(token, dict):
                continue
            sid = token.get("shape_id")
            if sid is None or sid in seen:
                continue
            seen.add(sid)
            box = self._token_snap_box(token)
            if not box:
                continue
            x1, y1, x2, y2 = box
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            # Scale the catch radius with the player, so big tokens are not harder to
            # hit than small ones.
            reach = self.BALL_SNAP_MARGIN + max(x2 - x1, y2 - y1) / 2
            for px, py in ((cx, y1), (cx, y2), (x1, cy), (x2, cy)):
                dist = math.hypot(px - x, py - y)
                if dist < best_dist and dist <= reach:
                    best_point, best_dist = (px, py), dist
        return best_point if best_point else (x, y)

    def _snap_ball_item(self, cid):
        """Re-seat an already-placed ball on the nearest player edge."""
        meta = self.drawn_items.get(cid) or {}
        if meta.get("type") != "sign" or str(meta.get("sign_type", "")).lower() not in \
                ("ball", "dot", "circle"):
            return False
        group = meta.get("group")
        box = self.canvas.bbox(group) if group else self.canvas.bbox(cid)
        if not box:
            return False
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        nx, ny = self.get_ball_snap_point(cx, cy)
        if (nx, ny) == (cx, cy):
            return False
        # A ball is a body plus its holes, so it moves as the group it was stamped as.
        self.canvas.move(group or cid, nx - cx, ny - cy)
        return True

    def get_angle_snapped_endpoint(self, x1, y1, x2, y2):
        if not self.snap_angle_var.get():
            return x2, y2
        dx = x2 - x1
        dy = y2 - y1
        dist = math.hypot(dx, dy)
        if dist == 0: return x2, y2
        angle = math.atan2(dy, dx)
        step = math.pi / 4  
        snapped_angle = round(angle / step) * step
        return x1 + dist * math.cos(snapped_angle), y1 + dist * math.sin(snapped_angle)

    def adjust_endpoints(self, x1, y1, x2, y2):
        if self.snap_angle_var.get():
            x2, y2 = self.get_angle_snapped_endpoint(x1, y1, x2, y2)
        if self.snap_player_var.get():
            x1, y1 = self.get_snap_point(x1, y1)
            x2, y2 = self.get_snap_point(x2, y2)
        return x1, y1, x2, y2
        
    def move_attached_line_ends(self, token, dx, dy):
        """Carry the ends of any arrows snapped to this player along with it.

        Nudging the item's first or last coordinate pair is right for a plain
        two-point line and wrong for everything else the tools draw. A shot's head is
        a polygon whose last coordinate is a base corner, so dragging that one corner
        folded the head up and the arrow came out shorter than the player had moved;
        a dribble is one long polyline whose final segment stretched on its own while
        the rest of the wave stayed where it was. Here every point is weighted by how
        far along the arrow it lies, so the drawing follows the player as a whole
        while its far end stays put."""
        if not isinstance(token, dict):
            return
        # Where the player is now: the end of an arrow nearest to that is the end that
        # is attached to them.
        near = self._token_centre_px(token)
        if not near:
            return
        for key in ("attached_lines_start", "attached_lines_end"):
            ids = token.get(key)
            if not ids:
                continue
            live = []
            for cid in list(ids):
                if self._stretch_attached_end(cid, dx, dy, near):
                    live.append(cid)
            # Ids of arrows that have since been deleted are dropped rather than
            # carried forever.
            token[key] = live

    def _stretch_attached_end(self, cid, dx, dy, near):
        """Move one drawing's attached end. False if the item is no longer there."""
        try:
            coords = list(self.canvas.coords(cid))
        except Exception:
            return False
        if len(coords) < 4:
            return bool(coords)
        axis = self._drawing_axis(cid, near)
        if not axis:
            return True
        moved = self._stretched_points(coords, axis[0], axis[1], dx, dy)
        if moved is None:
            return True
        self.canvas.coords(cid, *moved)
        return True

    def _drawing_axis(self, cid, near):
        """The line an arrow runs along, and which end of it is the attached one.

        Measured from the drawing as it stands, not from the record the tool drew it
        from. That record is in pixels from the moment of drawing, and a window
        resize, a change of field or a zoom moves the arrow without touching it -- so
        the weighting was computed against a line that was no longer there, every
        point came out at the same weight, and the whole arrow jumped along with the
        player instead of stretching from its far end. Which end is attached is
        decided by which end is nearer the player, which needs no record at all."""
        best, span = None, -1.0
        for other in self._drawn_siblings(cid):
            try:
                points = self.canvas.coords(other)
            except Exception:
                continue
            if not points or len(points) < 4:
                continue
            start, end = (points[0], points[1]), (points[-2], points[-1])
            length = math.hypot(end[0] - start[0], end[1] - start[1])
            if length > span:
                best, span = (start, end), length
        if not best:
            return None
        start, end = best
        to_start = math.hypot(start[0] - near[0], start[1] - near[1])
        to_end = math.hypot(end[0] - near[0], end[1] - near[1])
        # anchor stays put, head follows the player.
        return (end, start) if to_start <= to_end else (start, end)

    def _stretched_points(self, coords, anchor, head, dx, dy):
        """One drawing's points with its attached end moved by (dx, dy).

        Shared by the two callers so a hand drag and the animation agree on what
        "the arrow follows the player" means."""
        if len(coords) < 4 or not anchor or not head:
            return None
        vx, vy = head[0] - anchor[0], head[1] - anchor[1]
        span2 = vx * vx + vy * vy

        def weight(px, py):
            if span2 <= 0:
                return 1.0
            w = ((px - anchor[0]) * vx + (py - anchor[1]) * vy) / span2
            return min(1.0, max(0.0, w))

        points = [(coords[i], coords[i + 1]) for i in range(0, len(coords) - 1, 2)]
        weights = [weight(px, py) for px, py in points]
        lo, hi = min(weights), max(weights)
        if hi - lo < 0.5:
            # A compact piece -- an arrowhead -- is rigidly fixed to the tip of the
            # shaft, not something to stretch: it travels as one, by the weight of
            # whichever of its corners sits nearest the end being moved.
            weights = [hi] * len(weights)
        else:
            # The body of the arrow: the far end stays, the attached end moves the
            # whole way, everything between follows in proportion.
            weights = [(w - lo) / (hi - lo) for w in weights]
        return [value for (px, py), w in zip(points, weights)
                for value in (px + dx * w, py + dy * w)]

    def get_token_at_point(self, x, y, radius=20):
        def _matches(token):
            if "shape_id" not in token:
                return False
            # bbox, not coords: for a square player (a polygon) coords[1] and coords[3]
            # are both the top edge, so the centre came out a whole token too high and
            # the hit test missed. Lines drawn at square or X players never attached.
            box = self.canvas.bbox(token["shape_id"])
            if not box:
                return False
            cx = (box[0] + box[2]) / 2
            cy = (box[1] + box[3]) / 2
            return math.hypot(cx - x, cy - y) <= radius

        ghost_matches = [sid for sid, token in self.tokens.items() if _matches(token) and token.get("is_ghost")]
        if ghost_matches:
            return ghost_matches[0]

        for sid, token in self.tokens.items():
            if _matches(token):
                return sid
        return None

    def create_ghosts(self, sids):
        """Leave a faded copy of each player where it currently stands.

        Returns a spec for every ghost made, so the move that caused them can carry
        them and undo can take them away again."""
        specs = []
        for sid in sids:
            token = self.tokens.get(sid)
            if not token or token.get("is_ghost"):
                continue
            # bbox, not coords: a square player is a polygon, and reading coords as a
            # box dropped its ghost a whole token above where the player stood.
            box = self.canvas.bbox(token["shape_id"])
            if not box:
                continue

            token["ghost_count"] = token.get("ghost_count", 0) + 1
            spec = {"label": f"{token['label']} [{token['ghost_count']}]",
                    "shape": token["shape"], "color": token["color"],
                    "size": token.get("size", 14),
                    "cx": (box[0] + box[2]) / 2, "cy": (box[1] + box[3]) / 2,
                    "ghost_of": token["shape_id"]}
            self._spawn_ghost(spec)
            specs.append(spec)
        return specs

    def _spawn_ghost(self, spec):
        ghost_sid = self._create_token(spec["cx"], spec["cy"], spec["label"],
                                       shape=spec.get("shape", "circle"),
                                       color=spec.get("color", "black"),
                                       outline="#ced4da", stipple="gray50",
                                       size=spec.get("size"))
        self.tokens[ghost_sid]["is_ghost"] = True
        self.tokens[ghost_sid]["ghost_of"] = spec.get("ghost_of")

        # Give this ghost's shape + all of its text items (the black
        # outline copies plus the white center label) a shared tag so
        # they can be restacked as a single atomic group. Doing the
        # tag_lower calls one item at a time (as before) reordered the
        # items *relative to each other* on every call, since they all
        # also carry the "token" tag -- that's what caused the white
        # center text to end up buried under its own black outline
        # copies (looked like a solid black blob instead of white
        # text with a black outline).
        ghost_token = self.tokens[ghost_sid]
        ghost_tag = f"ghost_{ghost_sid}"
        for iid in self._token_items(ghost_token) + list(ghost_token.get("text_ids", [])):
            self.canvas.addtag_withtag(ghost_tag, iid)

        # Raise the whole ghost group as one call so its internal
        # stacking order (white text on top of the black outline
        # copies) is preserved, while still placing it just above the
        # pitch and below any "real" (non-ghost) tokens.
        self.canvas.tag_raise(ghost_tag, "pitch")
        return ghost_sid

    def clear_ghosts(self):
        """Remove every ghost on the board. Returns how many went."""
        removed = 0
        for token in [t for t in list(self.tokens.values()) if t.get("is_ghost")]:
            if token.get("shape_id") in self.tokens:
                self._delete_token(token)
                removed += 1
        self.selected_tokens = [s for s in self.selected_tokens if s in self.tokens]
        return removed

    def place_sign_canvas(self, x, y, sign_type, size=None):
        color = self.sign_color
        created = []
        sign_lower = sign_type.lower()
        # Signs made of several items (the ball, the X, the plus) get a shared tag so
        # they can be moved or re-snapped as one thing later.
        self._sign_group_seq = getattr(self, "_sign_group_seq", 0) + 1
        group_tag = f"signgrp{self._sign_group_seq}"
        # Parts that carry no colour of their own -- the holes punched in the ball.
        decor_items = set()
        sc = getattr(self, 'pitch_scale', 20)
        size_value = max(6, int(self.sign_size_var.get())) if size is None else max(6, int(size))
        # Every sign spans the same 2 * size_value box, so switching sign type does
        # not change how big the mark is. The Dot in particular used to be drawn at
        # half the radius of everything else.
        s = size_value
        if sign_lower == "goal":
            # The Goal sign is the exception: it always matches the cage drawn on the
            # pitch (1.6 m x 0.65 m at the current pitch scale) rather than the sign
            # size, so a stamped goal is never a different size from a real one. It
            # turns with the rink for the same reason.
            gw, gd = 1.6 * sc, 0.65 * sc
            if self.rink_rotated:
                gw, gd = gd, gw
            # A polygon, not a rectangle: Tk rectangles are axis-aligned by
            # definition, so Rotate Sel could never turn a stamped goal.
            gid = self.canvas.create_polygon(x - gw/2, y - gd/2, x + gw/2, y - gd/2,
                                             x + gw/2, y + gd/2, x - gw/2, y + gd/2,
                                             outline=color, fill=color, width=2, tags=("sign",))
            created.append(gid)
        elif sign_lower == "x":
            id1 = self.canvas.create_line(x-s, y-s, x+s, y+s, fill=color, width=2, tags=("sign",))
            id2 = self.canvas.create_line(x-s, y+s, x+s, y-s, fill=color, width=2, tags=("sign",))
            created.extend([id1, id2])
        elif sign_lower in ("ball", "dot", "circle"):
            # A plain filled dot. It used to be drawn as a floorball, with a ring of
            # holes punched in it, which at the size a ball is actually stamped read as
            # a smudge rather than as a ball. ("dot"/"circle" land here too, so macros
            # saved before the rename keep working.)
            id1 = self.canvas.create_oval(x-s, y-s, x+s, y+s, fill=color, outline=color, tags=("sign",))
            created.append(id1)
        elif sign_lower == "square":
            # Polygon for the same reason as the goal: rectangles cannot be rotated.
            id1 = self.canvas.create_polygon(x-s, y-s, x+s, y-s, x+s, y+s, x-s, y+s,
                                             outline=color, fill="", width=2, tags=("sign",))
            created.append(id1)
        elif sign_lower == "triangle":
            id1 = self.canvas.create_polygon(x, y-s, x-s, y+s, x+s, y+s, outline=color, fill="", width=2, tags=("sign",))
            created.append(id1)
        elif sign_lower == "plus":
            id1 = self.canvas.create_line(x-s, y, x+s, y, fill=color, width=2, tags=("sign",))
            id2 = self.canvas.create_line(x, y-s, x, y+s, fill=color, width=2, tags=("sign",))
            created.extend([id1, id2])
        for cid in created:
            self.canvas.addtag_withtag(group_tag, cid)
            self._register_drawn_item(cid, {"type": "sign", "sign_type": sign_type,
                                            "color": color, "size": size_value,
                                            "group": group_tag,
                                            "decor": cid in decor_items})
        if created and not getattr(self, "_replaying_sign", False):
            description = f"Sign {sign_type}"
            self.record_action_in_group(description)
            self.tag_items_with_group(created, description)
        return created

    def on_canvas_press(self, event):
        items = self.canvas.find_withtag("current")
        clicked_item = items[0] if items else None
        token = self.tokens.get(clicked_item) if clicked_item else None

        if self.paste_style_active and token:
            self._apply_copied_style_to_token(token)
            return

        clicked_drawn = None
        if clicked_item and clicked_item in self.drawn_items:
            clicked_drawn = clicked_item

        if token and self.active_tool == "ghost_reset":
            sid = token["shape_id"]
            start_pos = token.get("starting_pos")
            if start_pos:
                coords = self.canvas.coords(sid)
                if coords:
                    cx, cy = (coords[0] + coords[2]) / 2, (coords[1] + coords[3]) / 2
                    dx, dy = start_pos[0] - cx, start_pos[1] - cy
                    self.push_command(MoveTokensCommand(self, {token["label"]: (dx, dy)}))
            return

        if clicked_item in self.selection_overlay_handles and self.active_tool is None:
            handle_type = self._get_handle_type(clicked_item)
            if self._is_line_selection() and handle_type in {"line_mid", "line_start",
                                                             "line_end", "line_bend"}:
                self.line_edit_mode = True
                self.line_edit_handle = handle_type
                self.line_edit_start = (event.x, event.y)
                self.line_edit_initial_coords = self._get_selected_line_coords()
                return
            self.resize_mode = True
            self.resize_handle = clicked_item
            self.resize_start = (event.x, event.y)
            self.resize_anchor = self._get_resize_anchor(clicked_item)
            self.resize_initial_bounds = self._get_selection_bounds()
            self.resize_initial_coords = {}
            for cid in self.selected_drawn:
                coords = self.canvas.coords(cid)
                if coords:
                    self.resize_initial_coords[cid] = coords[:]
            return

        if token and self.active_tool is None:
            self.dragging_token_mode = True
            clicked = token["shape_id"]
            target_sids = {clicked}
            for g in self.groups:
                if clicked in g: target_sids.update(g)

            ctrl = (event.state & 0x4) != 0
            if ctrl:
                for sid in target_sids:
                    if sid in self.selected_tokens: self.selected_tokens.remove(sid)
                    else: self.selected_tokens.append(sid)
            else:
                if clicked not in self.selected_tokens: self.selected_tokens = list(target_sids)

            self.highlight_selected()
            # Held until the drag ends, then handed to the move command so undo takes
            # the ghost back along with the movement.
            self._drag_ghosts = (self.create_ghosts(self.selected_tokens)
                                 if self.ghosting_var.get() else [])
            # Shift at the start of the drag decides whether attached lines travel.
            self._drag_keep_attached = (event.state & 0x1) != 0
            
            self.drag_data["x"] = event.x
            self.drag_data["y"] = event.y
            self.drag_start_positions = {}
            for sid in self.selected_tokens:
                t = self.tokens.get(sid)
                if t: self.drag_start_positions[sid] = self.canvas.coords(sid)
            return

        if token and self.active_tool is not None and self.ghosting_var.get():
            self.create_ghosts([token["shape_id"]])

        if clicked_drawn and self.active_tool is None:
            self.dragging_drawn_mode = True
            ctrl = (event.state & 0x4) != 0
            family = self._drawn_siblings(clicked_drawn)
            if ctrl:
                if clicked_drawn in self.selected_drawn:
                    self.selected_drawn -= family
                else:
                    self.selected_drawn |= family
            else:
                self.selected_drawn = set(family)
            self.selected_tokens.clear()
            self.highlight_selected()
            self.drag_data["x"] = event.x
            self.drag_data["y"] = event.y
            self.drag_start_positions = {}
            for cid in self.selected_drawn:
                coords = self.canvas.coords(cid)
                if coords:
                    self.drag_start_positions[cid] = coords[:]
            return

        self.dragging_token_mode = False
        self.dragging_drawn_mode = False

        if self.active_tool is None:
            self.clear_selection()
            # Nothing of the user's own is under the pointer, so a fixture of the rink
            # itself may be: a goal, a cross, the centre line, the boards. They select
            # like anything else, which is what makes them deletable.
            part = self._pitch_part_at(event.x, event.y)
            if part:
                self.selected_pitch_parts = {part}
                self.highlight_selected()
                # No return: the rubber band still starts here, so a drag that happens
                # to begin on the boards or the centre line lassos as it always did.
                # The release drops the fixture again if the pointer actually moved.
            self.selection_start = (event.x, event.y)
            self.selection_rect = self.canvas.create_rectangle(event.x, event.y, event.x, event.y, dash=(4,4), outline="#228be6")
        elif self.active_tool == "text":
            self.place_text_canvas(event.x, event.y)
            self.active_tool = None
            self._update_indicators()
        elif self.active_tool and self.active_tool.startswith("sign_"):
            stype = self.active_tool.split("_")[1]
            px, py = event.x, event.y
            if stype.lower() in ("ball", "dot", "circle"):
                px, py = self.get_ball_snap_point(px, py)
            self.place_sign_canvas(px, py, stype)
            self.active_tool = None
            self._update_indicators()
            self.root.title("Floorball Tactics Studio - Active Mode: Select & Move")
        elif self.active_tool == "bend" and self.temp_line_start and not self.bend_control_point:
            self.bend_control_point = (event.x, event.y)
        else:
            self.temp_line_start = (event.x, event.y)
            self.current_preview_ids = []

    def on_canvas_drag(self, event):
        if self.line_edit_mode:
            if self.active_tool is not None:
                return
            line_id = self._get_selected_line_item()
            if not line_id:
                return
            coords = self.canvas.coords(line_id)
            if not coords:
                return
            dx = event.x - self.line_edit_start[0]
            dy = event.y - self.line_edit_start[1]
            new_coords = list(coords)
            if self.line_edit_handle == "line_mid":
                for idx, value in enumerate(new_coords):
                    if idx % 2 == 0:
                        new_coords[idx] = value + dx
                    else:
                        new_coords[idx] = value + dy
            elif self.line_edit_handle == "line_start":
                new_coords[0] = coords[0] + dx
                new_coords[1] = coords[1] + dy
            elif self.line_edit_handle == "line_end":
                new_coords[-2] = coords[-2] + dx
                new_coords[-1] = coords[-1] + dy
            elif self.line_edit_handle == "line_bend" and len(new_coords) >= 6:
                # Only the control point moves: the two ends stay where they were put,
                # so reshaping a curve never drags its start or its target with it.
                new_coords[2] = coords[2] + dx
                new_coords[3] = coords[3] + dy
                self._move_paired_bend(line_id, dx, dy)
            self.canvas.coords(line_id, *new_coords)
            self.line_edit_start = (event.x, event.y)
            self._draw_selection_overlay()
            return

        if self.resize_mode:
            if self.active_tool is not None:
                return
            if not self.resize_anchor or not self.resize_initial_bounds:
                return
            keep_ratio = (event.state & 0x1) != 0
            # The corner goes where the pointer goes. It used to travel a twelfth of
            # the distance and then have the result damped again on the way out, which
            # between them meant a long drag moved a sign by half a pixel -- the marks
            # looked as though they could not be resized at all.
            drag_x, drag_y = event.x, event.y
            drag_x = max(0, min(drag_x, self.width))
            drag_y = max(0, min(drag_y, self.height))
            if self.grid_var.get():
                drag_x, drag_y = self.get_grid_snapped_point(drag_x, drag_y)
            new_bounds = self._compute_resized_box(self.resize_initial_bounds, (drag_x, drag_y), self.resize_anchor, keep_ratio=keep_ratio)
            new_bounds = self._clamp_resized_box(new_bounds)
            self._apply_resized_selection(new_bounds)
            self._draw_selection_overlay()
            return

        if getattr(self, "dragging_token_mode", False):
            if self.active_tool is not None: return
            dx, dy = event.x - self.drag_data["x"], event.y - self.drag_data["y"]
            moved_sids = set(self.selected_tokens)
            for sid in list(self.selected_tokens):
                for g in self.groups:
                    if sid in g: moved_sids.update(g)

            for sid in moved_sids:
                token = self.tokens.get(sid)
                if token and token.get("locked", False): continue
                for item in self._token_items(token):
                    self.canvas.move(item, dx, dy)
                if "text_ids" in token:
                    for tid in token["text_ids"]: self.canvas.move(tid, dx, dy)
                # Snapping is what tied the arrow to the player in the first place;
                # dragging the player is normally a repositioning, not a redraw of the
                # play, so the arrow stays where it was. Hold Shift to bring it along.
                if getattr(self, "_drag_keep_attached", False):
                    self.move_attached_line_ends(token, dx, dy)

            self.drag_data["x"] = event.x
            self.drag_data["y"] = event.y
            return

        if getattr(self, "dragging_drawn_mode", False):
            dx, dy = event.x - self.drag_data["x"], event.y - self.drag_data["y"]
            id_moves = {}
            for cid in list(self.selected_drawn):
                coords = self.canvas.coords(cid)
                if not coords: continue
                new_coords = []
                for i, v in enumerate(coords):
                    if i % 2 == 0:
                        new_coords.append(v + dx)
                    else:
                        new_coords.append(v + dy)
                self.canvas.coords(cid, *new_coords)
                id_moves[cid] = (dx, dy)
            self.drag_data["x"] = event.x
            self.drag_data["y"] = event.y
            return

        if self.selection_rect:
            self.canvas.coords(self.selection_rect, self.selection_start[0], self.selection_start[1], event.x, event.y)
            return

        if self.active_tool is not None and self.temp_line_start:
            for pid in self.current_preview_ids: self.canvas.delete(pid)
            x1, y1 = self.temp_line_start
            x2, y2 = event.x, event.y
            if self.active_tool == "bend" and self.bend_control_point:
                cx, cy = self.bend_control_point
                self.current_preview_ids = self.draw_tactical_line_canvas("bend", x1, y1, x2, y2, preview=True, extra_data={"cx": cx, "cy": cy})
            else:
                adj_x1, adj_y1, adj_x2, adj_y2 = self.adjust_endpoints(x1, y1, x2, y2)
                self.current_preview_ids = self.draw_tactical_line_canvas(self.active_tool, adj_x1, adj_y1, adj_x2, adj_y2, preview=True)

    def on_canvas_release(self, event):
        if self.line_edit_mode:
            self.line_edit_mode = False
            self.line_edit_handle = None
            self.line_edit_start = None
            self.line_edit_initial_coords = []
            self._draw_selection_overlay()
            return

        if self.resize_mode:
            self.resize_mode = False
            self.resize_handle = None
            self.resize_start = None
            self.resize_anchor = None
            self.resize_initial_bounds = None
            self.resize_initial_coords = {}
            self._draw_selection_overlay()
            return

        if getattr(self, "dragging_token_mode", False):
            self.dragging_token_mode = False

            if self.grid_var.get():
                moved_sids = set(self.selected_tokens)
                for sid in list(self.selected_tokens):
                    for g in self.groups:
                        if sid in g:
                            moved_sids.update(g)

                for sid in moved_sids:
                    token = self.tokens.get(sid)
                    if token and not token.get("locked", False):
                        coords = self.canvas.coords(token["shape_id"])
                        if coords:
                            cx = (coords[0] + coords[2]) / 2
                            cy = (coords[1] + coords[3]) / 2
                            newx, newy = self.get_grid_snapped_point(cx, cy)
                            dx = newx - cx
                            dy = newy - cy
                            if abs(dx) > 1e-9 or abs(dy) > 1e-9:
                                for item in self._token_items(token):
                                    self.canvas.move(item, dx, dy)
                                if "text_ids" in token:
                                    for tid in token["text_ids"]:
                                        self.canvas.move(tid, dx, dy)
                                if getattr(self, "_drag_keep_attached", False):
                                    self.move_attached_line_ends(token, dx, dy)

            label_moves = {}
            for sid, old_coords in self.drag_start_positions.items():
                new_coords = self.canvas.coords(sid)
                if new_coords and old_coords != new_coords:
                    label_moves[self.tokens[sid]["label"]] = (new_coords[0] - old_coords[0], new_coords[1] - old_coords[1])
            if label_moves:
                # The tokens were already moved live, frame-by-frame, in
                # on_canvas_drag via direct canvas.move() calls. That's
                # not going through the command, so by the time we get
                # here the shapes are already sitting at their final
                # dropped position. If we call push_command with
                # execute=True, MoveTokensCommand.execute() would apply
                # that same (dx, dy) delta a *second* time on top of the
                # already-moved position -- doubling the distance moved
                # and making tokens (and any ghosts left behind) jump
                # past where you dropped them. We only want the command
                # recorded for undo/redo, not re-applied now.
                cmd = MoveTokensCommand(self, label_moves,
                                        ghosts=getattr(self, "_drag_ghosts", None),
                                        keep_attached=getattr(
                                            self, "_drag_keep_attached", False))
                self._drag_ghosts = []
                self.push_command(cmd, execute=False)
            self.drag_start_positions.clear()
            return

        if getattr(self, "dragging_drawn_mode", False):
            self.dragging_drawn_mode = False
            # Dropping a ball near a player clicks it onto the middle of the nearest
            # edge, the same rule that governs where a freshly stamped one lands.
            for cid in list(self.drag_start_positions):
                if self._snap_ball_item(cid):
                    break
            id_moves = {}
            for cid, old_coords in self.drag_start_positions.items():
                new_coords = self.canvas.coords(cid)
                if new_coords and old_coords != new_coords:
                    dx = new_coords[0] - old_coords[0]
                    dy = new_coords[1] - old_coords[1]
                    id_moves[cid] = (dx, dy)
            if id_moves:
                # Same reasoning as the token drag above: the drawn item
                # was already moved live during the drag, so executing
                # the command again here would double-apply the offset.
                cmd = MoveDrawnCommand(self, id_moves)
                self.push_command(cmd, execute=False)
            self.drag_start_positions.clear()
            return

        if self.selection_rect:
            x1, y1 = self.selection_start
            xmin, xmax = min(x1, event.x), max(x1, event.x)
            ymin, ymax = min(y1, event.y), max(y1, event.y)
            self.canvas.delete(self.selection_rect)
            self.selection_rect = None
            self.selected_tokens.clear()
            self.selected_drawn.clear()
            if (xmax - xmin) > 3 or (ymax - ymin) > 3:
                # A real lasso, not a click: whatever fixture the drag started on was
                # only ever a click's worth of selection.
                self.selected_pitch_parts.clear()
            for token in self.tokens.values():
                sid = token["shape_id"]
                coords = self.canvas.coords(sid)
                if coords:
                    cx, cy = (coords[0] + coords[2]) / 2, (coords[1] + coords[3]) / 2
                    if xmin <= cx <= xmax and ymin <= cy <= ymax:
                        self.selected_tokens.append(sid)
            for cid, meta in list(self.drawn_items.items()):
                coords = self.canvas.coords(cid)
                if not coords:
                    continue
                cx = sum(coords[0::2]) / (len(coords[0::2]) or 1)
                cy = sum(coords[1::2]) / (len(coords[1::2]) or 1)
                if xmin <= cx <= xmax and ymin <= cy <= ymax:
                    self.selected_drawn |= self._drawn_siblings(cid)
            self.highlight_selected()
            return

        if self.active_tool is not None and self.temp_line_start:
            x1, y1 = self.temp_line_start
            x2, y2 = event.x, event.y
            extra = {}
            if self.active_tool == "bend":
                if not self.bend_control_point:
                    return 
                extra["cx"], extra["cy"] = self.bend_control_point
                self.bend_control_point = None

            for pid in self.current_preview_ids: self.canvas.delete(pid)
            self.current_preview_ids = []

            adj_x1, adj_y1, adj_x2, adj_y2 = self.adjust_endpoints(x1, y1, x2, y2)
            cmd = DrawLineCommand(self, self.active_tool, adj_x1, adj_y1, adj_x2, adj_y2, extra_data=extra)
            self.push_command(cmd, execute=True)
            
            created_line_ids = cmd.line_ids
            start_token = self.get_token_at_point(adj_x1, adj_y1)
            end_token = self.get_token_at_point(adj_x2, adj_y2)
            if start_token: self.tokens[start_token]["attached_lines_start"].extend(created_line_ids)
            if end_token: self.tokens[end_token]["attached_lines_end"].extend(created_line_ids)
            self.temp_line_start = None

    def clear_selection(self):
        self.selected_tokens.clear()
        self.selected_drawn.clear()
        self.selected_pitch_parts.clear()
        for token in self.tokens.values():
            try:
                # width matches _create_token so deselecting does not thin the
                # white outline; X/plus tokens are lines and have no outline option,
                # which the surrounding try/except absorbs.
                # The dark edge, not the white one: the white ring is a separate item
                # underneath, so repainting this outline white would bury the edge.
                default_outline = self.TOKEN_EDGE
                self.canvas.itemconfig(token["shape_id"], outline="#6c757d" if token.get("locked", False) else default_outline, width=2)
            except Exception:
                pass
        for cid, meta in list(self.drawn_items.items()):
            if meta.get("decor") or meta.get("type") == "image":
                # The ball's holes carry the rink's colour rather than the sign's, and
                # a picture has no fill at all: repainting either on deselect turned
                # every ball solid.
                continue
            # Restore only the options that actually carry this item's colour. A
            # blanket fill= floods outline-only shapes (Square/Triangle signs) solid
            # the moment they are deselected.
            for option in meta.get("color_options") or ("fill",):
                try:
                    self.canvas.itemconfig(cid, **{option: meta.get("color", self.line_color)})
                except Exception:
                    pass
        self._clear_selection_overlay()

    def _clear_selection_overlay(self):
        for item_id in self.selection_overlay_ids + self.selection_overlay_handles:
            try:
                self.canvas.delete(item_id)
            except Exception:
                pass
        self.selection_overlay_ids = []
        self.selection_overlay_handles = []
        self.selection_overlay_handle_types = []

    def _get_selection_bounds(self):
        points = []
        for sid in self.selected_tokens:
            token = self.tokens.get(sid)
            if token and "shape_id" in token:
                coords = self.canvas.coords(token["shape_id"])
                if coords:
                    points.extend([(coords[0], coords[1]), (coords[2], coords[3])])
        for cid in self.selected_drawn:
            coords = self.canvas.coords(cid)
            if coords:
                xs = coords[0::2]
                ys = coords[1::2]
                points.extend([(min(xs), min(ys)), (max(xs), max(ys))])
        if not points:
            return None
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        return (min(xs), min(ys), max(xs), max(ys))

    def _draw_selection_overlay(self):
        self._clear_selection_overlay()
        bounds = self._get_selection_bounds()
        if not bounds:
            return
        x1, y1, x2, y2 = bounds
        rect_id = self.canvas.create_rectangle(x1, y1, x2, y2, outline="#4dabf7", width=2, dash=(4, 3), tags=("selection_overlay",))
        self.selection_overlay_ids.append(rect_id)
        if self._is_line_selection():
            line_id = self._get_selected_line_item()
            if line_id:
                coords = self.canvas.coords(line_id)
                if coords and len(coords) >= 4:
                    start = (coords[0], coords[1])
                    end = (coords[-2], coords[-1])
                    mid = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
                    for handle_name, (hx, hy) in [("line_start", start), ("line_mid", mid), ("line_end", end)]:
                        handle_id = self.canvas.create_oval(hx - 4, hy - 4, hx + 4, hy + 4, fill="#4dabf7", outline="#ffffff", width=1, tags=("selection_overlay", "resize_handle"))
                        self.selection_overlay_handles.append(handle_id)
                        self.selection_overlay_handle_types.append(handle_name)
                    # A curve carries its shape in a middle control point. Give it a
                    # handle of its own -- orange, to separate it from the blue ones
                    # that move the line -- so a bend can be reshaped after drawing.
                    if len(coords) >= 6:
                        bx, by = coords[2], coords[3]
                        handle_id = self.canvas.create_oval(
                            bx - 5, by - 5, bx + 5, by + 5, fill=self.BEND_HANDLE_COLOR,
                            outline="#ffffff", width=1,
                            tags=("selection_overlay", "resize_handle"))
                        self.selection_overlay_handles.append(handle_id)
                        self.selection_overlay_handle_types.append("line_bend")
                else:
                    handle_positions = [(x1, y1), (x2, y1), (x1, y2), (x2, y2)]
                    for x, y in handle_positions:
                        handle_id = self.canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill="#4dabf7", outline="#ffffff", width=1, tags=("selection_overlay", "resize_handle"))
                        self.selection_overlay_handles.append(handle_id)
                        self.selection_overlay_handle_types.append("corner")
            else:
                handle_positions = [(x1, y1), (x2, y1), (x1, y2), (x2, y2)]
                for x, y in handle_positions:
                    handle_id = self.canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill="#4dabf7", outline="#ffffff", width=1, tags=("selection_overlay", "resize_handle"))
                    self.selection_overlay_handles.append(handle_id)
                    self.selection_overlay_handle_types.append("corner")
        else:
            handle_positions = [(x1, y1), (x2, y1), (x1, y2), (x2, y2)]
            for x, y in handle_positions:
                handle_id = self.canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill="#4dabf7", outline="#ffffff", width=1, tags=("selection_overlay", "resize_handle"))
                self.selection_overlay_handles.append(handle_id)
                self.selection_overlay_handle_types.append("corner")
        self.canvas.tag_raise(rect_id)
        for handle_id in self.selection_overlay_handles:
            self.canvas.tag_raise(handle_id)

    def _get_handle_type(self, handle_id):
        for idx, handle in enumerate(self.selection_overlay_handles):
            if handle == handle_id:
                return self.selection_overlay_handle_types[idx]
        return None

    def _get_resize_anchor(self, handle_id):
        for idx, handle in enumerate(self.selection_overlay_handles):
            if handle == handle_id:
                handle_type = self.selection_overlay_handle_types[idx]
                if handle_type in {"top-left", "top-right", "bottom-left", "bottom-right"}:
                    return handle_type
                return ["top-left", "top-right", "bottom-left", "bottom-right"][idx]
        return None

    def _compute_resized_box(self, original_box, current_point, anchor, keep_ratio=False):
        x1, y1, x2, y2 = original_box
        orig_w = max(10.0, x2 - x1)
        orig_h = max(10.0, y2 - y1)
        if anchor == "bottom-right":
            new_x2, new_y2 = current_point
            new_x1, new_y1 = x1, y1
        elif anchor == "bottom-left":
            new_x1, new_y2 = current_point
            new_x2, new_y1 = x2, y1
        elif anchor == "top-right":
            new_x2, new_y1 = current_point
            new_x1, new_y2 = x1, y2
        else:
            new_x1, new_y1 = current_point
            new_x2, new_y2 = x2, y2

        if keep_ratio:
            width = max(10.0, abs(new_x2 - new_x1))
            height = max(10.0, abs(new_y2 - new_y1))
            if anchor in {"bottom-right", "top-left"}:
                if abs(width / orig_w) >= abs(height / orig_h):
                    target_height = width * (orig_h / orig_w)
                    if anchor == "bottom-right":
                        new_y2 = y1 + target_height
                    else:
                        new_y1 = y2 - target_height
                else:
                    target_width = height * (orig_w / orig_h)
                    if anchor == "bottom-right":
                        new_x2 = x1 + target_width
                    else:
                        new_x1 = x2 - target_width
            elif anchor in {"bottom-left", "top-right"}:
                if abs(width / orig_w) >= abs(height / orig_h):
                    target_height = width * (orig_h / orig_w)
                    if anchor == "bottom-left":
                        new_y2 = y1 + target_height
                    else:
                        new_y1 = y2 - target_height
                else:
                    target_width = height * (orig_w / orig_h)
                    if anchor == "bottom-left":
                        new_x1 = x2 - target_width
                    else:
                        new_x2 = x1 + target_width

        if anchor == "bottom-right":
            return (x1, y1, max(x1 + 10, new_x2), max(y1 + 10, new_y2))
        if anchor == "bottom-left":
            return (min(x2 - 10, new_x1), y1, x2, max(y1 + 10, new_y2))
        if anchor == "top-right":
            return (x1, min(y2 - 10, new_y1), max(x1 + 10, new_x2), y2)
        return (min(x2 - 10, new_x1), min(y2 - 10, new_y1), x2, y2)

    def _clamp_resized_box(self, bounds):
        x1, y1, x2, y2 = bounds
        max_x = getattr(self, "width", 0)
        max_y = getattr(self, "height", 0)
        padding = 12
        x1 = max(padding, min(x1, max_x - padding))
        y1 = max(padding, min(y1, max_y - padding))
        x2 = max(padding, min(x2, max_x - padding))
        y2 = max(padding, min(y2, max_y - padding))
        if x2 - x1 < 10:
            x2 = x1 + 10
        if y2 - y1 < 10:
            y2 = y1 + 10
        return (x1, y1, x2, y2)

    def _scale_canvas_coords(self, coords, cx, cy, scale_x, scale_y):
        new_coords = []
        for idx, value in enumerate(coords):
            if idx % 2 == 0:
                new_value = cx + (value - cx) * scale_x
            else:
                new_value = cy + (value - cy) * scale_y
            new_coords.append(max(12, min(self.width - 12, new_value)))
        return new_coords

    RESIZE_MIN_SCALE = 0.05
    RESIZE_MAX_SCALE = 20.0

    def _get_resize_scale(self, current_size, original_size):
        """How much the box grew, bounded only by what stays sane on a canvas.

        No damping: a handle that does a tenth of what the hand does is a handle that
        appears not to work."""
        if original_size <= 0:
            return 1.0
        ratio = current_size / original_size
        return max(self.RESIZE_MIN_SCALE, min(self.RESIZE_MAX_SCALE, ratio))

    def _apply_resized_selection(self, new_bounds):
        if not self.resize_initial_bounds:
            return
        x1, y1, x2, y2 = new_bounds
        target_w = max(10.0, x2 - x1)
        target_h = max(10.0, y2 - y1)
        orig_x1, orig_y1, orig_x2, orig_y2 = self.resize_initial_bounds
        orig_w = max(10.0, orig_x2 - orig_x1)
        orig_h = max(10.0, orig_y2 - orig_y1)
        scale_x = self._get_resize_scale(target_w, orig_w)
        scale_y = self._get_resize_scale(target_h, orig_h)
        cx = (orig_x1 + orig_x2) / 2.0
        cy = (orig_y1 + orig_y2) / 2.0

        for cid in list(self.selected_drawn):
            coords = self.canvas.coords(cid)
            if not coords:
                continue
            new_coords = self._scale_canvas_coords(coords, cx, cy, scale_x, scale_y)
            try:
                self.canvas.coords(cid, *new_coords)
            except Exception:
                pass
            # An image is a single anchor point, so scaling its coordinates only moves
            # it. Its bitmap has to be redrawn at the new size to actually resize.
            self._scale_board_image(cid, scale_x, scale_y)
            # Text is a point too: the type size is what makes it bigger or smaller.
            self._scale_board_text(cid, max(scale_x, scale_y))

        for sid in list(self.selected_tokens):
            token = self.tokens.get(sid)
            if not token or not token.get("shape_id") or token.get("locked", False):
                continue
            old_shape_id = token.get("shape_id")
            coords = self.canvas.coords(old_shape_id)
            if not coords:
                continue
            old_cx = (coords[0] + coords[2]) / 2.0
            old_cy = (coords[1] + coords[3]) / 2.0
            new_cx = cx + (old_cx - cx) * scale_x
            new_cy = cy + (old_cy - cy) * scale_y
            dx = new_cx - old_cx
            dy = new_cy - old_cy
            if abs(dx) < 1e-9 and abs(dy) < 1e-9:
                continue
            new_cx = max(12, min(self.width - 12, old_cx + dx))
            new_cy = max(12, min(self.height - 12, old_cy + dy))
            for item in self._token_items(token):
                self.canvas.move(item, new_cx - old_cx, new_cy - old_cy)
            for tid in token.get("text_ids", []):
                self.canvas.move(tid, new_cx - old_cx, new_cy - old_cy)
            self.move_attached_line_ends(token, dx, dy)
            token["starting_pos"] = (new_cx, new_cy)

    def _wait_modal(self, window):
        """Block until the dialog closes, without minding if it already has.

        Anything can close a dialog while the grab is being taken -- a window manager,
        a timer, the user -- and waiting on a window that is already gone is not an
        error worth taking the caller down for."""
        try:
            self.root.wait_window(window)
        except Exception:
            pass

    @staticmethod
    def _make_modal(window):
        """Take the input grab, once the window is actually on screen.

        grab_set() on a window the window manager has not mapped yet fails with
        "grab failed: window not viewable" and takes the dialog down with it, which is
        a race rather than a mistake: whether the map has happened by the time the next
        line runs depends on the machine. Waiting for the map first makes it a
        certainty, and a dialog that cannot be grabbed at all is still a usable dialog,
        so a failure here is not worth raising."""
        # update_idletasks, never wait_visibility: waiting blocks until the window
        # manager maps the window, and if it never does -- no window manager, a
        # window closed from elsewhere first -- the whole application hangs there.
        try:
            window.update_idletasks()
        except Exception:
            pass
        for _ in range(3):
            try:
                self._make_modal(window)
                return True
            except Exception:
                try:
                    window.update()
                except Exception:
                    break
        return False

    def _drawn_siblings(self, cid):
        """Every canvas item that makes up the same drawing as this one.

        A ball is a body and three holes, an X is two strokes, a shot is two shafts
        and a head. Clicking one piece has to take the whole mark, or dragging it
        leaves the rest of the drawing behind."""
        meta = self.drawn_items.get(cid)
        if not meta:
            return {cid}
        group = meta.get("group")
        data = meta.get("data")
        found = {cid}
        for other, other_meta in self.drawn_items.items():
            if group and other_meta.get("group") == group:
                found.add(other)
            elif data is not None and other_meta.get("data") is data:
                found.add(other)
        return found

    def _get_selected_line_item(self):
        if len(self.selected_drawn) == 1:
            cid = next(iter(self.selected_drawn))
            meta = self.drawn_items.get(cid, {})
            if meta.get("type") == "tactic_line":
                return cid
        return None

    def _get_selected_line_coords(self):
        line_id = self._get_selected_line_item()
        if not line_id:
            return []
        return self.canvas.coords(line_id)[:]

    def _move_paired_bend(self, line_id, dx, dy):
        """With Arches on, a bend is drawn as two offset curves. They were created from
        one drawing, so they share their metadata -- which is how the twin is found and
        kept in step when the control point is dragged."""
        meta = self.drawn_items.get(line_id)
        if not meta or meta.get("tool") != "bend":
            return
        data = meta.get("data")
        for other, other_meta in self.drawn_items.items():
            if other == line_id or other_meta.get("tool") != "bend":
                continue
            if data is not None and other_meta.get("data") is not data:
                continue
            coords = self.canvas.coords(other)
            if len(coords) >= 6:
                coords[2] += dx
                coords[3] += dy
                self.canvas.coords(other, *coords)

    def _is_line_selection(self):
        return self._get_selected_line_item() is not None

    def _resize_selected_players(self, event=None):
        """Redraw players at the size in the roster box.

        With a selection it resizes that; with nothing selected it resizes every
        player, so the box is never a control that appears to do nothing."""
        try:
            new_size = max(6, int(self.player_size_var.get()))
        except Exception:
            return

        if self.selected_tokens:
            targets, keep_selection = list(self.selected_tokens), True
        else:
            targets, keep_selection = [t.get("shape_id") for t in self._all_tokens()], False

        for sid in targets:
            token = self.tokens.get(sid)
            if not token or token.get("is_ghost") or token.get("locked", False):
                continue
            old_shape_id = token.get("shape_id")
            if not old_shape_id:
                continue
            # bbox, not coords: a square player is a polygon and a cross is a pair of
            # lines, so coords[0..3] is not the bounding box -- reading it as one put
            # the resized player half a token above where it had been standing.
            box = self.canvas.bbox(old_shape_id)
            if not box:
                continue
            cx = (box[0] + box[2]) / 2.0
            cy = (box[1] + box[3]) / 2.0
            # _delete_token, not a delete of the main shape alone: that left the white
            # halo, the dark edge and an X's extra strokes behind at the old size,
            # sitting under the new token.
            self._delete_token(token)
            shape_id = self._create_token(cx, cy, token["label"],
                                          shape=token.get("shape", "circle"),
                                          color=token.get("color", "black"),
                                          outline=token.get("outline", self.TOKEN_OUTLINE),
                                          stipple=token.get("stipple", ""),
                                          size=new_size, team=token.get("team"))
            new_token = self.tokens.get(shape_id)
            if new_token:
                new_token["size"] = new_size
                new_token["font_size"] = max(6, int(new_size * 0.57))
                new_token["starting_pos"] = (cx, cy)
                new_token["ghost_count"] = token.get("ghost_count", 0)
                new_token["angle"] = token.get("angle", 0)
                new_token["attached_lines_start"] = list(token.get("attached_lines_start", []))
                new_token["attached_lines_end"] = list(token.get("attached_lines_end", []))
                new_token["locked"] = token.get("locked", False)
                new_token["is_ghost"] = token.get("is_ghost", False)
                new_token["ghost_of"] = token.get("ghost_of")
                # The tactical role is what the token shows; without this a resize
                # reverted every label to A1..A5 / D1..D5.
                self._set_token_position(new_token, token.get("position"))
                if token.get("angle"):
                    self._rotate_token(new_token, token["angle"])
            if keep_selection:
                for idx, selected_id in enumerate(self.selected_tokens):
                    if selected_id == old_shape_id:
                        self.selected_tokens[idx] = shape_id
                        break
        self.highlight_selected()

    def _all_tokens(self):
        seen, out = set(), []
        for token in self.tokens.values():
            sid = token.get("shape_id")
            if sid is None or sid in seen:
                continue
            seen.add(sid)
            out.append(token)
        return out

    def highlight_selected(self):
        for token in self.tokens.values():
            sid = token["shape_id"]
            try:
                default_outline = self.TOKEN_EDGE
                self.canvas.itemconfig(sid, outline="#6c757d" if token.get("locked", False) else default_outline, width=2)
            except Exception:
                pass
        for sid in self.selected_tokens:
            token = self.tokens.get(sid)
            if token:
                try:
                    self.canvas.itemconfig(sid, outline="#228be6", width=3.5)
                except Exception:
                    pass
        for cid in self.drawn_items:
            meta = self.drawn_items.get(cid, {})
            if meta.get("decor"):
                # The holes in the ball carry the rink's colour, not the sign's.
                # Repainting them here turned every ball solid the moment anything
                # was selected.
                continue
            if meta.get("type") == "image":
                continue                      # a picture has no fill to recolour
            # Only the options that actually carry this item's colour. A blanket fill=
            # floods every outline-only shape -- ovals, circles, boxes, rectangles,
            # square and triangle signs -- solid the moment anything is selected or
            # deselected. clear_selection already knew this; this path did not.
            colour = ("#228be6" if cid in self.selected_drawn
                      else meta.get("color", self.line_color))
            for option in meta.get("color_options") or ("fill",):
                try:
                    self.canvas.itemconfig(cid, **{option: colour})
                except Exception:
                    pass
        self._draw_selection_overlay()
        self._highlight_pitch_parts()

    # How a curve is drawn for each line type: its dashes, how much heavier than a
    # plain line it is, and whether it carries an arrowhead. Solid lines and the run
    # keep the plain weight; a shot is the one that is meant to look heavy.
    BEND_STYLES = {
        "solid":   (None,   0, True),
        "dashed":  ((6, 4), 0, True),
        "dotted":  ((2, 4), 0, True),
        "pass":    ((4, 4), 0, True),
        "shot":    (None,   1, True),
        "dribble": ((2, 3), 0, True),
        "run":     ((8, 3), 0, True),
    }

    def _bend_style(self, ltype, base_width):
        """The dash pattern, width and head a bend of this line type is drawn with."""
        dash, extra, arrow = self.BEND_STYLES.get(str(ltype).lower(),
                                                  self.BEND_STYLES["solid"])
        return dash, max(1, base_width + extra), arrow

    def draw_tactical_line_canvas(self, tool, x1, y1, x2, y2, preview=False, extra_data=None):
        created_ids = []
        big_arrow = (14, 18, 7)
        color = self.line_color
        base_width = int(self.line_thick_var.get())
        ltype = self.line_type_var.get().lower()
        
        effective_tool = tool
        if tool == "line":
            if ltype == "pass": effective_tool = "pass"
            elif ltype == "shot": effective_tool = "shot"
            elif ltype == "dribble": effective_tool = "dribble"
            elif ltype == "run": effective_tool = "run"

        if effective_tool == "run":
            width = max(2, base_width + 1)
        else:
            width = base_width + 2

        dash_tuple = None
        if ltype == "dashed" or effective_tool == "pass":
            dash_tuple = (6, 4) if effective_tool != "pass" else (4, 4)
        elif ltype == "dotted":
            dash_tuple = (2, 4)

        if effective_tool == "line":
            lid = self.canvas.create_line(x1, y1, x2, y2, fill=color, width=base_width, dash=dash_tuple, tags=("tactic_line",))
            created_ids.append(lid)
        elif effective_tool == "box":
            side_len = max(abs(x2 - x1), abs(y2 - y1))
            nx2 = x1 + side_len if x2 >= x1 else x1 - side_len
            ny2 = y1 + side_len if y2 >= y1 else y1 - side_len
            lid = self.canvas.create_rectangle(x1, y1, nx2, ny2, outline=color, width=base_width, dash=dash_tuple, tags=("tactic_line",))
            created_ids.append(lid)
        elif effective_tool == "rectangle":
            lid = self.canvas.create_rectangle(x1, y1, x2, y2, outline=color, width=base_width, dash=dash_tuple, tags=("tactic_line",))
            created_ids.append(lid)
        elif effective_tool == "circle":
            r = math.hypot(x2 - x1, y2 - y1)
            lid = self.canvas.create_oval(x1 - r, y1 - r, x1 + r, y1 + r, outline=color, width=base_width, dash=dash_tuple, tags=("tactic_line",))
            created_ids.append(lid)
        elif effective_tool == "oval":
            lid = self.canvas.create_oval(x1, y1, x2, y2, outline=color, width=base_width, dash=dash_tuple, tags=("tactic_line",))
            created_ids.append(lid)
        elif effective_tool == "pass":
            lid = self.canvas.create_line(x1, y1, x2, y2, fill=color, width=width, dash=dash_tuple, arrow=tk.LAST, arrowshape=big_arrow, tags=("tactic_line",))
            created_ids.append(lid)
        elif effective_tool == "bend":
            cx = extra_data.get("cx", (x1+x2)/2) if extra_data else (x1+x2)/2
            cy = extra_data.get("cy", (y1+y2)/2) if extra_data else (y1+y2)/2
            # A bend is a shape, not a kind of line: whatever type is armed -- a pass,
            # a shot, a dribble, a run, or a plain dashed or dotted line -- can be
            # drawn as a curve, and comes out with that type's dashes, weight and head.
            bend_dash, bend_width, bend_arrow = self._bend_style(ltype, base_width)
            # base_width as the floor, not the heavier arrow width: a curve carries
            # more ink than a straight line of the same thickness and came out looking
            # fatter than every other arrow on the board.
            lid = self.canvas.create_line(x1, y1, cx, cy, x2, y2, smooth=True,
                                          fill=color, width=bend_width, dash=bend_dash,
                                          arrow=tk.LAST if bend_arrow else None,
                                          arrowshape=big_arrow, tags=("tactic_line",))
            created_ids.append(lid)
        elif effective_tool == "shot":
            dx, dy = x2 - x1, y2 - y1
            dist = math.hypot(dx, dy)
            if dist > 0:
                unit_x, unit_y = dx / dist, dy / dist
                nx, ny = -unit_y * 3, unit_x * 3
                l1 = self.canvas.create_line(x1 + nx, y1 + ny, x2 - 16*unit_x + nx, y2 - 16*unit_y + ny, fill=color, width=base_width, dash=dash_tuple, tags=("tactic_line",))
                l2 = self.canvas.create_line(x1 - nx, y1 - ny, x2 - 16*unit_x - nx, y2 - 16*unit_y - ny, fill=color, width=base_width, dash=dash_tuple, tags=("tactic_line",))
                arrow_id = self.canvas.create_polygon(x2, y2, x2 - 14*unit_x - 7*(-unit_y), y2 - 14*unit_y - 7*unit_x, x2 - 14*unit_x + 7*(-unit_y), y2 - 14*unit_y + 7*unit_x, fill=color, outline=color, tags=("tactic_line",))
                created_ids.extend([l1, l2, arrow_id])
            else:
                lid = self.canvas.create_line(x1, y1, x2, y2, fill=color, width=width, dash=dash_tuple, arrow=tk.LAST, arrowshape=big_arrow, tags=("tactic_line",))
                created_ids.append(lid)
        elif effective_tool == "dribble":
            dx, dy = x2 - x1, y2 - y1
            dist = math.hypot(dx, dy)
            if dist > 0:
                unit_x, unit_y = dx / dist, dy / dist
                nx, ny = -unit_y, unit_x
                points = []
                for s in range(0, int(dist) + 1, 2):
                    bx, by = x1 + (s/dist)*dx, y1 + (s/dist)*dy
                    offset = 12 * math.sin((s / 55) * 2 * math.pi)
                    points.extend([bx + nx*offset, by + ny*offset])
                if len(points) >= 4:
                    lid = self.canvas.create_line(*points, fill=color, width=width, dash=dash_tuple, smooth=True, tags=("tactic_line",))
                    created_ids.append(lid)
            else:
                lid = self.canvas.create_line(x1, y1, x2, y2, fill=color, width=width, dash=dash_tuple, arrow=tk.LAST, arrowshape=big_arrow, tags=("tactic_line",))
                created_ids.append(lid)
        elif effective_tool == "run":
            run_width = max(2, width - 1)
            lid = self.canvas.create_line(x1, y1, x2, y2, fill=color, width=run_width, dash=dash_tuple, arrow=tk.LAST, arrowshape=big_arrow, tags=("tactic_line",))
            created_ids.append(lid)

        if created_ids and not preview:
            # One tag for every piece of this arrow. A shot is two shafts and a head,
            # and they have to be recognised as one drawing -- for selecting, for
            # resizing, and for working out which line the arrow actually runs along.
            self._line_group_seq = getattr(self, "_line_group_seq", 0) + 1
            group_tag = f"linegrp{self._line_group_seq}"
            for cid in created_ids:
                self.canvas.addtag_withtag(group_tag, cid)
                self._register_drawn_item(cid, {"type": "tactic_line", "tool": tool,
                                                "group": group_tag, "color": color})
        return created_ids

    def on_window_resize(self, event):
        """Window-level changes only decide where the toolbar sits and how it wraps.
        The canvas size is NOT computed here: on_canvas_resize handles that."""
        if event.widget != self.root:
            return
        self._reflow_menu()

    def on_canvas_resize(self, event):
        """Take the size the canvas was actually given rather than deriving it from
        the window minus a guess at the toolbar's footprint. That guess was what put
        the rink off-screen: once the bar grew to two rows the estimate overshot, and
        the pitch was scaled for a canvas taller than the one on screen."""
        width, height = event.width, event.height
        if width <= 1 or height <= 1:
            return
        if (width, height) == (self.width, self.height):
            return
        self.width, self.height = width, height
        # redraw_canvas() re-projects everything through rink metres, so players keep
        # their position on the rink as the window changes size.
        self.redraw_canvas()

    ROTATE_STEP = 45

    def rotate_selected(self):
        """Rotate the selection. This used to only store an angle on the token and pop
        a dialog per selection -- nothing ever redrew, so nothing turned. Signs and
        other drawn items are included too: Rotate Sel did nothing at all for them,
        which is why a stamped goal could not be turned."""
        labels, seen = [], set()
        for sid in list(self.selected_tokens):
            token = self.tokens.get(sid)
            if not token or token.get("locked", False):
                continue
            label = token.get("label")
            if label and label not in seen:
                seen.add(label)
                labels.append(label)
        if labels:
            self.push_command(RotateTokensCommand(self, labels, self.ROTATE_STEP))

        drawn = self._selected_drawn_ids()
        if drawn:
            self.push_command(RotateDrawnCommand(self, drawn, self.ROTATE_STEP))

    def _selected_drawn_ids(self):
        """Every canvas id in the drawn selection, expanded through sign groups so a
        multi-part sign (a ball, an X, a plus) turns and moves as one piece."""
        ids, seen = [], set()
        for cid in self.selected_drawn:
            group = (self.drawn_items.get(cid) or {}).get("group")
            members = self.canvas.find_withtag(group) if group else (cid,)
            for member in members or (cid,):
                if member not in seen:
                    seen.add(member)
                    ids.append(member)
        return ids

    # ----------------------
    # Tactics / formations
    # ----------------------
    # Each slot is (position label, across, depth) with across 0=left..1=right and
    # depth 0=rearmost..1=most advanced, both measured in the team's own attacking
    # direction. Keeping them unitless lets one table serve either team, either rink
    # orientation and the half rink.
    FORMATIONS = {
        "Dice":     [("LD", 0.25, 0.00), ("RD", 0.75, 0.00), ("C", 0.50, 0.50),
                     ("LA", 0.25, 1.00), ("RA", 0.75, 1.00)],
        # The defenders sit wide, near the boards, rather than tucked in beside each
        # other -- that is what makes the house shape a house.
        "House":    [("LD", 0.12, 0.00), ("RD", 0.88, 0.00), ("LW", 0.15, 0.55),
                     ("RW", 0.85, 0.55), ("T", 0.50, 1.00)],
        "Point":    [("P", 0.50, 0.00), ("LW", 0.15, 0.50), ("RW", 0.85, 0.50),
                     ("LA", 0.30, 1.00), ("RA", 0.70, 1.00)],
        "Umbrella": [("P", 0.50, 0.00), ("LW", 0.20, 0.45), ("RW", 0.80, 0.45),
                     ("C", 0.50, 0.70), ("T", 0.50, 1.00)],
        # Four-player shapes. Applying one drops the team to four players; picking a
        # five-player formation again brings the fifth back.
        "Square":   [("LD", 0.25, 0.00), ("RD", 0.75, 0.00),
                     ("LA", 0.25, 1.00), ("RA", 0.75, 1.00)],
        "Diamond":  [("P", 0.50, 0.00), ("LW", 0.15, 0.50), ("RW", 0.85, 0.50),
                     ("T", 0.50, 1.00)],
    }

    def _token_team(self, token):
        """Tokens loaded from an older macro have no team field, so fall back to the
        A/D label prefix the roster has always used."""
        team = token.get("team")
        if team:
            return team
        label = str(token.get("label", ""))
        if label.startswith("A"):
            return "att"
        if label.startswith("D"):
            return "def"
        return None

    def _team_tokens(self, team):
        seen, out = set(), []
        for token in self.tokens.values():
            sid = token.get("shape_id")
            if sid is None or sid in seen:
                continue
            seen.add(sid)
            if self._token_team(token) == team:
                out.append(token)
        out.sort(key=lambda t: str(t.get("label", "")))
        return out

    def _token_items(self, token):
        """Every canvas item the token is drawn from. X and plus players are several
        strokes, and squares/triangles carry a white outline, so anything that moves,
        rotates or deletes a token has to act on all of them."""
        items = token.get("shape_ids")
        if not items:
            items = [token.get("shape_id")]
        return [item for item in items if item]

    def _rotate_token(self, token, degrees):
        """Turn a token about its own centre. Ovals are skipped -- a circle looks
        identical rotated, and rewriting an oval's two bbox corners would distort it
        rather than turn it.

        The pivot is the centroid of the shape's own points, NOT its bounding-box
        centre. A triangle's bbox centre moves when the triangle turns, so rotating
        back about it landed the shape somewhere else and undo did not restore."""
        shapes = []
        for item in self._token_items(token):
            try:
                if self.canvas.type(item) in ("polygon", "line"):
                    shapes.append((item, self.canvas.coords(item)))
            except Exception:
                continue
        points = [(c[i], c[i + 1]) for _, c in shapes for i in range(0, len(c), 2)]
        if not points:
            token["angle"] = (token.get("angle", 0) + degrees) % 360
            return

        cx = sum(px for px, _ in points) / len(points)
        cy = sum(py for _, py in points) / len(points)
        angle = math.radians(degrees)
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        for item, coords in shapes:
            turned = []
            for index in range(0, len(coords), 2):
                dx, dy = coords[index] - cx, coords[index + 1] - cy
                turned.extend([cx + dx * cos_a - dy * sin_a,
                               cy + dx * sin_a + dy * cos_a])
            try:
                self.canvas.coords(item, *turned)
            except Exception:
                pass
        token["angle"] = (token.get("angle", 0) + degrees) % 360

    def _delete_token(self, token):
        for item in self._token_items(token) + list(token.get("text_ids", [])):
            try:
                self.canvas.delete(item)
            except Exception:
                pass
        for key in [k for k, v in list(self.tokens.items()) if v is token]:
            self.tokens.pop(key, None)
        # Rebuild as a list: the rest of the app appends to and indexes into this,
        # and turning it into a set here broke both.
        self.selected_tokens = [s for s in self.selected_tokens if s in self.tokens]
        self.groups = [g for g in self.groups if not g.intersection({token.get("shape_id")})]

    def _token_centre_px(self, token):
        """A player's centre in pixels, as floats, taken from the shape's own
        coordinates rather than from its bounding box.

        canvas.bbox() reports whole pixels. Positioning by it therefore leaves up to
        half a pixel of error, and which half depends on where the token happened to
        be standing beforehand -- so loading the same file twice produced boards that
        were not quite identical, and two exports of the same play differed in every
        single frame."""
        sid = token.get("shape_id") if isinstance(token, dict) else None
        if sid is None:
            return None
        try:
            kind = self.canvas.type(sid)
            coords = self.canvas.coords(sid)
        except Exception:
            return None
        if not coords:
            box = self.canvas.bbox(sid)
            return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0) if box else None
        xs, ys = coords[0::2], coords[1::2]
        if kind in ("oval", "rectangle", "arc") and len(xs) >= 2:
            return ((xs[0] + xs[1]) / 2.0, (ys[0] + ys[1]) / 2.0)
        return (sum(xs) / len(xs), sum(ys) / len(ys))

    def _token_spec(self, token):
        """Everything about a player that is needed to build them again, with the
        position in rink metres so it survives a resize or a change of field."""
        centre = self._token_centre_px(token)
        if not centre:
            return None
        state = self._pitch_state()
        mx, my = self._state_px_to_m(centre[0], centre[1], state)
        return {"label": token.get("label"), "team": self._token_team(token),
                "shape": token.get("shape", "circle"),
                "color": token.get("color", "black"),
                "size": token.get("size", 14),
                "position": token.get("position"),
                "locked": bool(token.get("locked", False)),
                "is_ghost": bool(token.get("is_ghost")),
                "mx": mx, "my": my}

    def _restore_token(self, spec):
        """Put a deleted player back where they stood."""
        if not spec or self._get_sid_by_label(spec.get("label")):
            return None
        px, py = self._rink_to_px(spec.get("mx", 0.0), spec.get("my", 0.0))
        sid = self._create_token(px, py, spec["label"],
                                 shape=spec.get("shape", "circle"),
                                 color=spec.get("color", "black"),
                                 size=spec.get("size"),
                                 team=spec.get("team"))
        token = self.tokens.get(sid)
        if token:
            token["locked"] = spec.get("locked", False)
            if spec.get("is_ghost"):
                token["is_ghost"] = True
            self._set_token_position(token, spec.get("position"))
        return sid

    def _refresh_roster_counts(self):
        """Make the roster boxes say how many players are actually on the board.

        Without this a deleted player left the box reading five with four on the rink,
        and typing five back in changed nothing -- the box already said five, so
        nobody was added and the player looked unrecoverable."""
        for team, spinbox in (("att", getattr(self, "att_spinbox", None)),
                              ("def", getattr(self, "def_spinbox", None))):
            if spinbox is None:
                continue
            try:
                spinbox.delete(0, tk.END)
                spinbox.insert(0, str(len(self._team_tokens(team))))
            except Exception:
                pass

    def _set_team_count(self, team, count):
        """Resize one team in place. _update_roster() would do it by deleting every
        token on the board and respawning the default formation, taking the other
        team and the undo history with it."""
        tokens = self._team_tokens(team)
        if len(tokens) == count:
            return
        if len(tokens) > count:
            for token in tokens[count:]:
                self._delete_token(token)
        else:
            shape = (self.att_shape_var if team == "att" else self.def_shape_var).get()
            color = self.att_color if team == "att" else self.def_color
            prefix = "A" if team == "att" else "D"
            size = max(6, int(self.player_size_var.get()))
            taken = {t.get("label") for t in tokens}
            index = 1
            while len(tokens) < count:
                label = f"{prefix}{index}"
                if label not in taken:
                    rink_len, rink_wid = self._rink_size()
                    spot_x, spot_y = self._rink_to_px(
                        rink_len * (0.3 if team == "att" else 0.7), rink_wid / 2)
                    sid = self._create_token(spot_x, spot_y, label, shape=shape,
                                             color=color, size=size, team=team)
                    tokens.append(self.tokens[sid])
                    taken.add(label)
                index += 1
        spinbox = self.att_spinbox if team == "att" else self.def_spinbox
        try:
            spinbox.delete(0, tk.END)
            spinbox.insert(0, str(count))
        except Exception:
            pass

    def _set_token_position(self, token, position):
        """Show a tactical role on the token. Clearing it (position None) falls back
        to the internal label, so undoing a formation restores A1..A5 / D1..D5."""
        token["position"] = position
        text = position or token.get("label", "")
        for tid in token.get("text_ids", []):
            try:
                self.canvas.itemconfig(tid, text=text)
            except Exception:
                pass

    def apply_tactic(self, team):
        """Lay a team out in the chosen formation. The percentage slides the whole
        shape along the rink: 0% sits deep in front of the team's own goal, 100%
        pushes it up against the opponent's."""
        name = (self.att_tactic_var if team == "att" else self.def_tactic_var).get()
        slots = self.FORMATIONS.get(name)
        if not slots:
            return
        try:
            pct = min(max(float((self.att_pct_var if team == "att" else self.def_pct_var).get()), 0.0), 100.0) / 100.0
        except Exception:
            pct = 0.5

        # A formation defines how many players are on the ice, so the roster follows
        # it: four-player shapes drop a player, five-player shapes bring one back.
        self._set_team_count(team, len(slots))

        tokens = self._team_tokens(team)
        if not tokens:
            messagebox.showinfo("Tactics", f"No {'attackers' if team == 'att' else 'defenders'} on the board.")
            return
        if not getattr(self, "pitch_scale", None):
            return

        rink_len, rink_wid = self._rink_size()
        zone = 0.35 * rink_len          # how much rink the formation spans front-to-back
        margin = 2.0                    # keep bodies off the boards

        # The two teams attack opposite ways, so "advanced" runs in opposite
        # directions along the rink for each of them.
        if team == "att":
            rear, forward = (0.05 + 0.40 * pct) * rink_len, 1.0
        else:
            rear, forward = (0.95 - 0.40 * pct) * rink_len, -1.0

        label_moves, positions = {}, {}
        for token, (position, across, depth) in zip(tokens, slots):
            mx = min(max(rear + forward * depth * zone, margin), rink_len - margin)
            my = margin + across * (rink_wid - 2 * margin)
            nx, ny = self._rink_to_px(mx, my)
            bbox = self.canvas.bbox(token.get("shape_id"))
            if not bbox:
                continue
            label_moves[token["label"]] = (nx - (bbox[0] + bbox[2]) / 2,
                                           ny - (bbox[1] + bbox[3]) / 2)
            positions[token["label"]] = position

        if label_moves:
            # One command carries the move, the roles and the timeline entry, so the
            # step is undoable, readable and saveable as a unit.
            self.push_command(ApplyTacticCommand(self, team, name, pct * 100,
                                                 label_moves, positions))

        if len(tokens) < len(slots):
            messagebox.showinfo(
                "Tactics",
                f"{name} uses {len(slots)} players but only {len(tokens)} are on the "
                f"board, so the last {len(slots) - len(tokens)} position(s) were skipped.")

    # How far in and out the board can be taken, and by how much a keypress moves.
    ZOOM_MIN = 0.4
    ZOOM_MAX = 4.0
    ZOOM_STEP = 1.25

    def set_zoom(self, value):
        """Draw the rink at this magnification.

        Everything on the board is held in rink metres, so a zoom is nothing more than
        a different scale in the projection: the players, the drawings and the images
        all land where they belong at the new size, exactly as they do after a resize
        or a change of field."""
        value = max(self.ZOOM_MIN, min(self.ZOOM_MAX, float(value)))
        if abs(value - self.zoom) < 1e-6:
            return False
        self.zoom = value
        self.redraw_canvas()
        self._update_indicators()
        return True

    def _pan_limits(self, w=None, h=None, rink_len=None, rink_wid=None, scale=None):
        """How far the board may be scrolled in each direction, in pixels.

        Only whatever the zoom has pushed past the edge of the canvas: half of the
        overhang either side, since the rink is centred."""
        if scale is None:
            scale = getattr(self, "pitch_scale", None)
        if not scale:
            return 0.0, 0.0
        if rink_len is None or rink_wid is None:
            rink_len, rink_wid = self._rink_size()
        if w is None or h is None:
            w, h = self.width, self.height
        span_x, span_y = ((rink_wid, rink_len) if self.rink_rotated
                          else (rink_len, rink_wid))
        return (max(0.0, (span_x * scale - w) / 2.0),
                max(0.0, (span_y * scale - h) / 2.0))

    def _clamp_pan(self, w=None, h=None, rink_len=None, rink_wid=None, scale=None):
        limit_x, limit_y = self._pan_limits(w, h, rink_len, rink_wid, scale)
        self.pan_x = max(-limit_x, min(limit_x, self.pan_x))
        self.pan_y = max(-limit_y, min(limit_y, self.pan_y))
        return limit_x, limit_y

    # How far one notch of the wheel moves the board.
    SCROLL_STEP = 60

    def scroll_board(self, dx=0, dy=0):
        """Move the view over a zoomed-in board. False when there is nothing hidden."""
        limit_x, limit_y = self._pan_limits()
        if limit_x <= 0 and limit_y <= 0:
            return False
        before = (self.pan_x, self.pan_y)
        self.pan_x += dx
        self.pan_y += dy
        self._clamp_pan()
        if (self.pan_x, self.pan_y) == before:
            return False
        self.redraw_canvas()
        return True

    def _on_mouse_wheel(self, event):
        """The wheel scrolls the board up and down; with Shift, left and right.

        X11 reports the wheel as buttons 4-7 and everything else as <MouseWheel> with
        a delta, so both arrive here."""
        step = self.SCROLL_STEP
        if getattr(event, "num", None) in (4, 6):
            amount = step
        elif getattr(event, "num", None) in (5, 7):
            amount = -step
        else:
            delta = getattr(event, "delta", 0)
            if not delta:
                return None
            amount = step if delta > 0 else -step
        sideways = bool(getattr(event, "state", 0) & 0x1) or \
            getattr(event, "num", None) in (6, 7)
        if sideways:
            self.scroll_board(dx=amount)
        else:
            self.scroll_board(dy=amount)
        return "break"

    def zoom_in(self, event=None):
        self.set_zoom(self.zoom * self.ZOOM_STEP)
        return "break"

    def zoom_out(self, event=None):
        self.set_zoom(self.zoom / self.ZOOM_STEP)
        return "break"

    def zoom_reset(self, event=None):
        """Back to the whole rink filling the canvas, and centred again."""
        self.pan_x = self.pan_y = 0.0
        if not self.set_zoom(1.0):
            self.redraw_canvas()
        return "break"

    def toggle_rink_orientation(self):
        self.rotate_rink("vertical" if not self.rink_rotated else "horizontal")

    def rotate_rink(self, orientation):
        target_rotated = (orientation == "vertical")
        if target_rotated == self.rink_rotated:
            return

        # A plain 90-degree geometric rotation about the canvas centre is not enough:
        # the pitch scale and origin generally change between orientations (the rink
        # is 40x20, not square, and the canvas usually is not square either). So
        # convert everything to rink metres under the old mapping, switch, then
        # re-project into pixels under the new one.
        old_st = self._pitch_state()
        if not old_st["scale"]:
            # Nothing drawn yet -- nothing to re-project, but we still owe the caller
            # a pitch in the requested orientation.
            self.rink_rotated = target_rotated
            self.redraw_canvas()
            return

        snap = self._snapshot_rink_positions(old_st)
        self.rink_rotated = target_rotated
        self._draw_pitch()
        self._restore_rink_positions(snap, old_st)
        # Rotating swaps the rink's aspect, so the roomier side for the toolbar may
        # have changed with it.
        self._save_config()

    def copy_current_style(self):
        self.copied_style = {
            "line_color": self.line_color,
            "line_thick": int(self.line_thick_var.get()),
            "line_type": self.line_type_var.get(),
            "att_color": self.att_color,
            "def_color": self.def_color
        }
        messagebox.showinfo("Style Copied", "Current drawing style copied.")

    def set_as_default_popup(self):
        if self.dont_bother_again:
            self._apply_style_as_default()
            return
        popup = tk.Toplevel(self.root)
        popup.title("Set Current Style As Default")
        popup.geometry("320x110")
        tk.Label(popup, text="Make current style the default?").pack(pady=6)
        dont_var = tk.BooleanVar(value=False)
        tk.Checkbutton(popup, text="Don't bother me again!", variable=dont_var).pack()
        def on_ok():
            self.dont_bother_again = dont_var.get()
            self._apply_style_as_default()
            popup.destroy()
        tk.Button(popup, text="OK", command=on_ok).pack(side=tk.LEFT, padx=10, pady=8)
        tk.Button(popup, text="Cancel", command=popup.destroy).pack(side=tk.RIGHT, padx=10, pady=8)

    def _apply_style_as_default(self):
        try:
            cfg = {
                "line_color": self.line_color,
                "line_thick": int(self.line_thick_var.get()),
                "line_type": self.line_type_var.get(),
                "att_color": self.att_color,
                "def_color": self.def_color,
                "sign_color": self.sign_color,
                "att_tactic": self.att_tactic_var.get(),
                "def_tactic": self.def_tactic_var.get(),
                "att_pct": self.att_pct_var.get(),
                "def_pct": self.def_pct_var.get(),
                "half_rink": self.half_rink_var.get(),
                "rink_mode": self.rink_mode,
                "grid": self.grid_var.get(),
                "snap_player": self.snap_player_var.get(),
                "snap_angle": self.snap_angle_var.get(),
                "ghosting": self.ghosting_var.get(),
                "dont_bother_again": self.dont_bother_again,
                "menu_two_rows": self.menu_two_rows,
                "menu_rows_mode": self.menu_rows_mode,
                "menu_position": self.menu_position,
                "rink_rotated": self.rink_rotated,
                "color_theme": self.color_theme
            }
            with open(self.config_path, "w") as f:
                json.dump(cfg, f, indent=2)
            messagebox.showinfo("Saved", "Current style saved as default.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save default style: {e}")

    # Semicolon-separated patterns are a Windows convention. Tk on X11 treats
    # "*.png;*.jpg" as one literal glob, which matches nothing and hides every image
    # in the folder, so each extension has to be its own pattern. The globs are also
    # case-sensitive here, hence the upper-case twins.
    IMAGE_FILETYPES = [
        ("Image Files", ("*.png", "*.jpg", "*.jpeg", "*.gif", "*.bmp",
                         "*.PNG", "*.JPG", "*.JPEG", "*.GIF", "*.BMP")),
        ("PNG", ("*.png", "*.PNG")),
        ("JPEG", ("*.jpg", "*.jpeg", "*.JPG", "*.JPEG")),
        ("All Files", "*"),
    ]

    # The watermark is held in rink metres -- centre plus width and height -- exactly
    # like the players, so it keeps its place and its size on the rink through a
    # resize, a rotation or a switch to the half rink.
    WATERMARK_DEFAULT_W_M = 12.0
    WATERMARK_MIN_M = 1.0
    # Logos are stored inside the macro file, so the working copy is capped: a phone
    # photo would otherwise turn a 40 kB tactic into several megabytes of base64.
    WATERMARK_MAX_PX = 1200

    def add_watermark(self):
        path = filedialog.askopenfilename(filetypes=self.IMAGE_FILETYPES)
        if not path:
            return
        try:
            image = Image.open(path).convert("RGBA")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to add watermark: {e}")
            return
        longest = max(image.width, image.height)
        if longest > self.WATERMARK_MAX_PX:
            ratio = self.WATERMARK_MAX_PX / longest
            image = image.resize((max(1, int(image.width * ratio)),
                                  max(1, int(image.height * ratio))),
                                 Image.Resampling.LANCZOS)
        # What undo has to come back to: whatever was on the board before this file was
        # picked, not the freshly loaded logo the editor is about to open with.
        self._watermark_baseline = (self._watermark_snapshot(), True)
        rink_len, rink_wid = self._rink_size()
        w_m = min(self.WATERMARK_DEFAULT_W_M, rink_len * 0.6)
        h_m = w_m * image.height / image.width
        if h_m > rink_wid * 0.8:            # keep the first placement inside the boards
            h_m = rink_wid * 0.8
            w_m = h_m * image.width / image.height
        self.watermark = {"path": path, "original": image, "crop": None,
                          "bg_tolerance": None, "bg_mode": None, "behind": True,
                          "opacity": 100,
                          "mx": rink_len / 2.0, "my": rink_wid / 2.0,
                          "w_m": w_m, "h_m": h_m}
        self._refresh_watermark_image()
        self._render_watermark()
        self._open_watermark_editor()

    # ---- image pipeline: original -> crop -> background removal -------------
    def _refresh_watermark_image(self, wm=None):
        """Rebuild the displayed image from the untouched original. Crop and
        background removal are kept as parameters rather than baked into the pixels,
        so either can be adjusted or undone at any time -- and so a macro can store
        the original plus two numbers instead of a flattened result."""
        wm = wm or self.watermark
        if not wm:
            return None
        image = wm["original"]
        crop = wm.get("crop")
        if crop:
            image = image.crop(tuple(int(v) for v in crop))
        tolerance = wm.get("bg_tolerance")
        if tolerance is not None:
            image = self._strip_background(image, tolerance, wm.get("bg_mode"))
        opacity = wm.get("opacity", 100)
        if opacity is not None and opacity < 100:
            # Scales whatever alpha is already there, so a background that removal
            # made fully transparent stays fully transparent.
            image = image.convert("RGBA")
            factor = max(0.0, min(100.0, float(opacity))) / 100.0
            image.putalpha(image.getchannel("A").point(lambda level: int(level * factor)))
        wm["image"] = image
        return image

    @staticmethod
    def _detect_background_mode(image):
        """Whitish or blackish background, decided from the luminance histogram.

        The tallest bin is taken as the dominant colour; a logo is mostly its
        background, so that peak is the background. Which half of the range it falls
        in says whether it is light or dark."""
        histogram = image.convert("L").histogram()
        peak = max(range(256), key=lambda level: histogram[level])
        return ("light" if peak >= 128 else "dark"), peak

    def _strip_background(self, image, tolerance, mode=None):
        """Make the background bins transparent.

        The cutoff is anchored on the dominant bin found in the histogram, not on pure
        white or black, so a slightly off-white card is caught at aggressiveness 0.
        `tolerance` (0-100) then says how much further in to reach: 0 clears the
        background bin and everything past it, 100 reaches all the way to mid-grey.
        Everything is done on the luminance channel, so a coloured logo on a white
        card keeps its colours."""
        image = image.convert("RGBA")
        detected_mode, peak = self._detect_background_mode(image)
        mode = mode or detected_mode
        reach = max(0.0, min(100.0, float(tolerance))) * 1.27
        luminance = image.convert("L")
        if mode == "light":
            cutoff = peak - reach
            keep = luminance.point(lambda level: 0 if level >= cutoff else 255)
        else:
            cutoff = peak + reach
            keep = luminance.point(lambda level: 0 if level <= cutoff else 255)
        # multiply, not replace: a PNG that already has transparency keeps it.
        image.putalpha(ImageChops.multiply(image.getchannel("A"), keep))
        return image

    def _render_watermark(self):
        """Draw the watermark where the rink is now. Called after every pitch redraw."""
        self.canvas.delete("watermark")
        self._watermark_photo = None
        wm = getattr(self, "watermark", None)
        st = self._pitch_state()
        if not wm or not st["scale"]:
            return
        px_w = max(1, int(round(wm["w_m"] * st["scale"])))
        px_h = max(1, int(round(wm["h_m"] * st["scale"])))
        cx, cy = self._state_m_to_px(wm["mx"], wm["my"], st)
        # The PhotoImage has to outlive this method or Tk garbage-collects it and
        # draws nothing at all.
        self._watermark_photo = ImageTk.PhotoImage(
            wm["image"].resize((px_w, px_h), Image.Resampling.LANCZOS))
        self.canvas.create_image(cx, cy, image=self._watermark_photo, tags=("watermark",))
        # "Behind everything" means behind the rink markings, the players and the
        # drawings -- but still in front of the rink's own white surface, which is
        # opaque. Lowering it under that surface is what made a loaded watermark
        # appear to do nothing at all. Unchecked, it sits over the markings instead,
        # and stays below the players either way.
        anchor = "pitch_surface" if wm.get("behind", True) else "pitch"
        for candidate in (anchor, "pitch"):
            try:
                if self.canvas.find_withtag(candidate):
                    self.canvas.tag_raise("watermark", candidate)
                    break
            except Exception:
                pass

    def _preview_pitch_state(self, rink_len, rink_wid=20.0, long_px=720, short_px=430,
                             pad=18):
        """Mapping for the placement preview: the same rink-metre projection the board
        itself uses, so a position picked in the preview lands identically on the rink."""
        scale = min(long_px / rink_len, short_px / rink_wid)
        st = {"scale": scale, "ox": pad, "oy": pad,
              "rotated": self.rink_rotated, "rink_len": rink_len, "rink_wid": rink_wid}
        span_x, span_y = ((rink_wid, rink_len) if self.rink_rotated
                          else (rink_len, rink_wid))
        return st, int(span_x * scale) + 2 * pad, int(span_y * scale) + 2 * pad

    def _draw_preview_rink(self, canvas, st, rink_len, rink_wid=20.0):
        """A stripped-down rink for the placement preview: boards, centre spot and
        circle, goal areas and goals. Enough to judge where a logo sits."""
        def p(mx, my):
            return self._state_m_to_px(mx, my, st)

        # Measured out of the same markings the real board is drawn from, so the
        # preview is the field the logo will actually land on.
        half = self.half_rink
        marks = self._rink_markings()
        mid = rink_wid / 2.0
        canvas.create_rectangle(*p(0, 0), *p(rink_len, rink_wid), fill="#ffffff",
                                outline="#343a40", width=2)
        centre_mx = 0.0 if half else rink_len / 2.0
        if not half:
            canvas.create_line(*p(centre_mx, 0), *p(centre_mx, rink_wid),
                               fill="#ced4da", width=2)
        circle_r = marks["circle"]            # the small fields have a spot but no circle
        if circle_r:
            canvas.create_oval(*p(centre_mx - circle_r, mid - circle_r),
                               *p(centre_mx + circle_r, mid + circle_r),
                               outline="#ced4da", width=2)
        canvas.create_oval(*p(centre_mx - 0.2, mid - 0.2), *p(centre_mx + 0.2, mid + 0.2),
                           fill="#343a40", outline="#343a40")
        goal_line = marks["goal_line"]
        # The outermost goal area: on the large field that is the goal area around
        # the crease, on the small ones the single goalkeeper area.
        area_depth, area_width = marks["areas"][-1]
        for goal_mx, inward in ((goal_line, 1.0), (rink_len - goal_line, -1.0)):
            if half and inward > 0:
                continue                      # the half rink only has the far goal
            canvas.create_rectangle(*p(goal_mx, mid - area_width / 2),
                                    *p(goal_mx + inward * area_depth, mid + area_width / 2),
                                    outline="#ced4da", width=2)
            canvas.create_rectangle(*p(goal_mx - inward * 0.4, mid - 0.8),
                                    *p(goal_mx, mid + 0.8),
                                    fill="#000000", outline="#000000")

    def _open_watermark_editor(self):
        wm = getattr(self, "watermark", None)
        if not wm:
            messagebox.showinfo("Watermark", "Load a watermark image first (Watermark button).")
            return
        # Fill in anything a caller left out, so the editor never depends on who built
        # the dict -- the file picker, a macro load, or a plain image with no history.
        wm.setdefault("original", wm.get("image"))
        for key in ("crop", "bg_tolerance", "bg_mode"):
            wm.setdefault(key, None)
        wm.setdefault("behind", True)
        wm.setdefault("opacity", 100)
        if not wm.get("image"):
            self._refresh_watermark_image(wm)
        original = dict(wm)
        rink_len, rink_wid = self._rink_size()
        st, cv_w, cv_h = self._preview_pitch_state(rink_len, rink_wid)

        win = tk.Toplevel(self.root)
        win.title("Place Watermark")
        win.transient(self.root)
        win.configure(bg=self.C_PANEL)

        tk.Label(win, bg=self.C_PANEL, fg=self.C_TEXT, font=(self.UI_FONT, 9),
                 text="Drag the logo to move it.  Drag a corner to resize it.  "
                      "Hold Shift while dragging a corner to scale it evenly.").pack(
            padx=14, pady=(12, 8))

        cv = tk.Canvas(win, width=cv_w, height=cv_h, bg=self.C_SURFACE,
                       highlightthickness=1, highlightbackground=self.C_BORDER)
        cv.pack(padx=14)

        size_label = tk.Label(win, bg=self.C_PANEL, fg=self.C_MUTED, font=(self.UI_FONT, 8))
        size_label.pack(pady=(6, 0))

        state = {"mx": wm["mx"], "my": wm["my"], "w_m": wm["w_m"], "h_m": wm["h_m"],
                 "photo": None, "drag": None, "crop_mode": False, "crop_rect": None}
        HANDLE = 5

        def box_px():
            cx, cy = self._state_m_to_px(state["mx"], state["my"], st)
            hw = state["w_m"] * st["scale"] / 2.0
            hh = state["h_m"] * st["scale"] / 2.0
            return cx - hw, cy - hh, cx + hw, cy + hh

        def redraw():
            cv.delete("all")
            self._draw_preview_rink(cv, st, rink_len, rink_wid)
            x1, y1, x2, y2 = box_px()
            px_w = max(1, int(round(x2 - x1)))
            px_h = max(1, int(round(y2 - y1)))
            # BILINEAR, not LANCZOS: this runs on every mouse motion.
            state["photo"] = ImageTk.PhotoImage(
                wm["image"].resize((px_w, px_h), Image.Resampling.BILINEAR))
            cv.create_image((x1 + x2) / 2, (y1 + y2) / 2, image=state["photo"])
            cv.create_rectangle(x1, y1, x2, y2, outline=self.C_ACCENT, dash=(4, 3))
            if state["crop_mode"]:
                if state["crop_rect"]:
                    cv.create_rectangle(*state["crop_rect"], outline="#e03131", width=2)
            else:
                for hx, hy in ((x1, y1), (x2, y1), (x1, y2), (x2, y2)):
                    cv.create_rectangle(hx - HANDLE, hy - HANDLE, hx + HANDLE, hy + HANDLE,
                                        fill=self.C_ACCENT, outline=self.C_SURFACE)
            size_label.config(text=f"{state['w_m']:.1f} m  x  {state['h_m']:.1f} m   "
                                   f"at  {state['mx']:.1f} m, {state['my']:.1f} m   "
                                   f"[{wm['image'].width}x{wm['image'].height} px]")

        def on_press(event):
            x1, y1, x2, y2 = box_px()
            if state["crop_mode"]:
                # Crop is dragged out inside the logo, so it starts clamped to it.
                sx = min(max(event.x, x1), x2)
                sy = min(max(event.y, y1), y2)
                state["drag"] = {"mode": "crop", "start": (sx, sy)}
                state["crop_rect"] = (sx, sy, sx, sy)
                return
            for name, (hx, hy) in (("nw", (x1, y1)), ("ne", (x2, y1)),
                                   ("sw", (x1, y2)), ("se", (x2, y2))):
                if abs(event.x - hx) <= HANDLE + 3 and abs(event.y - hy) <= HANDLE + 3:
                    # The opposite corner stays put while the grabbed one moves.
                    anchor = (x2 if "w" in name else x1, y2 if "n" in name else y1)
                    state["drag"] = {"mode": "scale", "anchor": anchor,
                                     "aspect": state["w_m"] / max(state["h_m"], 1e-6)}
                    return
            if x1 <= event.x <= x2 and y1 <= event.y <= y2:
                gx, gy = self._state_px_to_m(event.x, event.y, st)
                state["drag"] = {"mode": "move",
                                 "grab": (state["mx"] - gx, state["my"] - gy)}

        def on_motion(event):
            drag = state["drag"]
            if not drag:
                return
            if drag["mode"] == "crop":
                bx1, by1, bx2, by2 = box_px()
                sx, sy = drag["start"]
                ex = min(max(event.x, bx1), bx2)
                ey = min(max(event.y, by1), by2)
                state["crop_rect"] = (min(sx, ex), min(sy, ey), max(sx, ex), max(sy, ey))
                redraw()
                return
            if drag["mode"] == "move":
                gx, gy = self._state_px_to_m(event.x, event.y, st)
                state["mx"] = min(max(gx + drag["grab"][0], 0.0), rink_len)
                state["my"] = min(max(gy + drag["grab"][1], 0.0), rink_wid)
            else:
                ax, ay = drag["anchor"]
                w_m = abs(event.x - ax) / st["scale"]
                h_m = abs(event.y - ay) / st["scale"]
                if event.state & 0x0001:      # Shift: keep the logo's proportions
                    aspect = drag["aspect"]
                    if w_m / max(h_m, 1e-6) > aspect:
                        h_m = w_m / aspect
                    else:
                        w_m = h_m * aspect
                w_m = max(self.WATERMARK_MIN_M, w_m)
                h_m = max(self.WATERMARK_MIN_M, h_m)
                amx, amy = self._state_px_to_m(ax, ay, st)
                # Grow away from the anchor, on whichever side the pointer is.
                gx, gy = self._state_px_to_m(event.x, event.y, st)
                state["w_m"], state["h_m"] = w_m, h_m
                sign_x = 1.0 if gx >= amx else -1.0
                sign_y = 1.0 if gy >= amy else -1.0
                dx_m, dy_m = (w_m, h_m) if not st["rotated"] else (h_m, w_m)
                state["mx"] = amx + sign_x * dx_m / 2.0
                state["my"] = amy + sign_y * dy_m / 2.0
            redraw()

        def on_release(_event):
            state["drag"] = None

        cv.bind("<ButtonPress-1>", on_press)
        cv.bind("<B1-Motion>", on_motion)
        cv.bind("<ButtonRelease-1>", on_release)

        btn_cfg = {"font": (self.UI_FONT, 8), "relief": tk.FLAT, "cursor": "hand2",
                   "padx": 4, "pady": 3, "bg": self.C_BTN, "fg": self.C_TEXT,
                   "activebackground": self.C_BTN_HOVER, "bd": 0, "highlightthickness": 1,
                   "highlightbackground": self.C_BORDER}

        # ---- image tools: crop and background removal ----
        tools = tk.Frame(win, bg=self.C_PANEL)
        tools.pack(padx=14, pady=(10, 0), fill=tk.X)

        crop_btn = tk.Button(tools, text="Crop", **btn_cfg)
        crop_btn.pack(side=tk.LEFT, padx=(0, 4))
        reset_btn = tk.Button(tools, text="Reset Image", **btn_cfg)
        reset_btn.pack(side=tk.LEFT, padx=4)

        detected_mode, peak = self._detect_background_mode(wm["original"])
        bg_var = tk.BooleanVar(value=wm.get("bg_tolerance") is not None)
        tol_var = tk.IntVar(value=wm.get("bg_tolerance") if wm.get("bg_tolerance") is not None else 20)

        tk.Checkbutton(tools, text="Remove background", variable=bg_var,
                       bg=self.C_PANEL, fg=self.C_TEXT, activebackground=self.C_PANEL,
                       selectcolor=self.C_SURFACE, font=(self.UI_FONT, 8),
                       command=lambda: apply_background()).pack(side=tk.LEFT, padx=(16, 4))
        tk.Label(tools, text=f"({detected_mode} background detected, peak {peak})",
                 bg=self.C_PANEL, fg=self.C_MUTED, font=(self.UI_FONT, 8)).pack(side=tk.LEFT)

        behind_var = tk.BooleanVar(value=bool(wm.get("behind", True)))

        def apply_behind():
            wm["behind"] = bool(behind_var.get())
            self._render_watermark()

        tk.Checkbutton(tools, text="Behind everything", variable=behind_var,
                       bg=self.C_PANEL, fg=self.C_TEXT, activebackground=self.C_PANEL,
                       selectcolor=self.C_SURFACE, font=(self.UI_FONT, 8),
                       command=apply_behind).pack(side=tk.RIGHT)

        tol_row = tk.Frame(win, bg=self.C_PANEL)
        tol_row.pack(padx=14, pady=(2, 0), fill=tk.X)
        scale_cfg = {"from_": 0, "to": 100, "orient": tk.HORIZONTAL, "bg": self.C_PANEL,
                     "fg": self.C_TEXT, "troughcolor": self.C_SURFACE,
                     "highlightthickness": 0, "font": (self.UI_FONT, 7), "length": 200}
        tk.Label(tol_row, text="Aggressiveness", bg=self.C_PANEL, fg=self.C_TEXT,
                 font=(self.UI_FONT, 8)).pack(side=tk.LEFT)
        tol_scale = tk.Scale(tol_row, variable=tol_var,
                             command=lambda _v: apply_background(), **scale_cfg)
        tol_scale.pack(side=tk.LEFT, padx=8, fill=tk.X, expand=True)

        opacity_var = tk.IntVar(value=int(wm.get("opacity", 100)))

        def apply_opacity(_value=None):
            wm["opacity"] = int(opacity_var.get())
            self._refresh_watermark_image(wm)
            redraw()

        tk.Label(tol_row, text="Opacity", bg=self.C_PANEL, fg=self.C_TEXT,
                 font=(self.UI_FONT, 8)).pack(side=tk.LEFT, padx=(14, 0))
        tk.Scale(tol_row, variable=opacity_var, command=apply_opacity,
                 **scale_cfg).pack(side=tk.LEFT, padx=8, fill=tk.X, expand=True)

        def apply_background():
            wm["bg_mode"] = detected_mode
            wm["bg_tolerance"] = int(tol_var.get()) if bg_var.get() else None
            self._refresh_watermark_image(wm)
            redraw()

        def toggle_crop():
            if state["crop_mode"] and state["crop_rect"]:
                # Second click commits: translate the box drawn on screen into pixels
                # of the original, through whatever crop is already in force.
                bx1, by1, bx2, by2 = box_px()
                cx1, cy1, cx2, cy2 = state["crop_rect"]
                span_x = max(bx2 - bx1, 1e-6)
                span_y = max(by2 - by1, 1e-6)
                left, top, right, bottom = wm.get("crop") or (0, 0, wm["original"].width,
                                                              wm["original"].height)
                cur_w, cur_h = right - left, bottom - top
                new_crop = (left + (cx1 - bx1) / span_x * cur_w,
                            top + (cy1 - by1) / span_y * cur_h,
                            left + (cx2 - bx1) / span_x * cur_w,
                            top + (cy2 - by1) / span_y * cur_h)
                if new_crop[2] - new_crop[0] >= 2 and new_crop[3] - new_crop[1] >= 2:
                    wm["crop"] = new_crop
                    self._refresh_watermark_image(wm)
                    # The kept region stays exactly where it was drawn on the rink,
                    # rather than stretching back out to fill the old box.
                    state["w_m"] *= (cx2 - cx1) / span_x
                    state["h_m"] *= (cy2 - cy1) / span_y
                    new_cx, new_cy = (cx1 + cx2) / 2, (cy1 + cy2) / 2
                    state["mx"], state["my"] = self._state_px_to_m(new_cx, new_cy, st)
                state["crop_mode"] = False
                state["crop_rect"] = None
                crop_btn.config(text="Crop")
            elif state["crop_mode"]:
                state["crop_mode"] = False
                crop_btn.config(text="Crop")
            else:
                state["crop_mode"] = True
                state["crop_rect"] = None
                crop_btn.config(text="Apply Crop")
            redraw()

        def reset_image():
            wm["crop"] = None
            state["crop_mode"] = False
            state["crop_rect"] = None
            crop_btn.config(text="Crop")
            self._refresh_watermark_image(wm)
            # Back to the untouched aspect, keeping the current width.
            state["h_m"] = state["w_m"] * wm["image"].height / wm["image"].width
            redraw()

        crop_btn.config(command=toggle_crop)
        reset_btn.config(command=reset_image)

        btns = tk.Frame(win, bg=self.C_PANEL)
        btns.pack(padx=14, pady=12, fill=tk.X)

        # What the board looked like before this edit, so Apply and Remove can be pushed
        # as undoable commands that also write themselves into the timeline. When the
        # editor was opened straight off a file pick, add_watermark leaves the pre-load
        # state here -- otherwise undoing a freshly loaded logo would undo to itself.
        baseline = getattr(self, "_watermark_baseline", None)
        if baseline:
            before_data = baseline[0]
            self._watermark_baseline = None
        else:
            before_data = self._watermark_snapshot()

        def record(after_data):
            command = SetWatermarkCommand(self, after_data, before_data)
            # The change is already on the canvas, so the command is pushed without
            # executing; the timeline line is written here in its place.
            self.push_command(command, execute=False)
            self.action_steps.append(command.step_desc)
            try:
                self.steps_listbox.insert(tk.END, command.step_desc)
            except Exception:
                pass

        def apply_and_close():
            self.watermark.update({k: state[k] for k in ("mx", "my", "w_m", "h_m")})
            self._render_watermark()
            record(self._watermark_snapshot())
            win.destroy()

        def cancel():
            self.watermark = original
            self._render_watermark()
            win.destroy()

        def remove():
            self.watermark = None
            self._render_watermark()
            record(None)
            win.destroy()

        tk.Button(btns, text="Apply", command=apply_and_close, **btn_cfg).pack(side=tk.RIGHT, padx=3)
        tk.Button(btns, text="Cancel", command=cancel, **btn_cfg).pack(side=tk.RIGHT, padx=3)
        tk.Button(btns, text="Remove", command=remove, **btn_cfg).pack(side=tk.LEFT, padx=3)
        win.protocol("WM_DELETE_WINDOW", cancel)

        redraw()
        self._make_modal(win)

    def copy_selection(self):
        data = []
        token_ids = []
        for sid in self.selected_tokens:
            token = self.tokens.get(sid)
            if token and token["shape_id"] not in token_ids:
                token_ids.append(token["shape_id"])
                coords = self.canvas.coords(token["shape_id"])
                cx = (coords[0] + coords[2]) / 2 if coords else token.get("starting_pos", (0,0))[0]
                cy = (coords[1] + coords[3]) / 2 if coords else token.get("starting_pos", (0,0))[1]
                data.append({"type": "token", "label": token["label"], "shape": token["shape"], "color": token["color"], "cx": cx, "cy": cy, "size": token.get("size",14)})
        drawn_ids = sorted(list(self.selected_drawn))
        for cid in drawn_ids:
            meta = self.drawn_items.get(cid)
            if not meta: continue
            coords = self.canvas.coords(cid)
            data.append({"type": "drawn", "meta": meta, "coords": coords})
        self.clipboard = data
        messagebox.showinfo("Copied", f"Copied {len(data)} items.")

    # Text fields are where Delete and Backspace mean "edit this text". The board
    # only claims the keys when the focus is not in one, so editing a roster count or
    # a tactic percentage never wipes players off the rink.
    TEXT_ENTRY_WIDGETS = (tk.Entry, tk.Spinbox, tk.Text, ttk.Entry, ttk.Combobox,
                          ttk.Spinbox)

    def _delete_key(self, event=None):
        try:
            focused = self.root.focus_get()
        except Exception:
            focused = None
        if isinstance(focused, self.TEXT_ENTRY_WIDGETS):
            return None
        self.delete_selection()
        return "break"

    def _remove_key(self, event=None):
        """Backspace: the second way to remove something.

        Delete takes a mark off the board there and then. Backspace instead writes the
        removal into the play: the mark stays on the rink while you work, the timeline
        gains a line saying it goes, and during the animation it disappears when that
        group comes up. Players have no such half-state, so for them Backspace is a
        plain delete."""
        try:
            focused = self.root.focus_get()
        except Exception:
            focused = None
        if isinstance(focused, self.TEXT_ENTRY_WIDGETS):
            return None
        if self.selected_drawn:
            self.remove_selection_at_group()
        elif self.selected_tokens:
            self.delete_selection()
        return "break"

    def _drawn_label(self, cid):
        """What to call a drawing in the timeline."""
        meta = self.drawn_items.get(cid) or {}
        name = (meta.get("sign_type") or meta.get("tool")
                or meta.get("type") or "drawing")
        return str(name).capitalize()

    def remove_selection_at_group(self):
        """Mark the selection as going away when the open group is reached."""
        index = max(0, len(self.animation_steps) - 1)
        marked = []
        for cid in self._selected_drawn_ids():
            meta = self.drawn_items.get(cid)
            if meta is None or meta.get("decor"):
                continue
            meta["anim_remove_group"] = index
            marked.append(cid)
        if not marked:
            return 0
        # One line per mark, not per canvas item: a ball is four items and a shot is
        # three, and the timeline should not say so four times.
        for name in dict.fromkeys(self._drawn_label(cid) for cid in marked):
            self.record_action_in_group(f"Remove {name}")
        self._refresh_animation_list()
        self.highlight_selected()
        return len(marked)

    def delete_selection(self, event=None):
        """Remove everything selected: players, signs and drawn lines alike.

        Delete used to be bound to nothing at all, and the deletion inside Cut removed
        only a token's main shape, leaving its outline rings and the extra strokes of
        an X orphaned on the canvas."""
        removed = 0
        seen = set()
        doomed = []
        for sid in list(self.selected_tokens):
            token = self.tokens.get(sid)
            if not token or token.get("locked", False):
                continue
            key = token.get("shape_id")
            if key in seen:
                continue
            seen.add(key)
            doomed.append(token)
        if doomed:
            # Through a command: a player deleted by mistake used to be gone for good,
            # with undo stepping straight past it to whatever happened before.
            self.push_command(DeleteTokensCommand(self, doomed))
            removed += len(doomed)
        for cid in self._selected_drawn_ids():
            try:
                self.canvas.delete(cid)
            except Exception:
                pass
            self.drawn_items.pop(cid, None)
            # Drop the picture with the item, or its PIL copy is kept alive forever.
            self.board_images.pop(cid, None)
            removed += 1
        self.selected_drawn.clear()
        self.selected_tokens = [s for s in self.selected_tokens if s in self.tokens]
        if self.selected_pitch_parts:
            # Through a command: a goal or a face-off cross cannot be drawn back by
            # hand, so removing one has to be something undo can take back.
            parts = set(self.selected_pitch_parts)
            self.push_command(HidePitchPartsCommand(self, parts))
            removed += len(parts)
        if removed:
            self.highlight_selected()
        return removed

    # ----------------------
    # Right-click context menu
    # ----------------------
    def _context_target(self, event):
        """What the pointer is over, selecting it if it was not already part of the
        selection -- the behaviour people expect from a right-click."""
        items = self.canvas.find_overlapping(event.x - 2, event.y - 2,
                                             event.x + 2, event.y + 2)
        for cid in reversed(items):
            token = self.tokens.get(cid)
            if token:
                sid = token["shape_id"]
                if sid not in self.selected_tokens:
                    self.clear_selection()
                    self.selected_tokens = [sid]
                    self.highlight_selected()
                return "token"
            if cid in self.drawn_items:
                if cid not in self.selected_drawn:
                    self.clear_selection()
                    self.selected_drawn = self._drawn_siblings(cid)
                    self.highlight_selected()
                return "drawn"
        part = self._pitch_part_at(event.x, event.y)
        if part:
            if part not in self.selected_pitch_parts:
                self.clear_selection()
                self.selected_pitch_parts = {part}
                self.highlight_selected()
            return "pitch"
        return "empty"

    def show_context_menu(self, event):
        target = self._context_target(event)
        menu = tk.Menu(self.root, tearoff=0, font=(self.UI_FONT, 9))

        if target in ("token", "drawn"):
            menu.add_command(label=f"Rotate {self.ROTATE_STEP}°", command=self.rotate_selected)
            menu.add_command(label="Change Colour...", command=self.recolor_selection)
            if target == "token":
                menu.add_command(label="Copy / Paste Style", command=self.toggle_copy_paste_style)
                menu.add_separator()
                menu.add_command(label="Group", command=self.group_selected)
                locked = any((self.tokens.get(sid) or {}).get("locked")
                             for sid in self.selected_tokens)
                menu.add_command(label="Unlock" if locked else "Lock",
                                 command=self.unlock_selected if locked else self.lock_selected)
                menu.add_separator()
                menu.add_command(label="Align Horizontally",
                                 command=lambda: self.align_tokens("horizontal"))
                menu.add_command(label="Align Vertically",
                                 command=lambda: self.align_tokens("vertical"))
            menu.add_separator()
            menu.add_command(label="Copy", command=self.copy_selection)
            menu.add_command(label="Cut", command=self.cut_selection)
            menu.add_command(label="Delete", command=self.delete_selection)
        elif target == "pitch":
            name = ", ".join(sorted({self.pitch_part_label(key)
                                     for key in self.selected_pitch_parts}))
            menu.add_command(label=f"Delete {name}", command=self.delete_selection)
            menu.add_command(label="Restore Rink Features",
                             command=self.restore_pitch_parts,
                             state=tk.NORMAL if self.hidden_pitch_parts else tk.DISABLED)
            menu.add_separator()
            menu.add_command(label="Undo", command=self.undo,
                             state=tk.NORMAL if self.undo_stack else tk.DISABLED)
        else:
            menu.add_command(label="Paste", command=self.paste_clipboard,
                             state=tk.NORMAL if self.clipboard else tk.DISABLED)
            menu.add_command(label="Select All", command=self.select_all)
            has_ghosts = any(t.get("is_ghost") for t in self.tokens.values())
            menu.add_command(label="Clear Ghosts", command=self.clear_ghosts,
                             state=tk.NORMAL if has_ghosts else tk.DISABLED)
            menu.add_command(label="Restore Rink Features",
                             command=self.restore_pitch_parts,
                             state=tk.NORMAL if self.hidden_pitch_parts else tk.DISABLED)
            menu.add_separator()
            menu.add_command(label="Undo", command=self.undo,
                             state=tk.NORMAL if self.undo_stack else tk.DISABLED)
            menu.add_command(label="Redo", command=self.redo,
                             state=tk.NORMAL if self.redo_stack else tk.DISABLED)
            menu.add_separator()
            menu.add_command(label="Preferences...", command=self.open_preferences)

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def recolor_selection(self):
        """Recolour whatever is selected -- the same picker the toolbar swatches use."""
        chosen = colorchooser.askcolor(title="Choose Colour")[1]
        if not chosen:
            return
        for sid in list(self.selected_tokens):
            token = self.tokens.get(sid)
            if not token:
                continue
            token["color"] = chosen
            skip = set(token.get("decor_ids", ())) | set(token.get("halo_ids", ()))
            for item in self._token_items(token):
                if item in skip:      # the white halo and dark edge stay as they are
                    continue
                try:
                    self.canvas.itemconfig(item, fill=chosen)
                except Exception:
                    pass
        for cid in self._selected_drawn_ids():
            meta = self.drawn_items.get(cid)
            if not meta:
                continue
            meta["color"] = chosen
            for option in meta.get("color_options") or ("fill",):
                try:
                    if self.canvas.itemcget(cid, option):
                        self.canvas.itemconfig(cid, **{option: chosen})
                except Exception:
                    pass

    def cut_selection(self):
        self.copy_selection()
        for sid in list(self.selected_tokens):
            token = self.tokens.get(sid)
            if token:
                shape_id = token["shape_id"]
                try:
                    self.canvas.delete(shape_id)
                except Exception:
                    pass
                for tid in token.get("text_ids", []):
                    try:
                        self.canvas.delete(tid)
                    except Exception:
                        pass
                for k in list(self.tokens.keys()):
                    if self.tokens.get(k) == token:
                        self.tokens.pop(k, None)
        for cid in list(self.selected_drawn):
            try:
                self.canvas.delete(cid)
            except Exception:
                pass
            self.drawn_items.pop(cid, None)
        self.selected_tokens.clear()
        self.selected_drawn.clear()
        messagebox.showinfo("Cut", "Selected items removed and copied.")

    def paste_clipboard(self):
        if not self.clipboard:
            return
        existing_labels = {tok["label"] for tok in [v for v in self.tokens.values()] if isinstance(tok, dict) and tok.get("label")}
        offset_x, offset_y = 20, 20
        for item in self.clipboard:
            if item["type"] == "token":
                label = item["label"]
                new_label = label
                if new_label in existing_labels:
                    base = new_label
                    num = 1
                    if "_" in new_label and new_label.rsplit("_", 1)[-1].isdigit():
                        base = new_label.rsplit("_", 1)[0]
                    elif new_label and new_label[-1].isdigit():
                        i = len(new_label)-1
                        while i>=0 and new_label[i].isdigit():
                            i-=1
                        base = new_label[:i+1]
                        suffix = new_label[i+1:]
                        try:
                            num = int(suffix) + 1
                        except Exception:
                            num = 1
                    candidate = f"{base}_{num}"
                    while candidate in existing_labels:
                        num += 1
                        candidate = f"{base}_{num}"
                    new_label = candidate
                existing_labels.add(new_label)
                cx = item["cx"] + offset_x
                cy = item["cy"] + offset_y
                self._create_token(cx, cy, new_label, shape=item.get("shape","circle"), color=item.get("color","black"))
            elif item["type"] == "drawn":
                coords = item["coords"][:]
                new_coords = []
                for i, v in enumerate(coords):
                    if i % 2 == 0:
                        new_coords.append(v + offset_x)
                    else:
                        new_coords.append(v + offset_y)
                meta = item.get("meta",{})
                tool = meta.get("tool", "line")
                if len(new_coords) >= 4:
                    x1,y1,x2,y2 = new_coords[0], new_coords[1], new_coords[-2], new_coords[-1]
                else:
                    x1,y1,x2,y2 = new_coords[0], new_coords[1], new_coords[0]+20, new_coords[1]+20
                cmd = DrawLineCommand(self, tool, x1, y1, x2, y2, extra_data=meta.get("data",{}))
                self.push_command(cmd, execute=True)
        messagebox.showinfo("Pasted", "Pasted clipboard items.")

    def select_all(self):
        # Everything the user put on the board -- not the rink it is played on. Select
        # All followed by Delete is a common enough reflex that including the goals
        # and the face-off crosses in it would be a trap.
        self.selected_tokens.clear()
        self.selected_drawn.clear()
        self.selected_pitch_parts.clear()
        seen = set()
        for sid, token in self.tokens.items():
            if token and token.get("shape_id") not in seen:
                seen.add(token["shape_id"])
                self.selected_tokens.append(token["shape_id"])
        for cid in self.drawn_items.keys():
            self.selected_drawn.add(cid)
        self.highlight_selected()

    def toggle_grid_visuals(self):
        if self.grid_var.get():
            self.draw_grid_points()
        else:
            self.canvas.delete("grid_point")

    def draw_grid_points(self):
        self.canvas.delete("grid_point")
        if not self.grid_var.get():
            return
        w, h = self.width, self.height
        r = 1 
        for x in range(0, w + self.GRID, self.GRID):
            for y in range(0, h + self.GRID, self.GRID):
                self.canvas.create_oval(x-r, y-r, x+r, y+r, fill="#adb5bd", outline="#adb5bd", tags=("grid_point",))
        self.canvas.tag_lower("grid_point")
        self.canvas.tag_lower("pitch")

    def redraw_canvas(self):
        """Redraw the pitch, keeping every token and drawing on the same spot on the
        rink. It deliberately does NOT call _update_roster(): that deletes every token
        and respawns the default formation (along with the undo history), which is why
        resizing the window or toggling half-rink used to reset the board."""
        old_st = self._pitch_state()
        snap = self._snapshot_rink_positions(old_st) if old_st["scale"] else None
        self._draw_pitch()
        if snap:
            self._restore_rink_positions(snap, old_st)

    # ----------------------
    # Rink size
    # ----------------------
    # The fields are named after the game they are played with, because that is what
    # a coach picks. Length x width in metres, always the whole field: the half rink
    # is a view of one end of whichever of these is chosen, not a field of its own.
    #   5v5  IFF large-field floorball, 40 x 20 m.
    #   4v4  NeFUB small field, 27 x 15 m.
    #   3v3  NeFUB / IFF 3v3, 22 x 11 m (Rules of the Game 2025, rule 101).
    RINK_SIZES = {"5v5": (40.0, 20.0), "4v4": (27.0, 15.0), "3v3": (22.0, 11.0)}
    RINK_LABELS = {mode: mode for mode in RINK_SIZES}
    # On the button itself the field needs saying out loud, or it reads as the
    # old full/half toggle it replaced.
    RINK_BUTTON_LABELS = {mode: f"Rink: {name}"
                          for mode, name in RINK_LABELS.items()}
    RINK_ORDER = ("5v5", "4v4", "3v3")

    # What the fields used to be called, and whether that name meant the half rink.
    # Config files, saved boards and recorded macros written before the fields were
    # named after the game still speak this language, so it is understood on the way
    # in -- and only on the way in; nothing is written back out in it.
    LEGACY_RINK_MODES = {"full": ("5v5", False), "half": ("5v5", True),
                         "small": ("4v4", False)}

    # The goal cage is the same on every field: 1.6 m between the posts, 1.15 m to
    # the crossbar (which a plan view cannot show), 0.65 m deep.
    # Shown in the bottom-left corner of the board.
    CREDIT_TEXT = "\u00a9 Simon Wagener"

    GOAL_MOUTH_M = 1.6
    GOAL_HEIGHT_M = 1.15
    GOAL_DEPTH_M = 0.65

    # What each field is marked with, in metres.
    #   corner_r    radius of the rounded corners of the boards
    #   goal_line   distance from the end boards to the goal line
    #   areas       the goal areas in front of each goal, as (depth, width)
    #   circle      radius of the centre circle, or None where there is none
    #   faceoff     how far the face-off marks sit in from the boards
    #   penalty     distance from the goal line to the penalty spot, or None
    #   sub_zone    substitution zones as (start from the centre line, length)
    # The large field is the IFF one the app has always drawn. The 3v3 figures come
    # from the NeFUB / IFF 3v3 Rules of the Game 2025, rules 101-104; the small-field
    # figures from the NeFUB small-field diagram. The half rink borrows the markings
    # of the field it is a half of, which is the whole point of it being a view
    # rather than a field of its own.
    RINK_MARKINGS = {
        "5v5": {"corner_r": 2.0, "goal_line": 2.85, "areas": ((1.0, 2.5), (4.0, 5.0)),
                "circle": 3.0, "faceoff": 2.85, "penalty": None, "sub_zone": None},
        "4v4": {"corner_r": 1.5, "goal_line": 1.8, "areas": ((0.9, 1.9),),
                "circle": None, "faceoff": 1.0, "penalty": 7.0, "sub_zone": (0.0, 5.0)},
        "3v3": {"corner_r": 1.0, "goal_line": 2.5, "areas": ((1.0, 2.5),),
                "circle": None, "faceoff": 2.0, "penalty": 5.0, "sub_zone": (4.0, 4.0)},
    }

    def _rink_markings(self):
        return self.RINK_MARKINGS[self.rink_mode]

    @property
    def rink_mode(self):
        mode = self.rink_mode_var.get()
        return mode if mode in self.RINK_SIZES else self.RINK_ORDER[0]

    @property
    def half_rink(self):
        """Whether only one end of the field is being drawn. Every field has a half."""
        return bool(self.half_rink_var.get())

    def _resolve_rink_mode(self, mode):
        """A field name, old or current, as (mode, half) -- where half is None unless
        the name itself said which half it meant. An unknown name is no field at all,
        so it comes back as (None, None) for the caller to fall back from."""
        if mode in self.RINK_SIZES:
            # A current name says nothing about the half: that is its own switch now.
            return mode, None
        return self.LEGACY_RINK_MODES.get(mode, (None, None))

    def _read_rink_mode(self, data):
        """The field a config file or a saved board asks for, as (mode, half).

        The half is taken from the file's own flag where it has one, and otherwise
        from the field name, which is all the oldest files have to say about it."""
        mode, implied_half = self._resolve_rink_mode(data.get("rink_mode"))
        if mode is None:
            mode = self.RINK_ORDER[0]
        half = data.get("half_rink")
        if half is None:
            half = implied_half if implied_half is not None else self.half_rink
        return mode, bool(half)

    def _rink_size(self):
        """The rink being drawn, as (length, width) in metres.

        A half rink is the goal end of its field: half the length, full width."""
        length, width = self.RINK_SIZES[self.rink_mode]
        return (length / 2.0 if self.half_rink else length), width

    def set_rink_mode(self, mode, redraw=True, half=None):
        """Switch between the three fields, optionally setting the half rink with it.

        Everything on the board is held in rink metres, so the players and the
        drawings keep their place on the rink across the change rather than being
        left where their pixels happened to be."""
        mode, implied_half = self._resolve_rink_mode(mode)
        if mode is None:
            return
        # An old name that meant the half rink still means it, so macros and files
        # recorded against the old full/half toggle replay as they were recorded.
        if half is None:
            half = self.half_rink if implied_half is None else implied_half
        half = bool(half)
        # Not skipped when nothing appears to change: the Preferences radio buttons
        # have already moved the variable by the time this runs, so a guard against
        # the current mode would see its own new value and leave the board undrawn.
        self.rink_mode_var.set(mode)
        self.half_rink_var.set(half)
        self._set_rink_team_sizes(mode)
        self._update_indicators()
        if redraw:
            self.redraw_canvas()
        self._save_config()

    def set_half_rink(self, half, redraw=True):
        """Show one end of the current field, or all of it.

        Deliberately not guarded against being told what is already true: the
        Preferences checkbox has flipped the variable before it calls this, and a
        guard would then see no change and leave the board undrawn."""
        self.half_rink_var.set(bool(half))
        self._update_indicators()
        if redraw:
            self.redraw_canvas()
        self._save_config()

    def toggle_half_rink(self):
        self.set_half_rink(not self.half_rink)

    # How many players a side each field is played with. The half rink does not
    # change this: half a 5v5 field is still 5v5, being drilled at one end.
    RINK_TEAM_SIZES = {"5v5": 5, "4v4": 4, "3v3": 3}

    def _set_rink_team_sizes(self, mode):
        """Put both teams at the size the field is played with.

        Through _set_team_count, which adds or removes players where they stand
        rather than rebuilding the roster, so choosing a field does not throw away
        an arrangement."""
        wanted = self.RINK_TEAM_SIZES.get(mode)
        if not wanted:
            return
        for team in ("att", "def"):
            try:
                if len(self._team_tokens(team)) != wanted:
                    self._set_team_count(team, wanted)
            except Exception:
                pass

    def cycle_rink_mode(self):
        """5v5 -> 4v4 -> 3v3 -> 5v5. One button, because the toolbar is two rows and
        a field apiece was not worth three quarters of one. The half rink is left
        alone: it is a view of whichever field this lands on."""
        order = self.RINK_ORDER
        self.set_rink_mode(order[(order.index(self.rink_mode) + 1) % len(order)])

    def _pitch_state(self):
        """The mapping currently on screen, captured so positions can be converted
        back out of pixels after the pitch is redrawn at a new scale/origin."""
        rink_len, rink_wid = self._rink_size()
        return {
            "scale": getattr(self, "pitch_scale", None),
            "ox": getattr(self, "pitch_ox", None),
            "oy": getattr(self, "pitch_oy", None),
            "rotated": self.rink_rotated,
            "rink_len": rink_len,
            "rink_wid": rink_wid,
        }

    @staticmethod
    def _state_px_to_m(px, py, st):
        if st["rotated"]:
            return (st["rink_len"] - (py - st["oy"]) / st["scale"],
                    (px - st["ox"]) / st["scale"])
        return ((px - st["ox"]) / st["scale"],
                (py - st["oy"]) / st["scale"])

    @staticmethod
    def _state_m_to_px(mx, my, st):
        if st["rotated"]:
            return (st["ox"] + my * st["scale"],
                    st["oy"] + (st["rink_len"] - mx) * st["scale"])
        return (st["ox"] + mx * st["scale"],
                st["oy"] + my * st["scale"])

    def _snapshot_rink_positions(self, st):
        """Record where every token and drawing sits in rink metres. Pixel positions
        are meaningless across a redraw because the scale, origin and orientation can
        all change; rink metres are what has to be preserved."""
        snap = {"tokens": {}, "drawn": {}}
        seen = set()
        for token in list(self.tokens.values()):
            sid = token.get("shape_id")
            if sid is None or sid in seen:
                continue
            seen.add(sid)
            # bbox, not coords: a token may be a polygon (triangle) or a multi-point
            # line (X, plus), where coords[0..3] is not the bounding box.
            bbox = self.canvas.bbox(sid)
            if not bbox:
                continue
            snap["tokens"][sid] = self._state_px_to_m((bbox[0] + bbox[2]) / 2,
                                                      (bbox[1] + bbox[3]) / 2, st)
        for cid in list(self.drawn_items.keys()):
            coords = self.canvas.coords(cid)
            if not coords:
                continue
            snap["drawn"][cid] = [self._state_px_to_m(coords[i], coords[i+1], st)
                                  for i in range(0, len(coords), 2)]
        return snap

    def _restore_rink_positions(self, snap, old_st):
        """Re-project a snapshot onto the pitch as it is drawn now."""
        st = self._pitch_state()
        if not st["scale"] or not old_st.get("scale"):
            return
        ratio = st["scale"] / old_st["scale"]
        rink_len, rink_wid = st["rink_len"], st.get("rink_wid", 20.0)

        seen = set()
        for token in list(self.tokens.values()):
            sid = token.get("shape_id")
            if sid is None or sid in seen or sid not in snap["tokens"]:
                continue
            seen.add(sid)
            mx, my = snap["tokens"][sid]
            # Switching between full and half rink shortens the long axis, so clamp
            # rather than leaving players stranded off the pitch.
            mx = min(max(mx, 0.0), rink_len)
            my = min(max(my, 0.0), rink_wid)
            nx, ny = self._state_m_to_px(mx, my, st)
            token["size"] = token.get("size", 14) * ratio
            try:
                # move + scale rather than rewriting coords as a 4-value bounding box,
                # which would collapse polygon and multi-line token shapes. Every item
                # the token is drawn from has to make the same trip: shifting only the
                # primary shape left the outline rings and the extra strokes of an X
                # behind, at the old position and the old size.
                bx1, by1, bx2, by2 = self.canvas.bbox(sid)
                dx, dy = nx - (bx1 + bx2) / 2, ny - (by1 + by2) / 2
                for item in self._token_items(token):
                    self.canvas.move(item, dx, dy)
                    if abs(ratio - 1.0) > 1e-9:
                        self.canvas.scale(item, nx, ny, ratio, ratio)
            except Exception:
                pass
            for tid, (ox_off, oy_off) in zip(token.get("text_ids", []), token.get("text_offsets", [])):
                try:
                    self.canvas.coords(tid, nx + ox_off, ny + oy_off)
                except Exception:
                    pass

        for cid, pts in snap["drawn"].items():
            new_coords = []
            for (mx, my) in pts:
                px, py = self._state_m_to_px(mx, my, st)
                new_coords.extend([px, py])
            try:
                self.canvas.coords(cid, *new_coords)
            except Exception:
                pass
        # The drawings now sit at new pixel coordinates; what the animation restores
        # them to has to follow, or the first Play after a resize would drag every
        # arrow back to where the window used to be.
        self._sync_full_coords()

    def _rink_to_px(self, mx, my):
        """Rink-space metres -> canvas pixels, matching _draw_pitch's m2px."""
        return self._state_m_to_px(mx, my, self._pitch_state())

    def _faceoff_point_px(self):
        """The centre spot. On a half rink the halfway line is the open edge at
        mx = 0, so the spot sits at the middle of that edge -- i.e. the centre of
        the halfway semicircle, not the middle of the visible board."""
        rink_len, rink_wid = self._rink_size()
        return self._rink_to_px(0.0 if self.half_rink else rink_len / 2.0,
                                rink_wid / 2.0)

    def _pitch_center_px(self):
        rink_len, rink_wid = self._rink_size()
        # When rotated the rink's long axis runs down the canvas, so the pixel spans
        # swap over -- using the landscape spans puts the centre off the pitch.
        span_x, span_y = (rink_wid, rink_len) if self.rink_rotated else (rink_len, rink_wid)
        return (self.pitch_ox + span_x * self.pitch_scale / 2,
                self.pitch_oy + span_y * self.pitch_scale / 2)

    # ----------------------
    # Fixtures of the rink
    # ----------------------
    PITCH_PART_NAMES = {
        "boards": "Boards",
        "centre_line": "Centre line",
        "centre_circle": "Centre circle",
        "goal_left": "Goal",
        "goal_right": "Goal",
    }

    def _part_tags(self, key):
        """Tags for one removable fixture of the rink.

        The name is carried in a tag rather than held as a canvas id because every
        redraw makes new items: after a resize the ids that were deleted would name
        nothing, while the name still finds whatever was drawn in their place."""
        return ("pitch", "pitch_part", f"part:{key}")

    def pitch_part_label(self, key):
        """What to call a fixture in a menu."""
        if str(key).startswith("faceoff_"):
            return "Face-off cross"
        return self.PITCH_PART_NAMES.get(key, "Rink feature")

    def _pitch_part_at(self, x, y, reach=4):
        """The fixture under the pointer, or None. Topmost first, so a cross sitting
        on the boards is picked before the boards are."""
        try:
            items = self.canvas.find_overlapping(x - reach, y - reach,
                                                 x + reach, y + reach)
        except Exception:
            return None
        for cid in reversed(items):
            for tag in self.canvas.gettags(cid):
                if tag.startswith("part:"):
                    key = tag[5:]
                    if key not in self.hidden_pitch_parts:
                        return key
        return None

    def _apply_hidden_pitch_parts(self):
        """Show every fixture, then take out the ones that were deleted."""
        try:
            self.canvas.itemconfigure("pitch_part", state=tk.NORMAL)
        except Exception:
            pass
        for key in self.hidden_pitch_parts:
            try:
                self.canvas.itemconfigure(f"part:{key}", state=tk.HIDDEN)
            except Exception:
                pass

    def _highlight_pitch_parts(self):
        """Mark selected fixtures with the same dashed box the rest of the selection
        gets. No resize handles: a goal is where the rules put it."""
        for key in self.selected_pitch_parts:
            box = self.canvas.bbox(f"part:{key}")
            if not box:
                continue
            rect = self.canvas.create_rectangle(box[0] - 3, box[1] - 3,
                                                box[2] + 3, box[3] + 3,
                                                outline=self.C_ACCENT, width=2,
                                                dash=(4, 3), tags=("selection_overlay",))
            self.selection_overlay_ids.append(rect)

    def restore_pitch_parts(self):
        """Put every deleted fixture of the rink back."""
        if not self.hidden_pitch_parts:
            return
        self.push_command(HidePitchPartsCommand(self, set(self.hidden_pitch_parts),
                                                hide=False))

    def _draw_pitch(self):
        self.canvas.delete("pitch")
        w, h = self.width, self.height
        is_half = self.half_rink

        rink_len, rink_wid = self._rink_size()
        marks = self._rink_markings()
        corner_r = marks["corner_r"]
        
        margin = 35
        avail_w = w - (margin * 2)
        avail_h = h - (margin * 2)
        
        # The rink is fitted to the canvas and then multiplied by the zoom, and stays
        # centred either way -- so zooming in enlarges what is in the middle of the
        # board rather than pushing the rink into a corner.
        zoom = self.zoom
        if self.rink_rotated:
            scale = min(avail_w / rink_wid, avail_h / rink_len) * zoom
            ox = (w - rink_wid * scale) / 2
            oy = (h - rink_len * scale) / 2
            def m2px(mx, my):
                return ox + my * scale, oy + (rink_len - mx) * scale
        else:
            scale = min(avail_w / rink_len, avail_h / rink_wid) * zoom
            ox = (w - rink_len * scale) / 2
            oy = (h - rink_wid * scale) / 2
            def m2px(mx, my):
                return ox + mx * scale, oy + my * scale
        # Scrolling shifts the origin. It is clamped to what is actually off-screen,
        # so the rink can never be scrolled away from under the pointer, and at 1x
        # there is nothing hidden and therefore nothing to scroll.
        self._clamp_pan(w, h, rink_len, rink_wid, scale)
        ox += self.pan_x
        oy += self.pan_y

        self.pitch_scale = scale
        self.pitch_ox = ox
        self.pitch_oy = oy

        hx1, hy1 = m2px(corner_r, 0)
        hx2, hy2 = m2px(rink_len - corner_r, rink_wid)
        self.canvas.create_rectangle(hx1, hy1, hx2, hy2, fill="#ffffff", outline="", tags=("pitch", "pitch_surface"))

        vx1, vy1 = m2px(0, corner_r)
        vx2, vy2 = m2px(rink_len, rink_wid - corner_r)
        self.canvas.create_rectangle(vx1, vy1, vx2, vy2, fill="#ffffff", outline="", tags=("pitch", "pitch_surface"))

        # The four rounded-corner boxes below are defined in rink (meter) space using
        # the fixed labels tl/tr/bl/br. When the rink is rotated 90 degrees, m2px()
        # maps each of these boxes to a *different* visual corner of the canvas, so
        # the arc's start angle has to rotate along with it (+90 deg per box) or the
        # rounded corner ends up drawn on the wrong side of its bounding box.
        corner_start = {"tl": 90, "tr": 0, "bl": 180, "br": 270}
        if self.rink_rotated:
            corner_start = {k: (v + 90) % 360 for k, v in corner_start.items()}

        tl_x1, tl_y1 = m2px(0, 0)
        tl_x2, tl_y2 = m2px(2*corner_r, 2*corner_r)
        self.canvas.create_arc(tl_x1, tl_y1, tl_x2, tl_y2, start=corner_start["tl"], extent=90, fill="#ffffff", outline="", tags=("pitch", "pitch_surface"))
        tr_x1, tr_y1 = m2px(rink_len - 2*corner_r, 0)
        tr_x2, tr_y2 = m2px(rink_len, 2*corner_r)
        self.canvas.create_arc(tr_x1, tr_y1, tr_x2, tr_y2, start=corner_start["tr"], extent=90, fill="#ffffff", outline="", tags=("pitch", "pitch_surface"))
        bl_x1, bl_y1 = m2px(0, rink_wid - 2*corner_r)
        bl_x2, bl_y2 = m2px(2*corner_r, rink_wid)
        self.canvas.create_arc(bl_x1, bl_y1, bl_x2, bl_y2, start=corner_start["bl"], extent=90, fill="#ffffff", outline="", tags=("pitch", "pitch_surface"))
        br_x1, br_y1 = m2px(rink_len - 2*corner_r, rink_wid - 2*corner_r)
        br_x2, br_y2 = m2px(rink_len, rink_wid)
        self.canvas.create_arc(br_x1, br_y1, br_x2, br_y2, start=corner_start["br"], extent=90, fill="#ffffff", outline="", tags=("pitch", "pitch_surface"))

        boards = self._part_tags("boards")
        lt1_x, lt1_y = m2px(corner_r, 0)
        lt2_x, lt2_y = m2px(rink_len - corner_r, 0)
        self.canvas.create_line(lt1_x, lt1_y, lt2_x, lt2_y, fill="#343a40", width=2.5, tags=boards)

        lb1_x, lb1_y = m2px(corner_r, rink_wid)
        lb2_x, lb2_y = m2px(rink_len - corner_r, rink_wid)
        self.canvas.create_line(lb1_x, lb1_y, lb2_x, lb2_y, fill="#343a40", width=2.5, tags=boards)

        ll1_x, ll1_y = m2px(0, corner_r)
        ll2_x, ll2_y = m2px(0, rink_wid - corner_r)
        self.canvas.create_line(ll1_x, ll1_y, ll2_x, ll2_y, fill="#343a40", width=2.5, tags=boards)

        lr1_x, lr1_y = m2px(rink_len, corner_r)
        lr2_x, lr2_y = m2px(rink_len, rink_wid - corner_r)
        self.canvas.create_line(lr1_x, lr1_y, lr2_x, lr2_y, fill="#343a40", width=2.5, tags=boards)

        self.canvas.create_arc(tl_x1, tl_y1, tl_x2, tl_y2, start=corner_start["tl"], extent=90, style=tk.ARC, outline="#343a40", width=2.5, tags=boards)
        self.canvas.create_arc(tr_x1, tr_y1, tr_x2, tr_y2, start=corner_start["tr"], extent=90, style=tk.ARC, outline="#343a40", width=2.5, tags=boards)
        self.canvas.create_arc(bl_x1, bl_y1, bl_x2, bl_y2, start=corner_start["bl"], extent=90, style=tk.ARC, outline="#343a40", width=2.5, tags=boards)
        self.canvas.create_arc(br_x1, br_y1, br_x2, br_y2, start=corner_start["br"], extent=90, style=tk.ARC, outline="#343a40", width=2.5, tags=boards)

        # The halfway line sits at the middle of whatever field is being drawn; a half
        # rink has none, because its open edge is the halfway line.
        halfway_mx = 0.0 if is_half else rink_len / 2.0
        mid_wid = rink_wid / 2.0
        if not is_half:
            # Both endpoints must go through m2px in full: when rotated, the x of a
            # pixel depends only on my and the y only on mx, so mixing components
            # from two different m2px calls collapses the line to zero length.
            cl1_x, cl1_y = m2px(halfway_mx, 0)
            cl2_x, cl2_y = m2px(halfway_mx, rink_wid)
            self.canvas.create_line(cl1_x, cl1_y, cl2_x, cl2_y, fill="#ced4da", width=2,
                                    tags=self._part_tags("centre_line"))

        # On a half rink the local mx axis spans only one half of the 40 m rink: the
        # goal end is at mx = rink_len and the halfway line is the open edge at
        # mx = 0. Placing the centre circle at mx = 20 (correct for a full rink) put
        # it on the goal end wall, overlapping the cage and goal areas.
        # The small fields have a centre point but no circle, so there is nothing to
        # draw here for them -- the centre mark is one of the face-off spots below.
        cc_px, cc_py = m2px(halfway_mx, mid_wid)
        if marks["circle"]:
            c_radius_px = marks["circle"] * scale
            if is_half:
                # The semicircle has to open into the rink. Rotating the rink turns the
                # whole mapping 90 deg, so the arc's start angle turns with it.
                self.canvas.create_arc(
                    cc_px - c_radius_px, cc_py - c_radius_px,
                    cc_px + c_radius_px, cc_py + c_radius_px,
                    start=(0 if self.rink_rotated else 270), extent=180,
                    outline="#ced4da", width=2, style=tk.ARC,
                    tags=self._part_tags("centre_circle")
                )
            else:
                self.canvas.create_oval(
                    cc_px - c_radius_px, cc_py - c_radius_px,
                    cc_px + c_radius_px, cc_py + c_radius_px,
                    outline="#ced4da", width=2, tags=self._part_tags("centre_circle")
                )

        goal_line_dist = marks["goal_line"]
        cage_depth = self.GOAL_DEPTH_M
        cage_width = self.GOAL_MOUTH_M

        def draw_goal_end(goal_line_x, is_left):
            # The cage, its goal line and the two goal areas are one part: they are
            # one piece of furniture, and picking them off separately would be
            # fiddlier than it is worth.
            goal = self._part_tags("goal_left" if is_left else "goal_right")
            # Take both endpoints straight from m2px; taking the x from one call and
            # the y from another only happens to work in the landscape mapping and
            # degenerates to a zero-length line once the rink is rotated.
            gl1_x, gl1_y = m2px(goal_line_x, mid_wid - (cage_width/2))
            gl2_x, gl2_y = m2px(goal_line_x, mid_wid + (cage_width/2))
            self.canvas.create_line(gl1_x, gl1_y, gl2_x, gl2_y, fill="#000000", width=2.5, tags=goal)

            # The cage is a solid black box. It is built as a polygon from four
            # rink-space corners rather than as an axis-aligned rectangle so it stays
            # correct at either end of the rink and in either orientation.
            back_x = goal_line_x - cage_depth if is_left else goal_line_x + cage_depth
            cage_y1 = mid_wid - (cage_width / 2)
            cage_y2 = mid_wid + (cage_width / 2)
            cage_pts = [
                m2px(goal_line_x, cage_y1),
                m2px(back_x, cage_y1),
                m2px(back_x, cage_y2),
                m2px(goal_line_x, cage_y2),
            ]
            flat = [c for pt in cage_pts for c in pt]
            self.canvas.create_polygon(*flat, fill="#000000", outline="#000000", width=1, tags=goal)

            # One box on the small fields (the goalkeeper area), two on the large one
            # (the goal crease and the goal area around it).
            for depth, width in marks["areas"]:
                area_x1 = goal_line_x if is_left else goal_line_x - depth
                area_x2 = goal_line_x + depth if is_left else goal_line_x
                ax1, ay1 = m2px(area_x1, mid_wid - (width/2))
                ax2, ay2 = m2px(area_x2, mid_wid + (width/2))
                self.canvas.create_rectangle(ax1, ay1, ax2, ay2, outline="#ced4da",
                                             width=2, tags=goal)

        if self.goals_visible_var.get():
            if is_half:
                draw_goal_end(rink_len - goal_line_dist, False)
            else:
                draw_goal_end(goal_line_dist, True)
                draw_goal_end(rink_len - goal_line_dist, False)

        # Face-off crosses: the corner spots plus the ones on the halfway line,
        # including the centre spot. How far they sit in from the long sides differs
        # per field -- 2.85 m on the large rink, 2 m on the 3v3 field, 1 m on the
        # small one -- while along the rink they line up with the goal lines. On a
        # half rink the halfway line is the open edge at mx = 0 and only one end
        # exists.
        cross_arm = 0.25
        inset = marks["faceoff"]
        if is_half:
            halfway_x, end_xs = 0.0, [rink_len - goal_line_dist]
        else:
            halfway_x = rink_len / 2.0
            end_xs = [goal_line_dist, rink_len - goal_line_dist]

        faceoff_spots = []
        for end_x in end_xs:
            faceoff_spots.append((end_x, inset))
            faceoff_spots.append((end_x, rink_wid - inset))
        faceoff_spots.append((halfway_x, inset))
        faceoff_spots.append((halfway_x, rink_wid - inset))
        faceoff_spots.append((halfway_x, mid_wid))

        # The small fields put a penalty spot in front of each goal instead of the
        # large rink's extra face-off circles.
        if marks["penalty"]:
            for goal_x, inward in ((goal_line_dist, 1.0),
                                   (rink_len - goal_line_dist, -1.0)):
                faceoff_spots.append((goal_x + inward * marks["penalty"], mid_wid))

        for spot_x, spot_y in faceoff_spots:
            # Diagonal arms: a face-off mark is an X, not a +.
            nw = m2px(spot_x - cross_arm, spot_y - cross_arm)
            se = m2px(spot_x + cross_arm, spot_y + cross_arm)
            sw = m2px(spot_x - cross_arm, spot_y + cross_arm)
            ne = m2px(spot_x + cross_arm, spot_y - cross_arm)
            cross = self._part_tags(f"faceoff_{spot_x:.1f}_{spot_y:.1f}")
            self.canvas.create_line(*nw, *se, fill="#000000", width=2, tags=cross)
            self.canvas.create_line(*sw, *ne, fill="#000000", width=2, tags=cross)

        # Substitution zones: a marked stretch of one long side, one per team, either
        # side of the halfway line. Drawn on the boards themselves, which is where the
        # tape goes.
        if marks["sub_zone"] and not is_half:
            start, length = marks["sub_zone"]
            zones = self._part_tags("sub_zones")
            for direction in (-1.0, 1.0):
                near = halfway_x + direction * start
                far = near + direction * length
                z1_x, z1_y = m2px(min(near, far), rink_wid)
                z2_x, z2_y = m2px(max(near, far), rink_wid)
                self.canvas.create_line(z1_x, z1_y, z2_x, z2_y, fill="#f59f00",
                                        width=5, tags=zones)

        self.draw_grid_points()

        # The rink has just been rebuilt, so anything the user removed has to be
        # taken back out of it.
        self._apply_hidden_pitch_parts()
        self.canvas.tag_lower("pitch")
        self._draw_credit()
        self._render_watermark()

    def _draw_credit(self):
        """The authorship line in the bottom-left corner of the board.

        Drawn on the canvas rather than packed into the chrome so it comes along with
        an exported frame, and left out of drawn_items so it cannot be selected,
        dragged or deleted with the rest of the board."""
        self.canvas.delete("credit")
        self.canvas.create_text(
            10, max(0, self.height - 8), text=self.CREDIT_TEXT, anchor="sw",
            fill="#adb5bd", font=(self.UI_FONT, 8), tags=("credit",))

if __name__ == "__main__":
    root = tk.Tk()
    app = FloorballTacticsApp(root)
    root.mainloop()
