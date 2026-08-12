# floorball_animator.py
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, colorchooser
from tkinter import font as tkfont
from PIL import Image, ImageTk, ImageDraw, ImageChops
import math
import os
import io
import base64
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
    def __init__(self, app, label_moves):
        self.app = app
        self.label_moves = label_moves

    def execute(self):
        for label, (dx, dy) in self.label_moves.items():
            sid = self.app._get_sid_by_label(label)
            if sid:
                self._move(sid, dx, dy)

    def undo(self):
        for label, (dx, dy) in self.label_moves.items():
            sid = self.app._get_sid_by_label(label)
            if sid:
                self._move(sid, -dx, -dy)

    def _move(self, sid, dx, dy):
        token = self.app.tokens.get(sid)
        if token:
            for item in self.app._token_items(token):
                self.app.canvas.move(item, dx, dy)
            if "text_ids" in token:
                for tid in token["text_ids"]:
                    self.app.canvas.move(tid, dx, dy)
            for line_id in token.get("attached_lines_start", []):
                coords = self.app.canvas.coords(line_id)
                if coords and len(coords) >= 4:
                    coords[0] += dx
                    coords[1] += dy
                    self.app.canvas.coords(line_id, *coords)
            for line_id in token.get("attached_lines_end", []):
                coords = self.app.canvas.coords(line_id)
                if coords and len(coords) >= 4:
                    coords[-2] += dx
                    coords[-1] += dy
                    self.app.canvas.coords(line_id, *coords)

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
        # Composed rather than subclassed so the movement keeps following attached
        # tactic lines exactly as a hand-drag would.
        self.move = MoveTokensCommand(app, label_moves)
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
        # after the toolbar is built -- the Full/Half button relabels itself.
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
        self.step_desc = f"{tool.capitalize()} ({int(x1)},{int(y1)} ➔ {int(x2)},{int(y2)})"
        self.drawing_data = {"tool": self.tool, "x1": self.x1, "y1": self.y1, "x2": self.x2, "y2": self.y2, "extra": self.extra_data}

    def execute(self):
        self.line_ids = self.app.draw_tactical_line_canvas(self.tool, self.x1, self.y1, self.x2, self.y2, preview=False, extra_data=self.extra_data)
        # register drawn items in app.drawn_items
        for lid in self.line_ids:
            self.app.drawn_items[lid] = {"type": "tactic_line", "tool": self.tool, "data": self.drawing_data, "color": self.app.line_color}
        self.app.drawings.append((self.line_ids, self.drawing_data))
        self.app.action_steps.append(self.step_desc)
        try:
            self.app.steps_listbox.insert(tk.END, self.step_desc)
        except Exception:
            pass

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
        self.root.geometry("1300x850")
        # The toolbar is pinned to two rows, so it cannot shed a row to cope with a
        # narrow window: below this width Tactics gets squeezed to a sliver and then
        # drops out entirely. Refuse the sizes that would break it.
        self.root.minsize(1240, 700)
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
        self.selection_rect = None
        self.selection_start = None

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
        self.half_rink_var = tk.BooleanVar(value=True)
        self.snap_player_var = tk.BooleanVar(value=True)
        self.snap_angle_var = tk.BooleanVar(value=False)
        self.ghosting_var = tk.BooleanVar(value=False)
        self.curved_arches_var = tk.BooleanVar(value=False)
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
            ("<BackSpace>", self._delete_key),
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
                "grid": self.grid_var.get(),
                "snap_player": self.snap_player_var.get(),
                "snap_angle": self.snap_angle_var.get(),
                "ghosting": self.ghosting_var.get(),
                "dont_bother_again": self.dont_bother_again,
                "menu_two_rows": self.menu_two_rows,
                "menu_rows_mode": self.menu_rows_mode,
                "menu_position": self.menu_position,
                "rink_rotated": self.rink_rotated
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
        self.undo_stack.append(cmd)
        self.redo_stack.clear()
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
        """Apply the current sign colour to any already-placed signs in the selection,
        so the picker can be used to fix a sign after the fact and not only before."""
        for cid in list(self.selected_drawn):
            meta = self.drawn_items.get(cid)
            if not meta or meta.get("type") != "sign":
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
            try:
                self.btn_att_color.config(bg=self.att_color)
            except Exception:
                pass
            self._update_roster()

    def choose_def_color(self):
        color_code = colorchooser.askcolor(title="Choose Defense Team Color", color=self.def_color)[1]
        if color_code:
            self.def_color = color_code
            try:
                self.btn_def_color.config(bg=self.def_color)
            except Exception:
                pass
            self._update_roster()

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
                bbox = self.canvas.bbox(sid)
                if not bbox:
                    continue
                mx, my = self._state_px_to_m((bbox[0] + bbox[2]) / 2,
                                             (bbox[1] + bbox[3]) / 2, state)
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
            "rink_rotated": bool(self.rink_rotated),
            "players": players,
            "watermark": self._watermark_snapshot(),
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

    def _restore_board(self, board):
        players = [p for p in (board.get("players") or []) if p.get("label")]
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
        if bool(self.half_rink_var.get()) != bool(board.get("half_rink", self.half_rink_var.get())):
            self.half_rink_var.set(bool(board.get("half_rink")))
            self._update_indicators()
            self.redraw_canvas()
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
            bbox = self.canvas.bbox(token.get("shape_id"))
            if not bbox:
                continue
            nx, ny = self._rink_to_px(entry.get("mx", 0.0), entry.get("my", 0.0))
            dx = nx - (bbox[0] + bbox[2]) / 2
            dy = ny - (bbox[1] + bbox[3]) / 2
            for item in self._token_items(token) + list(token.get("text_ids", [])):
                try:
                    self.canvas.move(item, dx, dy)
                except Exception:
                    pass
            self._set_token_position(token, entry.get("position"))

    def save_macro(self):
        if not self.undo_stack and not self.tokens:
            messagebox.showinfo("Empty", "Nothing on the board to save.")
            return
        filepath = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON Files", "*.json")])
        if filepath:
            commands = [cmd.serialize() for cmd in self.undo_stack if hasattr(cmd, "serialize")]
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
            data = {"version": 2, "commands": commands, "board": board}
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

            # Version 1 files were a bare list of commands; version 2 adds the board.
            if isinstance(data, dict):
                commands, board = data.get("commands", []), data.get("board")
            else:
                commands, board = data, None

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

            # Applied last so the recorded positions win over anything the replayed
            # commands did with their pixel deltas.
            if board:
                self._restore_board(board)

        except Exception as e:
            messagebox.showerror("Error", f"Failed to load macro:\n{e}")

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
        elif st_lower == "dot" or st_lower == "circle":
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
            var.set(not var.get())
            self._update_indicators()
            if name == "half_rink" or name == "goals":
                self.redraw_canvas()
            elif name == "grid":
                self.toggle_grid_visuals()

        settings_list = [
            ("Half", self.half_rink_var, "half_rink"),
            ("Arches", self.curved_arches_var, "arches"),
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

        # Eight toggles fill a 2x4 grid exactly: no spans, no empty cells, and the
        # uniform equal-weight columns give every button the same length.
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

        att_row = tk.Frame(roster_frame, bg=self.C_PANEL)
        att_row.pack(fill=tk.X, pady=2)
        tk.Label(att_row, text="Atk:", bg=self.C_PANEL, font=(self.UI_FONT, 8, "bold"), width=3, anchor="w").pack(side=tk.LEFT)
        self.att_spinbox = tk.Spinbox(att_row, from_=1, to=10, width=2, command=self._update_roster, font=(self.UI_FONT, 8))
        self.att_spinbox.delete(0, tk.END)
        self.att_spinbox.insert(0, "5")
        self.att_spinbox.pack(side=tk.LEFT, padx=1)
        self.att_shape_var = tk.StringVar(value="Square")
        ttk.Combobox(att_row, textvariable=self.att_shape_var, values=["Square", "Circle", "X", "Triangle", "Plus"], width=7, font=(self.UI_FONT, 8), style="Toolbar.TCombobox").pack(side=tk.LEFT, padx=1, expand=True, fill=tk.X)
        self.btn_att_color = tk.Button(att_row, text="", command=self.choose_att_color, **swatch_cfg)
        self.btn_att_color.config(bg=self.att_color)
        self.btn_att_color._is_swatch = True
        self.btn_att_color.pack(side=tk.LEFT, padx=1)
        tk.Label(att_row, text="Size:", bg=self.C_PANEL, font=(self.UI_FONT, 8, "bold"), anchor="w").pack(side=tk.LEFT, padx=(6, 0))
        self.player_size_spinbox = tk.Spinbox(att_row, from_=6, to=60, width=3, textvariable=self.player_size_var, command=self._resize_selected_players, font=(self.UI_FONT, 8))
        self.player_size_spinbox.pack(side=tk.LEFT, padx=1)
        # `command` only fires for the little arrows. Typing a number and pressing
        # Return, or clicking away, has to work as well.
        self.player_size_spinbox.bind("<Return>", self._resize_selected_players)
        self.player_size_spinbox.bind("<FocusOut>", self._resize_selected_players)

        def_row = tk.Frame(roster_frame, bg=self.C_PANEL)
        def_row.pack(fill=tk.X, pady=2)
        tk.Label(def_row, text="Def:", bg=self.C_PANEL, font=(self.UI_FONT, 8, "bold"), width=3, anchor="w").pack(side=tk.LEFT)
        self.def_spinbox = tk.Spinbox(def_row, from_=1, to=10, width=2, command=self._update_roster, font=(self.UI_FONT, 8))
        self.def_spinbox.delete(0, tk.END)
        self.def_spinbox.insert(0, "5")
        self.def_spinbox.pack(side=tk.LEFT, padx=1)
        self.def_shape_var = tk.StringVar(value="Circle")
        ttk.Combobox(def_row, textvariable=self.def_shape_var, values=["Square", "Circle", "X", "Triangle", "Plus"], width=7, font=(self.UI_FONT, 8), style="Toolbar.TCombobox").pack(side=tk.LEFT, padx=1, expand=True, fill=tk.X)
        self.btn_def_color = tk.Button(def_row, text="", command=self.choose_def_color, **swatch_cfg)
        self.btn_def_color.config(bg=self.def_color)
        self.btn_def_color._is_swatch = True
        self.btn_def_color.pack(side=tk.LEFT, padx=1)


        signs_frame = ttk.LabelFrame(self.top_inner, style="Toolbar.TLabelframe", text=" Signs ", padding=5)
        signs_frame.pack(side=tk.LEFT, padx=3, pady=2, fill=tk.Y)
        self._signs_frame = signs_frame
        sign_size_row = tk.Frame(signs_frame, bg=self.C_PANEL)
        sign_size_row.pack(fill=tk.X, pady=2)
        tk.Label(sign_size_row, text="Size:", bg=self.C_PANEL, font=(self.UI_FONT, 8, "bold"), width=3, anchor="w").pack(side=tk.LEFT)
        self.sign_size_spinbox = tk.Spinbox(sign_size_row, from_=6, to=60, width=3, textvariable=self.sign_size_var, font=(self.UI_FONT, 8))
        self.sign_size_spinbox.pack(side=tk.LEFT, padx=1)
        self.btn_sign_color = tk.Button(sign_size_row, text="", command=self.choose_sign_color, **swatch_cfg)
        self.btn_sign_color.config(bg=self.sign_color)
        self.btn_sign_color._is_swatch = True
        self.btn_sign_color.pack(side=tk.LEFT, padx=1)
        sign_row2 = tk.Frame(signs_frame, bg=self.C_PANEL)
        sign_row2.pack(fill=tk.X, pady=2)

        # Two rows: the first three signs sit beside the size/colour controls.
        sign_row3 = tk.Frame(signs_frame, bg=self.C_PANEL)
        sign_row3.pack(fill=tk.X, pady=2)
        for idx, stype in enumerate(["Goal", "X", "Ball", "Square", "Triangle", "Plus"]):
            parent = sign_row2 if idx < 3 else sign_row3
            btn = tk.Button(parent, text=stype, command=lambda t=stype: self.set_tool(f"sign_{t.lower()}"), **gray_btn_cfg)
            btn.pack(side=tk.LEFT, padx=3, expand=True, fill=tk.X)
            self.tool_buttons[f"sign_{stype.lower()}"] = btn

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

        # Timeline is now only the step list, so it can stay narrow while spanning
        # both toolbar rows. Its buttons moved out into General.
        timeline_frame = ttk.LabelFrame(self.top_inner, style="Toolbar.TLabelframe", text=" Timeline ", padding=5)
        timeline_frame.pack(side=tk.LEFT, padx=3, pady=2, fill=tk.Y)
        self._timeline_frame = timeline_frame

        t_sub = tk.Frame(timeline_frame, bg=self.C_PANEL)
        t_sub.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Roomier than the other sections because it spans both toolbar rows.
        self.steps_listbox = tk.Listbox(t_sub, font=(self.UI_FONT, 8), selectmode=tk.SINGLE,
                                        height=8, width=18, relief=tk.FLAT, bd=0,
                                        highlightthickness=1, highlightbackground=self.C_BORDER,
                                        bg=self.C_SURFACE, fg=self.C_TEXT,
                                        selectbackground=self.C_ACCENT, selectforeground=self.C_ACCENT_FG)
        self.steps_listbox.pack(side=tk.LEFT, padx=2, fill=tk.BOTH, expand=True)

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
        tk.Button(g_grid, text="Load", command=self.load_macro, **general_btn_cfg).grid(row=1, column=0, padx=3, pady=2, sticky="ew")
        tk.Button(g_grid, text="Watermark", command=self.add_watermark, **general_btn_cfg).grid(row=1, column=1, padx=3, pady=2, sticky="ew")
        tk.Button(g_grid, text="Prefs", command=self.open_preferences, **general_btn_cfg).grid(row=1, column=2, padx=3, pady=2, sticky="ew")
        for col in range(3):
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
        # Board settings
        "Full": "Switch to the half rink.",
        "Half": "Switch back to the full rink.",
        "Arches": "Show or hide the rounded corner arches.",
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
        # Drawing tools
        "Select": "Select and move players, signs and lines. Drag to box-select.",
        "Pass": "Draw a pass: a straight arrow.",
        "Shot": "Draw a shot.",
        "Dribble": "Draw a dribble: a wavy run with the ball.",
        "Run": "Draw a run without the ball.",
        "Line": "Draw a plain straight line.",
        "Bend": "Draw a curve: click the start, the bend, then the end.",
        "Box": "Draw a filled box.",
        "Rect": "Draw a rectangle outline.",
        "Circle": "Draw a circle.",
        "Oval": "Draw an oval.",
        "Rotate Sel": "Turn the selection 45 degrees, players and signs alike.",
        "Copy Style": "Copy the colour and shape of one player, then click others to "
                      "paste it.",
        "Default": "Save the current colours and sizes as the defaults for new boards.",
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
                    if label in ("Full", "Half"):
                        # This one relabels itself as the rink is toggled, so the
                        # caption is looked up when it is shown, not when it is bound.
                        Tooltip(child, lambda w=child: self.BUTTON_TOOLTIPS.get(w.cget("text")),
                                font=(self.UI_FONT, 8))
                    elif self.BUTTON_TOOLTIPS.get(label):
                        Tooltip(child, self.BUTTON_TOOLTIPS[label], font=(self.UI_FONT, 8))
                walk(child)

        walk(container if container is not None else self.top_bar)

    def _update_indicators(self):
        for name, btn in self.tool_buttons.items():
            try:
                if name == self.active_tool:
                    btn.config(bg=self.C_ACCENT, fg=self.C_ACCENT_FG)
                else:
                    btn.config(bg=self.C_BTN, fg=self.C_TEXT)
            except Exception:
                pass

        for name, btn in self.setting_buttons.items():
            var_map = {
                "half_rink": self.half_rink_var,
                "arches": self.curved_arches_var,
                "goals": self.goals_visible_var,
                "snap_player": self.snap_player_var,
                "snap_angle": self.snap_angle_var,
                "grid": self.grid_var,
                "ghosting": self.ghosting_var
            }
            is_active = bool(name in var_map and var_map[name].get())
            if name == "half_rink":
                try:
                    btn.config(text="Half" if self.half_rink_var.get() else "Full")
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
    # Timeline comes first so it sits on the left whether it is spanning beside the
    # rows or has fallen back into one of them; General follows it.
    MENU_SECTION_ATTRS = ("_timeline_frame", "_general_frame", "_snapping_frame",
                          "_roster_frame", "_tactics_frame", "_align_frame",
                          "_signs_frame", "_actions_frame")

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
            # trailing off into empty panel.
            spanning.pack(in_=self.top_inner, side=tk.LEFT, padx=4, pady=3,
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
    def open_preferences(self):
        win = tk.Toplevel(self.root)
        win.title("Preferences")
        win.transient(self.root)
        win.resizable(False, False)

        def heading(text, row):
            tk.Label(win, text=text, font=(self.UI_FONT, 9, "bold")).grid(
                row=row, column=0, columnspan=2, sticky="w", padx=10, pady=(10, 2))
            return row + 1

        row = heading("Board", 0)
        for text, var in (("Half rink", self.half_rink_var),
                          ("Curved arches", self.curved_arches_var),
                          ("Show goals", self.goals_visible_var),
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

        tk.Button(btns, text="Save & Close", command=apply_and_close, width=self.BTN_W).pack(side=tk.LEFT, padx=6)
        tk.Button(btns, text="Cancel", command=win.destroy, width=self.BTN_W).pack(side=tk.LEFT, padx=6)

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
                sid = token["shape_id"]
                # bbox, not coords: a square player is a polygon and a cross is a
                # line, so coords() is 8 or 4 numbers and unpacking it as a box
                # raised ValueError -- snapping simply died on those shapes.
                box = self.canvas.bbox(sid)
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
            box = self.canvas.bbox(sid)
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
        for sid in sids:
            token = self.tokens.get(sid)
            if not token: continue
            coords = self.canvas.coords(token["shape_id"])
            if not coords: continue
            
            token["ghost_count"] = token.get("ghost_count", 0) + 1
            count_idx = token["ghost_count"]
            
            cx, cy = (coords[0] + coords[2]) / 2, (coords[1] + coords[3]) / 2
            label_str = f"{token['label']} [{count_idx}]"
            
            ghost_sid = self._create_token(cx, cy, label_str, shape=token["shape"], color=token["color"], outline="#ced4da", stipple="gray50")
            self.tokens[ghost_sid]["is_ghost"] = True
            self.tokens[ghost_sid]["ghost_of"] = token["shape_id"]

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
            for iid in [ghost_sid] + list(ghost_token.get("text_ids", [])):
                self.canvas.addtag_withtag(ghost_tag, iid)

            # Raise the whole ghost group as one call so its internal
            # stacking order (white text on top of the black outline
            # copies) is preserved, while still placing it just above the
            # pitch and below any "real" (non-ghost) tokens.
            self.canvas.tag_raise(ghost_tag, "pitch")

    def place_sign_canvas(self, x, y, sign_type, size=None):
        color = self.sign_color
        created = []
        sign_lower = sign_type.lower()
        # Signs made of several items (the ball, the X, the plus) get a shared tag so
        # they can be moved or re-snapped as one thing later.
        self._sign_group_seq = getattr(self, "_sign_group_seq", 0) + 1
        group_tag = f"signgrp{self._sign_group_seq}"
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
            # A floorball, not a plain dot: the body plus the ring of holes that makes
            # it read as a ball at a glance. ("dot"/"circle" still land here so macros
            # saved before the rename keep working.)
            id1 = self.canvas.create_oval(x-s, y-s, x+s, y+s, fill=color, outline=color, tags=("sign",))
            created.append(id1)
            hole_r = max(1.0, s * 0.22)
            for angle in (90, 210, 330):
                hx = x + math.cos(math.radians(angle)) * s * 0.45
                hy = y - math.sin(math.radians(angle)) * s * 0.45
                created.append(self.canvas.create_oval(
                    hx - hole_r, hy - hole_r, hx + hole_r, hy + hole_r,
                    fill=self.C_SURFACE, outline=self.C_SURFACE, tags=("sign",)))
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
                                            "group": group_tag})
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
            if self._is_line_selection() and handle_type in {"line_mid", "line_start", "line_end"}:
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
            if self.ghosting_var.get(): self.create_ghosts(self.selected_tokens)
            
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
            if ctrl:
                if clicked_drawn in self.selected_drawn:
                    self.selected_drawn.remove(clicked_drawn)
                else:
                    self.selected_drawn.add(clicked_drawn)
            else:
                self.selected_drawn = {clicked_drawn}
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
            self.selection_start = (event.x, event.y)
            self.selection_rect = self.canvas.create_rectangle(event.x, event.y, event.x, event.y, dash=(4,4), outline="#228be6")
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
            dx = event.x - self.resize_start[0] if self.resize_start else 0
            dy = event.y - self.resize_start[1] if self.resize_start else 0
            if abs(dx) < 8 and abs(dy) < 8:
                drag_x, drag_y = self.resize_start
            else:
                drag_x = self.resize_start[0] + dx * 0.12 if self.resize_start else event.x
                drag_y = self.resize_start[1] + dy * 0.12 if self.resize_start else event.y
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
                for line_id in token.get("attached_lines_start", []):
                    coords = self.canvas.coords(line_id)
                    if coords and len(coords) >= 4:
                        coords[0] += dx; coords[1] += dy
                        self.canvas.coords(line_id, *coords)
                for line_id in token.get("attached_lines_end", []):
                    coords = self.canvas.coords(line_id)
                    if coords and len(coords) >= 4:
                        coords[-2] += dx; coords[-1] += dy
                        self.canvas.coords(line_id, *coords)

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
                                for line_id in token.get("attached_lines_start", []):
                                    coords = self.canvas.coords(line_id)
                                    if coords and len(coords) >= 4:
                                        coords[0] += dx
                                        coords[1] += dy
                                        self.canvas.coords(line_id, *coords)
                                for line_id in token.get("attached_lines_end", []):
                                    coords = self.canvas.coords(line_id)
                                    if coords and len(coords) >= 4:
                                        coords[-2] += dx
                                        coords[-1] += dy
                                        self.canvas.coords(line_id, *coords)

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
                cmd = MoveTokensCommand(self, label_moves)
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
            for token in self.tokens.values():
                sid = token["shape_id"]
                coords = self.canvas.coords(sid)
                if coords:
                    cx, cy = (coords[0] + coords[2]) / 2, (coords[1] + coords[3]) / 2
                    if xmin <= cx <= xmax and ymin <= cy <= ymax:
                        self.selected_tokens.append(sid)
            for cid, meta in self.drawn_items.items():
                coords = self.canvas.coords(cid)
                if not coords:
                    continue
                cx = sum(coords[0::2]) / (len(coords[0::2]) or 1)
                cy = sum(coords[1::2]) / (len(coords[1::2]) or 1)
                if xmin <= cx <= xmax and ymin <= cy <= ymax:
                    self.selected_drawn.add(cid)
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

    def _get_resize_scale(self, current_size, original_size):
        if original_size <= 0:
            return 1.0
        ratio = current_size / original_size
        damped_ratio = 1.0 + (ratio - 1.0) * 0.1
        return max(0.85, min(1.15, damped_ratio))

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
            for line_id in token.get("attached_lines_start", []):
                coords = self.canvas.coords(line_id)
                if coords and len(coords) >= 4:
                    coords[0] += dx
                    coords[1] += dy
                    self.canvas.coords(line_id, *coords)
            for line_id in token.get("attached_lines_end", []):
                coords = self.canvas.coords(line_id)
                if coords and len(coords) >= 4:
                    coords[-2] += dx
                    coords[-1] += dy
                    self.canvas.coords(line_id, *coords)
            token["starting_pos"] = (new_cx, new_cy)

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
            try:
                if cid in self.selected_drawn:
                    self.canvas.itemconfig(cid, fill="#228be6", outline="#228be6")
                else:
                    meta = self.drawn_items.get(cid, {})
                    color = meta.get("color", self.line_color)
                    self.canvas.itemconfig(cid, fill=color, outline=color)
            except Exception:
                pass
        self._draw_selection_overlay()

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
            if self.curved_arches_var.get():
                dx_off, dy_off = -(y2 - y1) * 0.15, (x2 - x1) * 0.15
                lid1 = self.canvas.create_line(x1, y1, cx + dx_off, cy + dy_off, x2, y2, smooth=True, fill=color, width=width, dash=dash_tuple, tags=("tactic_line",))
                lid2 = self.canvas.create_line(x1, y1, cx - dx_off, cy - dy_off, x2, y2, smooth=True, fill=color, width=width, dash=dash_tuple, arrow=tk.LAST, arrowshape=big_arrow, tags=("tactic_line",))
                created_ids.extend([lid1, lid2])
            else:
                lid = self.canvas.create_line(x1, y1, cx, cy, x2, y2, smooth=True, fill=color, width=width, dash=dash_tuple, arrow=tk.LAST, arrowshape=big_arrow, tags=("tactic_line",))
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
            for cid in created_ids:
                self.drawn_items[cid] = {"type": "tactic_line", "tool": tool, "color": color}
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
        "House":    [("LD", 0.30, 0.00), ("RD", 0.70, 0.00), ("LW", 0.15, 0.55),
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
                    spot_x, spot_y = self._rink_to_px(
                        (20.0 if self.half_rink_var.get() else 40.0) * (0.3 if team == "att" else 0.7),
                        10.0)
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

        rink_len = 20.0 if self.half_rink_var.get() else 40.0
        rink_wid = 20.0
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
                "grid": self.grid_var.get(),
                "snap_player": self.snap_player_var.get(),
                "snap_angle": self.snap_angle_var.get(),
                "ghosting": self.ghosting_var.get(),
                "dont_bother_again": self.dont_bother_again,
                "menu_two_rows": self.menu_two_rows,
                "menu_rows_mode": self.menu_rows_mode,
                "menu_position": self.menu_position,
                "rink_rotated": self.rink_rotated
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
        rink_len = 20.0 if self.half_rink_var.get() else 40.0
        w_m = min(self.WATERMARK_DEFAULT_W_M, rink_len * 0.6)
        h_m = w_m * image.height / image.width
        if h_m > 16.0:                      # keep the first placement inside the boards
            h_m = 16.0
            w_m = h_m * image.width / image.height
        self.watermark = {"path": path, "original": image, "crop": None,
                          "bg_tolerance": None, "bg_mode": None, "behind": True,
                          "opacity": 100,
                          "mx": rink_len / 2.0, "my": 10.0, "w_m": w_m, "h_m": h_m}
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

    def _preview_pitch_state(self, rink_len, long_px=720, short_px=430, pad=18):
        """Mapping for the placement preview: the same rink-metre projection the board
        itself uses, so a position picked in the preview lands identically on the rink."""
        scale = min(long_px / rink_len, short_px / 20.0)
        st = {"scale": scale, "ox": pad, "oy": pad,
              "rotated": self.rink_rotated, "rink_len": rink_len}
        span_x, span_y = (20.0, rink_len) if self.rink_rotated else (rink_len, 20.0)
        return st, int(span_x * scale) + 2 * pad, int(span_y * scale) + 2 * pad

    def _draw_preview_rink(self, canvas, st, rink_len):
        """A stripped-down rink for the placement preview: boards, centre spot and
        circle, goal areas and goals. Enough to judge where a logo sits."""
        def p(mx, my):
            return self._state_m_to_px(mx, my, st)

        canvas.create_rectangle(*p(0, 0), *p(rink_len, 20), fill="#ffffff",
                                outline="#343a40", width=2)
        centre_mx = 0.0 if rink_len <= 20.0 else rink_len / 2.0
        if rink_len > 20.0:
            canvas.create_line(*p(centre_mx, 0), *p(centre_mx, 20), fill="#ced4da", width=2)
        canvas.create_oval(*p(centre_mx - 1.5, 8.5), *p(centre_mx + 1.5, 11.5),
                           outline="#ced4da", width=2)
        canvas.create_oval(*p(centre_mx - 0.2, 9.8), *p(centre_mx + 0.2, 10.2),
                           fill="#343a40", outline="#343a40")
        for goal_mx, inward in ((2.85, 1.0), (rink_len - 2.85, -1.0)):
            if rink_len <= 20.0 and inward > 0:
                continue                      # the half rink only has the far goal
            canvas.create_rectangle(*p(goal_mx, 6.0), *p(goal_mx + inward * 5.0, 14.0),
                                    outline="#ced4da", width=2)
            canvas.create_rectangle(*p(goal_mx - inward * 0.4, 9.0),
                                    *p(goal_mx, 11.0),
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
        rink_len = 20.0 if self.half_rink_var.get() else 40.0
        st, cv_w, cv_h = self._preview_pitch_state(rink_len)

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
            self._draw_preview_rink(cv, st, rink_len)
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
                state["my"] = min(max(gy + drag["grab"][1], 0.0), 20.0)
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
        win.grab_set()

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

    def delete_selection(self, event=None):
        """Remove everything selected: players, signs and drawn lines alike.

        Delete used to be bound to nothing at all, and the deletion inside Cut removed
        only a token's main shape, leaving its outline rings and the extra strokes of
        an X orphaned on the canvas."""
        removed = 0
        seen = set()
        for sid in list(self.selected_tokens):
            token = self.tokens.get(sid)
            if not token or token.get("locked", False):
                continue
            key = token.get("shape_id")
            if key in seen:
                continue
            seen.add(key)
            self._delete_token(token)
            removed += 1
        for cid in self._selected_drawn_ids():
            try:
                self.canvas.delete(cid)
            except Exception:
                pass
            self.drawn_items.pop(cid, None)
            removed += 1
        self.selected_drawn.clear()
        self.selected_tokens = [s for s in self.selected_tokens if s in self.tokens]
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
                    self.selected_drawn = {cid}
                    self.highlight_selected()
                return "drawn"
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
        else:
            menu.add_command(label="Paste", command=self.paste_clipboard,
                             state=tk.NORMAL if self.clipboard else tk.DISABLED)
            menu.add_command(label="Select All", command=self.select_all)
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
        self.selected_tokens.clear()
        self.selected_drawn.clear()
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

    def _pitch_state(self):
        """The mapping currently on screen, captured so positions can be converted
        back out of pixels after the pitch is redrawn at a new scale/origin."""
        return {
            "scale": getattr(self, "pitch_scale", None),
            "ox": getattr(self, "pitch_ox", None),
            "oy": getattr(self, "pitch_oy", None),
            "rotated": self.rink_rotated,
            "rink_len": 20.0 if self.half_rink_var.get() else 40.0,
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
        rink_len, rink_wid = st["rink_len"], 20.0

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

    def _rink_to_px(self, mx, my):
        """Rink-space metres -> canvas pixels, matching _draw_pitch's m2px."""
        return self._state_m_to_px(mx, my, self._pitch_state())

    def _faceoff_point_px(self):
        """The centre spot. On a half rink the halfway line is the open edge at
        mx = 0, so the spot sits at the middle of that edge -- i.e. the centre of
        the halfway semicircle, not the middle of the visible board."""
        return self._rink_to_px(0.0 if self.half_rink_var.get() else 20.0, 10.0)

    def _pitch_center_px(self):
        rink_len = 20.0 if self.half_rink_var.get() else 40.0
        rink_wid = 20.0
        # When rotated the rink's long axis runs down the canvas, so the pixel spans
        # swap over -- using the landscape spans puts the centre off the pitch.
        span_x, span_y = (rink_wid, rink_len) if self.rink_rotated else (rink_len, rink_wid)
        return (self.pitch_ox + span_x * self.pitch_scale / 2,
                self.pitch_oy + span_y * self.pitch_scale / 2)

    def _draw_pitch(self):
        self.canvas.delete("pitch")
        w, h = self.width, self.height
        is_half = self.half_rink_var.get()
        
        rink_len = 20.0 if is_half else 40.0
        rink_wid = 20.0
        corner_r = 2.0
        
        margin = 35
        avail_w = w - (margin * 2)
        avail_h = h - (margin * 2)
        
        if self.rink_rotated:
            scale = min(avail_w / rink_wid, avail_h / rink_len)
            ox = (w - rink_wid * scale) / 2
            oy = (h - rink_len * scale) / 2
            def m2px(mx, my):
                return ox + my * scale, oy + (rink_len - mx) * scale
        else:
            scale = min(avail_w / rink_len, avail_h / rink_wid)
            ox = (w - rink_len * scale) / 2
            oy = (h - rink_wid * scale) / 2
            def m2px(mx, my):
                return ox + mx * scale, oy + my * scale

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

        lt1_x, lt1_y = m2px(corner_r, 0)
        lt2_x, lt2_y = m2px(rink_len - corner_r, 0)
        self.canvas.create_line(lt1_x, lt1_y, lt2_x, lt2_y, fill="#343a40", width=2.5, tags="pitch")

        lb1_x, lb1_y = m2px(corner_r, rink_wid)
        lb2_x, lb2_y = m2px(rink_len - corner_r, rink_wid)
        self.canvas.create_line(lb1_x, lb1_y, lb2_x, lb2_y, fill="#343a40", width=2.5, tags="pitch")

        ll1_x, ll1_y = m2px(0, corner_r)
        ll2_x, ll2_y = m2px(0, rink_wid - corner_r)
        self.canvas.create_line(ll1_x, ll1_y, ll2_x, ll2_y, fill="#343a40", width=2.5, tags="pitch")

        lr1_x, lr1_y = m2px(rink_len, corner_r)
        lr2_x, lr2_y = m2px(rink_len, rink_wid - corner_r)
        self.canvas.create_line(lr1_x, lr1_y, lr2_x, lr2_y, fill="#343a40", width=2.5, tags="pitch")

        self.canvas.create_arc(tl_x1, tl_y1, tl_x2, tl_y2, start=corner_start["tl"], extent=90, style=tk.ARC, outline="#343a40", width=2.5, tags="pitch")
        self.canvas.create_arc(tr_x1, tr_y1, tr_x2, tr_y2, start=corner_start["tr"], extent=90, style=tk.ARC, outline="#343a40", width=2.5, tags="pitch")
        self.canvas.create_arc(bl_x1, bl_y1, bl_x2, bl_y2, start=corner_start["bl"], extent=90, style=tk.ARC, outline="#343a40", width=2.5, tags="pitch")
        self.canvas.create_arc(br_x1, br_y1, br_x2, br_y2, start=corner_start["br"], extent=90, style=tk.ARC, outline="#343a40", width=2.5, tags="pitch")

        if not is_half:
            # Both endpoints must go through m2px in full: when rotated, the x of a
            # pixel depends only on my and the y only on mx, so mixing components
            # from two different m2px calls collapses the line to zero length.
            cl1_x, cl1_y = m2px(20.0, 0)
            cl2_x, cl2_y = m2px(20.0, 20.0)
            self.canvas.create_line(cl1_x, cl1_y, cl2_x, cl2_y, fill="#ced4da", width=2, tags="pitch")

        # On a half rink the local mx axis spans only one half of the 40 m rink: the
        # goal end is at mx = rink_len and the halfway line is the open edge at
        # mx = 0. Placing the centre circle at mx = 20 (correct for a full rink) put
        # it on the goal end wall, overlapping the cage and goal areas.
        cc_px, cc_py = m2px(0.0 if is_half else 20.0, 10.0)
        c_radius_px = 3.0 * scale
        if is_half:
            # The semicircle has to open into the rink. Rotating the rink turns the
            # whole mapping 90 deg, so the arc's start angle turns with it.
            self.canvas.create_arc(
                cc_px - c_radius_px, cc_py - c_radius_px,
                cc_px + c_radius_px, cc_py + c_radius_px,
                start=(0 if self.rink_rotated else 270), extent=180,
                outline="#ced4da", width=2, style=tk.ARC, tags="pitch"
            )
        else:
            self.canvas.create_oval(
                cc_px - c_radius_px, cc_py - c_radius_px,
                cc_px + c_radius_px, cc_py + c_radius_px,
                outline="#ced4da", width=2, tags="pitch"
            )

        goal_line_dist = 2.85
        cage_depth = 0.65
        cage_width = 1.6
        small_depth = 1.0
        small_width = 2.5
        large_depth = 4.0
        large_width = 5.0
        
        def draw_goal_end(goal_line_x, is_left):
            # Take both endpoints straight from m2px; taking the x from one call and
            # the y from another only happens to work in the landscape mapping and
            # degenerates to a zero-length line once the rink is rotated.
            gl1_x, gl1_y = m2px(goal_line_x, 10.0 - (cage_width/2))
            gl2_x, gl2_y = m2px(goal_line_x, 10.0 + (cage_width/2))
            self.canvas.create_line(gl1_x, gl1_y, gl2_x, gl2_y, fill="#000000", width=2.5, tags="pitch")

            # The cage is a solid black box. It is built as a polygon from four
            # rink-space corners rather than as an axis-aligned rectangle so it stays
            # correct at either end of the rink and in either orientation.
            back_x = goal_line_x - cage_depth if is_left else goal_line_x + cage_depth
            cage_y1 = 10.0 - (cage_width / 2)
            cage_y2 = 10.0 + (cage_width / 2)
            cage_pts = [
                m2px(goal_line_x, cage_y1),
                m2px(back_x, cage_y1),
                m2px(back_x, cage_y2),
                m2px(goal_line_x, cage_y2),
            ]
            flat = [c for pt in cage_pts for c in pt]
            self.canvas.create_polygon(*flat, fill="#000000", outline="#000000", width=1, tags="pitch")

            small_x1 = goal_line_x if is_left else goal_line_x - small_depth
            small_x2 = goal_line_x + small_depth if is_left else goal_line_x
            sx1, sy1 = m2px(small_x1, 10.0 - (small_width/2))
            sx2, sy2 = m2px(small_x2, 10.0 + (small_width/2))
            self.canvas.create_rectangle(sx1, sy1, sx2, sy2, outline="#ced4da", width=2, tags="pitch")

            large_x1 = goal_line_x if is_left else goal_line_x - large_depth
            large_x2 = goal_line_x + large_depth if is_left else goal_line_x
            lx1, ly1 = m2px(large_x1, 10.0 - (large_width/2))
            lx2, ly2 = m2px(large_x2, 10.0 + (large_width/2))
            self.canvas.create_rectangle(lx1, ly1, lx2, ly2, outline="#ced4da", width=2, tags="pitch")

        if self.goals_visible_var.get():
            if is_half:
                draw_goal_end(20.0 - goal_line_dist, False)
            else:
                draw_goal_end(goal_line_dist, True)
                draw_goal_end(40.0 - goal_line_dist, False)

        # Face-off crosses: the four corner spots plus the three on the halfway line
        # (including the centre spot). Every one of them is set in from the boards by
        # the same 2.85 m that separates a goal line from the end wall. On a half rink
        # the halfway line is the open edge at mx = 0 and only one end exists.
        cross_arm = 0.25
        if is_half:
            halfway_x, end_xs = 0.0, [rink_len - goal_line_dist]
        else:
            halfway_x, end_xs = 20.0, [goal_line_dist, rink_len - goal_line_dist]

        faceoff_spots = []
        for end_x in end_xs:
            faceoff_spots.append((end_x, goal_line_dist))
            faceoff_spots.append((end_x, rink_wid - goal_line_dist))
        faceoff_spots.append((halfway_x, goal_line_dist))
        faceoff_spots.append((halfway_x, rink_wid - goal_line_dist))
        faceoff_spots.append((halfway_x, rink_wid / 2))

        for spot_x, spot_y in faceoff_spots:
            # Diagonal arms: a face-off mark is an X, not a +.
            nw = m2px(spot_x - cross_arm, spot_y - cross_arm)
            se = m2px(spot_x + cross_arm, spot_y + cross_arm)
            sw = m2px(spot_x - cross_arm, spot_y + cross_arm)
            ne = m2px(spot_x + cross_arm, spot_y - cross_arm)
            self.canvas.create_line(*nw, *se, fill="#000000", width=2, tags="pitch")
            self.canvas.create_line(*sw, *ne, fill="#000000", width=2, tags="pitch")

        self.draw_grid_points()

        self.canvas.tag_lower("pitch")
        self._render_watermark()

if __name__ == "__main__":
    root = tk.Tk()
    app = FloorballTacticsApp(root)
    root.mainloop()
