# floorball_animator.py
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, colorchooser
from PIL import Image, ImageTk, ImageDraw
import math
import os
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
            self.app.canvas.move(token["shape_id"], dx, dy)
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
    def __init__(self, root):
        self.root = root
        self.root.title("Floorball Tactics Studio")
        self.root.geometry("1300x850")
        self.root.configure(bg="#f8f9fa")

        # Configure Modern TTK Styles
        self.style = ttk.Style()
        self.style.theme_use("clam")
        self.style.configure(".", background="#f8f9fa", foreground="#212529", font=("Segoe UI", 9))
        self.style.configure("TLabel", background="#ffffff", foreground="#212529")
        self.style.configure("TLabelframe", background="#ffffff", bordercolor="#dee2e6", relief="solid")
        self.style.configure("TLabelframe.Label", background="#ffffff", foreground="#343a40", font=("Segoe UI", 9, "bold"))

        # State Variables
        self.width = 1120
        self.height = 560
        self.tokens = {}        # token_id -> metadata (tokens share same object for text ids)
        self.drawn_items = {}   # canvas_id -> metadata for signs/lines
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

        self.groups = []  
        self.tool_buttons = {}
        self.setting_buttons = {}
        self.sign_images = {}
        self.icon_cache = {}

        # config path
        self.config_path = os.path.join(str(pathlib.Path.home()), ".floorball_tactics_config.json")
        self._load_config()

        # view/ layout state
        self.menu_two_rows = False
        self.rink_rotated = False  # False = default landscape, True = rotated 90deg vertical
        self._last_esc_time = 0.0

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
                self.half_rink_var = tk.BooleanVar(value=cfg.get("half_rink", self.half_rink_var.get()))
                self.grid_var = tk.BooleanVar(value=False)
                self.snap_player_var = tk.BooleanVar(value=cfg.get("snap_player", self.snap_player_var.get()))
                self.snap_angle_var = tk.BooleanVar(value=cfg.get("snap_angle", self.snap_angle_var.get()))
                self.ghosting_var = tk.BooleanVar(value=False)
                self.dont_bother_again = cfg.get("dont_bother_again", False)
                self.menu_two_rows = cfg.get("menu_two_rows", self.menu_two_rows)
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
                "half_rink": self.half_rink_var.get(),
                "grid": self.grid_var.get(),
                "snap_player": self.snap_player_var.get(),
                "snap_angle": self.snap_angle_var.get(),
                "ghosting": self.ghosting_var.get(),
                "dont_bother_again": self.dont_bother_again,
                "menu_two_rows": self.menu_two_rows,
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
    def save_macro(self):
        if not self.undo_stack:
            messagebox.showinfo("Empty", "No history available to save as macro.")
            return
        filepath = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON Files", "*.json")])
        if filepath:
            data = [cmd.serialize() for cmd in self.undo_stack if hasattr(cmd, "serialize")]
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

            for cmd_data in data:
                ctype = cmd_data.get("type")
                if ctype == "move_tokens":
                    self.push_command(MoveTokensCommand(self, cmd_data["moves"]))
                elif ctype == "draw":
                    self.push_command(DrawLineCommand(self, cmd_data["tool"], cmd_data["x1"], cmd_data["y1"], cmd_data["x2"], cmd_data["y2"], cmd_data.get("extra")))
                elif ctype == "group":
                    self.push_command(GroupCommand(self, cmd_data["labels"], cmd_data.get("is_ungroup", False)))
                elif ctype == "lock":
                    self.push_command(LockCommand(self, cmd_data["labels"], cmd_data["lock_state"]))

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
        menubar.add_cascade(label="Menu", menu=menu_menu)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Save Macro...", command=self.save_macro)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        menubar.add_cascade(label="File", menu=file_menu)

        self.root.config(menu=menubar)

        self.top_bar = tk.Frame(self.root, bg="#ffffff", bd=1, relief=tk.SOLID)
        self.top_bar.pack(side=tk.TOP, fill=tk.X, padx=10, pady=8)

        self.top_inner = tk.Frame(self.top_bar, bg="#ffffff", padx=6, pady=6)
        self.top_inner.pack(fill=tk.X)

        self.top_row1 = tk.Frame(self.top_inner, bg="#ffffff")
        self.top_row2 = tk.Frame(self.top_inner, bg="#ffffff")
        self.top_row1.pack(fill=tk.X)

        gray_btn_cfg = {"font": ("Segoe UI", 8), "relief": tk.RAISED, "cursor": "hand2", "padx": 4, "pady": 1, "bg": "#d3d3d3", "activebackground": "#c0c0c0"}

        snapping_frame = ttk.LabelFrame(self.top_row1, text=" Board Settings ", padding=5)
        snapping_frame.pack(side=tk.LEFT, padx=3, pady=2, fill=tk.Y)
        self._snapping_frame = snapping_frame
        
        setting_grid = tk.Frame(snapping_frame, bg="#ffffff")
        setting_grid.pack(fill=tk.BOTH)

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

        for idx, (label_txt, var_ref, name_key) in enumerate(settings_list):
            r, c = divmod(idx, 3)
            btn = tk.Button(setting_grid, text=label_txt, command=lambda v=var_ref, k=name_key: toggle_setting(v, k), **gray_btn_cfg, width=7)
            btn.grid(row=r, column=c, padx=1, pady=1)
            self.setting_buttons[name_key] = btn

        roster_frame = ttk.LabelFrame(self.top_row1, text=" Roster ", padding=5)
        roster_frame.pack(side=tk.LEFT, padx=3, pady=2, fill=tk.Y)
        self._roster_frame = roster_frame

        att_row = tk.Frame(roster_frame, bg="#ffffff")
        att_row.pack(fill=tk.X, pady=1)
        tk.Label(att_row, text="Atk:", bg="#ffffff", font=("Segoe UI", 8, "bold"), width=3, anchor="w").pack(side=tk.LEFT)
        self.att_spinbox = tk.Spinbox(att_row, from_=1, to=10, width=2, command=self._update_roster, font=("Segoe UI", 8))
        self.att_spinbox.delete(0, tk.END)
        self.att_spinbox.insert(0, "5")
        self.att_spinbox.pack(side=tk.LEFT, padx=1)
        self.att_shape_var = tk.StringVar(value="Square")
        ttk.Combobox(att_row, textvariable=self.att_shape_var, values=["Square", "Circle", "X", "Triangle", "Plus"], width=7, font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=1)
        self.btn_att_color = tk.Button(att_row, text="Att Color", width=10, command=self.choose_att_color, **gray_btn_cfg)
        self.btn_att_color.config(bg=self.att_color)
        self.btn_att_color.pack(side=tk.LEFT, padx=1)

        def_row = tk.Frame(roster_frame, bg="#ffffff")
        def_row.pack(fill=tk.X, pady=1)
        tk.Label(def_row, text="Def:", bg="#ffffff", font=("Segoe UI", 8, "bold"), width=3, anchor="w").pack(side=tk.LEFT)
        self.def_spinbox = tk.Spinbox(def_row, from_=1, to=10, width=2, command=self._update_roster, font=("Segoe UI", 8))
        self.def_spinbox.delete(0, tk.END)
        self.def_spinbox.insert(0, "5")
        self.def_spinbox.pack(side=tk.LEFT, padx=1)
        self.def_shape_var = tk.StringVar(value="Circle")
        ttk.Combobox(def_row, textvariable=self.def_shape_var, values=["Square", "Circle", "X", "Triangle", "Plus"], width=7, font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=1)
        self.btn_def_color = tk.Button(def_row, text="Def Color", width=10, command=self.choose_def_color, **gray_btn_cfg)
        self.btn_def_color.config(bg=self.def_color)
        self.btn_def_color.pack(side=tk.LEFT, padx=1)

        size_row = tk.Frame(roster_frame, bg="#ffffff")
        size_row.pack(fill=tk.X, pady=1)
        tk.Label(size_row, text="Size:", bg="#ffffff", font=("Segoe UI", 8, "bold"), width=3, anchor="w").pack(side=tk.LEFT)
        self.player_size_spinbox = tk.Spinbox(size_row, from_=6, to=60, width=3, textvariable=self.player_size_var, command=self._resize_selected_players, font=("Segoe UI", 8))
        self.player_size_spinbox.pack(side=tk.LEFT, padx=1)

        signs_frame = ttk.LabelFrame(self.top_row1, text=" Signs ", padding=5)
        signs_frame.pack(side=tk.LEFT, padx=3, pady=2, fill=tk.Y)
        self._signs_frame = signs_frame
        sign_size_row = tk.Frame(signs_frame, bg="#ffffff")
        sign_size_row.pack(fill=tk.X, pady=1)
        tk.Label(sign_size_row, text="Size:", bg="#ffffff", font=("Segoe UI", 8, "bold"), width=3, anchor="w").pack(side=tk.LEFT)
        self.sign_size_spinbox = tk.Spinbox(sign_size_row, from_=6, to=60, width=3, textvariable=self.sign_size_var, font=("Segoe UI", 8))
        self.sign_size_spinbox.pack(side=tk.LEFT, padx=1)
        sign_grid = tk.Frame(signs_frame, bg="#ffffff")
        sign_grid.pack(fill=tk.BOTH)
        
        sign_types = ["Goal", "X", "Dot", "Square", "Triangle", "Plus"]
        for idx, stype in enumerate(sign_types):
            r, c = divmod(idx, 3)
            btn = tk.Button(sign_grid, text=stype, command=lambda t=stype: self.set_tool(f"sign_{t.lower()}"), **gray_btn_cfg, width=8)
            btn.grid(row=r, column=c, padx=1, pady=1)
            self.tool_buttons[f"sign_{stype.lower()}"] = btn

        actions_frame = ttk.LabelFrame(self.top_row1, text=" Drawing Tools ", padding=5)
        actions_frame.pack(side=tk.LEFT, padx=3, pady=2, fill=tk.Y)
        self._actions_frame = actions_frame

        act_row1 = tk.Frame(actions_frame, bg="#ffffff")
        act_row1.pack(fill=tk.X, pady=1)
        act_row2 = tk.Frame(actions_frame, bg="#ffffff")
        act_row2.pack(fill=tk.X, pady=1)
        act_row3 = tk.Frame(actions_frame, bg="#ffffff")
        act_row3.pack(fill=tk.X, pady=1)
        
        select_btn = tk.Button(act_row1, text="Select", command=self.cancel_active_tool, font=("Segoe UI", 8, "bold"), relief=tk.RAISED, cursor="hand2", padx=3, pady=1, bg="#cce5ff", fg="#004085")
        select_btn.pack(side=tk.LEFT, padx=1)
        self.tool_buttons["select"] = select_btn

        self.icon_pass = self._create_action_icon("pass")
        self.icon_shot = self._create_action_icon("shot")
        self.icon_dribble = self._create_action_icon("dribble")
        self.icon_run = self._create_action_icon("run")

        pass_btn = tk.Button(act_row1, text="Pass", image=self.icon_pass, compound=tk.LEFT, command=lambda: self.set_tool("pass"), **gray_btn_cfg, width=80)
        pass_btn.image = self.icon_pass
        pass_btn.pack(side=tk.LEFT, padx=1)
        self.tool_buttons["pass"] = pass_btn

        shot_btn = tk.Button(act_row1, text="Shot", image=self.icon_shot, compound=tk.LEFT, command=lambda: self.set_tool("shot"), **gray_btn_cfg, width=80)
        shot_btn.image = self.icon_shot
        shot_btn.pack(side=tk.LEFT, padx=1)
        self.tool_buttons["shot"] = shot_btn

        drib_btn = tk.Button(act_row1, text="Dribble", image=self.icon_dribble, compound=tk.LEFT, command=lambda: self.set_tool("dribble"), **gray_btn_cfg, width=90)
        drib_btn.image = self.icon_dribble
        drib_btn.pack(side=tk.LEFT, padx=1)
        self.tool_buttons["dribble"] = drib_btn

        run_btn = tk.Button(act_row1, text="Run", image=self.icon_run, compound=tk.LEFT, command=lambda: self.set_tool("run"), **gray_btn_cfg, width=80)
        run_btn.image = self.icon_run
        run_btn.pack(side=tk.LEFT, padx=1)
        self.tool_buttons["run"] = run_btn

        line_btn = tk.Button(act_row2, text="Line", command=lambda: self.set_tool("line"), **gray_btn_cfg, width=8)
        line_btn.pack(side=tk.LEFT, padx=1)
        self.tool_buttons["line"] = line_btn

        bend_btn = tk.Button(act_row2, text="Bend", command=lambda: self.set_tool("bend"), **gray_btn_cfg, width=8)
        bend_btn.pack(side=tk.LEFT, padx=1)
        self.tool_buttons["bend"] = bend_btn

        box_btn = tk.Button(act_row2, text="Box", command=lambda: self.set_tool("box"), **gray_btn_cfg, width=8)
        box_btn.pack(side=tk.LEFT, padx=1)
        self.tool_buttons["box"] = box_btn

        rect_btn = tk.Button(act_row2, text="Rect", command=lambda: self.set_tool("rectangle"), **gray_btn_cfg, width=8)
        rect_btn.pack(side=tk.LEFT, padx=1)
        self.tool_buttons["rectangle"] = rect_btn

        circ_btn = tk.Button(act_row2, text="Circle", command=lambda: self.set_tool("circle"), **gray_btn_cfg, width=8)
        circ_btn.pack(side=tk.LEFT, padx=1)
        self.tool_buttons["circle"] = circ_btn

        oval_btn = tk.Button(act_row2, text="Oval", command=lambda: self.set_tool("oval"), **gray_btn_cfg, width=8)
        oval_btn.pack(side=tk.LEFT, padx=1)
        self.tool_buttons["oval"] = oval_btn

        rotate_btn = tk.Button(act_row3, text="Rotate Sel", command=self.rotate_selected, **gray_btn_cfg, width=10)
        rotate_btn.pack(side=tk.LEFT, padx=1, pady=1)

        copy_style_btn = tk.Button(act_row3, text="Copy Style", command=self.toggle_copy_paste_style, **gray_btn_cfg, width=12)
        copy_style_btn.pack(side=tk.LEFT, padx=1)
        self.tool_buttons["copy_style"] = copy_style_btn
        self.copy_style_btn = copy_style_btn

        set_default_btn = tk.Button(act_row3, text="Set as Default", command=self.set_as_default_popup, **gray_btn_cfg, width=12)
        set_default_btn.pack(side=tk.LEFT, padx=1)
        self.tool_buttons["set_default"] = set_default_btn

        line_style_sub = tk.Frame(act_row3, bg="#ffffff")
        line_style_sub.pack(side=tk.LEFT, padx=2)
        
        tk.Label(line_style_sub, text="Type:", bg="#ffffff", font=("Segoe UI", 7)).pack(side=tk.LEFT)
        line_style_options = ["Solid", "Dashed", "Dotted", "Pass", "Shot", "Dribble", "Run"]
        ttk.Combobox(line_style_sub, textvariable=self.line_type_var, values=line_style_options, width=6, font=("Segoe UI", 7)).pack(side=tk.LEFT, padx=1)
        
        tk.Label(line_style_sub, text="Thick:", bg="#ffffff", font=("Segoe UI", 7)).pack(side=tk.LEFT)
        ttk.Combobox(line_style_sub, textvariable=self.line_thick_var, values=["1", "2", "3", "4", "5"], width=2, font=("Segoe UI", 7)).pack(side=tk.LEFT, padx=1)
        
        tk.Label(line_style_sub, text="Clr", bg="#ffffff", font=("Segoe UI", 7)).pack(side=tk.LEFT)
        self.btn_line_color = tk.Button(line_style_sub, text="Line Color", width=10, command=self.choose_line_color, **gray_btn_cfg)
        self.btn_line_color.config(bg=self.line_color)
        self.btn_line_color.pack(side=tk.LEFT, padx=1)

        align_frame = ttk.LabelFrame(self.top_row1, text=" Board Control ", padding=5)
        align_frame.pack(side=tk.LEFT, padx=3, pady=2, fill=tk.Y)
        self._align_frame = align_frame

        a_col1 = tk.Frame(align_frame, bg="#ffffff")
        a_col1.pack(side=tk.LEFT, padx=2)
        align_btn_cfg = {"bg": "#d3d3d3", "activebackground": "#c0c0c0", "relief": tk.RAISED, "font": ("Segoe UI", 8), "width": 8}
        tk.Button(a_col1, text="Align H", command=lambda: self.align_tokens("horizontal"), **align_btn_cfg).grid(row=0, column=0, padx=1, pady=1, sticky="ew")
        tk.Button(a_col1, text="Align V", command=lambda: self.align_tokens("vertical"), **align_btn_cfg).grid(row=0, column=1, padx=1, pady=1, sticky="ew")
        tk.Button(a_col1, text="Distribute H", command=self.distribute_horizontally, **align_btn_cfg).grid(row=1, column=0, padx=1, pady=1, sticky="ew")
        tk.Button(a_col1, text="Distribute V", command=self.distribute_vertically, **align_btn_cfg).grid(row=1, column=1, padx=1, pady=1, sticky="ew")

        a_col2 = tk.Frame(align_frame, bg="#ffffff")
        a_col2.pack(side=tk.LEFT, padx=2)
        tk.Button(a_col2, text="Group", command=self.group_selected, **gray_btn_cfg, width=10).pack(fill=tk.X, pady=1)
        tk.Button(a_col2, text="Lock", command=self.lock_selected, **gray_btn_cfg, width=10).pack(fill=tk.X, pady=1)

        timeline_frame = ttk.LabelFrame(self.top_row1, text=" Timeline & Macros ", padding=5)
        timeline_frame.pack(side=tk.LEFT, padx=3, pady=2, fill=tk.Y)
        self._timeline_frame = timeline_frame

        t_sub = tk.Frame(timeline_frame, bg="#ffffff")
        t_sub.pack(side=tk.LEFT, fill=tk.Y)
        
        self.steps_listbox = tk.Listbox(t_sub, font=("Segoe UI", 7), selectmode=tk.SINGLE, height=3, width=15, relief=tk.SOLID, bd=1, highlightthickness=0, bg="#f8f9fa")
        self.steps_listbox.pack(side=tk.LEFT, padx=2)

        t_btns = tk.Frame(timeline_frame, bg="#ffffff")
        t_btns.pack(side=tk.LEFT, padx=2, fill=tk.Y)
        
        tk.Button(t_btns, text="Undo", command=self.undo, width=6, **gray_btn_cfg).pack(fill=tk.X, pady=1)
        tk.Button(t_btns, text="Redo", command=self.redo, width=6, **gray_btn_cfg).pack(fill=tk.X, pady=1)
        
        io_btns = tk.Frame(timeline_frame, bg="#ffffff")
        io_btns.pack(side=tk.LEFT, padx=2, fill=tk.Y)
        tk.Button(io_btns, text="Save", command=self.save_macro, width=6, **gray_btn_cfg).pack(fill=tk.X, pady=1)
        tk.Button(io_btns, text="Load", command=self.load_macro, width=6, **gray_btn_cfg).pack(fill=tk.X, pady=1)
        tk.Button(io_btns, text="Watermark", command=self.add_watermark, width=8, **gray_btn_cfg).pack(fill=tk.X, pady=1)

        canvas_container = tk.Frame(self.root, bg="#ffffff", bd=1, relief=tk.SOLID)
        canvas_container.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        self.canvas = tk.Canvas(canvas_container, width=self.width, height=self.height, bg="#ffffff", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=4, pady=4, anchor="center")

        self.canvas.bind("<ButtonPress-1>", self.on_canvas_press)
        self.canvas.bind("<B1-Motion>", self.on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_canvas_release)
        self.root.bind("<Configure>", self.on_window_resize)

        self._update_indicators()

        if self.menu_two_rows:
            self.toggle_menu_rows()

    def _update_indicators(self):
        for name, btn in self.tool_buttons.items():
            try:
                if name == self.active_tool:
                    btn.config(bg="#228be6", fg="#ffffff")
                else:
                    btn.config(bg="#d3d3d3", fg="#212529")
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
                    btn.config(bg="#228be6", fg="#ffffff")
                except Exception:
                    pass
            else:
                try:
                    btn.config(bg="#d3d3d3", fg="#212529")
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
                self.canvas.itemconfig(sid, fill=new_color, outline=token.get("outline", "#343a40"))
            elif st in ("x", "plus"):
                for k, v in list(self.tokens.items()):
                    if v is token and isinstance(k, int):
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

    def toggle_menu_rows(self):
        self.menu_two_rows = not self.menu_two_rows
        for child in list(self.top_row1.pack_slaves()):
            child.pack_forget()
        for child in list(self.top_row2.pack_slaves()):
            child.pack_forget()
        sections = [self._snapping_frame, self._roster_frame, self._signs_frame, self._actions_frame, self._align_frame, self._timeline_frame]
        if self.menu_two_rows:
            half = math.ceil(len(sections) / 2)
            for s in sections[:half]:
                s.pack(in_=self.top_row1, side=tk.LEFT, padx=3, pady=2, fill=tk.Y)
            self.top_row2.pack(fill=tk.X)
            for s in sections[half:]:
                s.pack(in_=self.top_row2, side=tk.LEFT, padx=3, pady=2, fill=tk.Y)
        else:
            for s in sections:
                s.pack(in_=self.top_row1, side=tk.LEFT, padx=3, pady=2, fill=tk.Y)
            try:
                self.top_row2.pack_forget()
            except Exception:
                pass

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
            self._create_token(x, y, f"A{i}", shape=att_shape, color=self.att_color, size=player_size)

        for i in range(1, num_def + 1):
            x = self.width * 0.7
            y = self.height * (i / (num_def + 1))
            self._create_token(x, y, f"D{i}", shape=def_shape, color=self.def_color, size=player_size)

        self._create_token(self.width * 0.5, self.height * 0.5, "B", shape="ball", color="black", size=player_size)

    def _create_token(self, x, y, label, shape="circle", color="black", outline="#343a40", stipple="", size=None):
        base_size = 14 if size is None else max(6, int(size))
        shape_lower = shape.lower()
        shape_ids = []
        
        rect_kwargs = {"fill": color, "outline": outline, "width": 2, "tags": ("token",)}
        if stipple: rect_kwargs["stipple"] = stipple
        
        line_kwargs = {"fill": color, "width": 3, "tags": ("token",)}
        if stipple: line_kwargs["stipple"] = stipple

        if shape_lower == "square":
            shape_id = self.canvas.create_rectangle(x-base_size, y-base_size, x+base_size, y+base_size, **rect_kwargs)
        elif shape_lower == "ball":
            ball_size = max(4, base_size // 2)
            shape_id = self.canvas.create_oval(x-ball_size, y-ball_size, x+ball_size, y+ball_size, **rect_kwargs)
        elif shape_lower == "x":
            s = base_size
            shape_id = self.canvas.create_line(x-s, y-s, x+s, y+s, **line_kwargs)
            shape_id2 = self.canvas.create_line(x-s, y+s, x+s, y-s, **line_kwargs)
        elif shape_lower == "triangle":
            s = base_size
            shape_id = self.canvas.create_polygon(x, y-s, x-s, y+s, x+s, y+s, **rect_kwargs)
        elif shape_lower == "plus":
            s = base_size
            shape_id = self.canvas.create_line(x-s, y, x+s, y, **line_kwargs)
            shape_id2 = self.canvas.create_line(x, y-s, x, y+s, **line_kwargs)
        else:
            shape_id = self.canvas.create_oval(x-base_size, y-base_size, x+base_size, y+base_size, **rect_kwargs)

        base_font_size = max(6, int(base_size * 0.57))
        text_offsets = []
        text_ids = []
        if shape_lower != "ball":
            for ox, oy in [(-1, -1), (1, -1), (-1, 1), (1, 1), (-1, 0), (1, 0), (0, -1), (0, 1)]:
                otid = self.canvas.create_text(
                    x + ox, y + oy,
                    text=label,
                    fill="black",
                    font=("Segoe UI", base_font_size, "bold"),
                    tags=("token",)
                )
                text_ids.append(otid)
                text_offsets.append((ox, oy))

            main_otid = self.canvas.create_text(
                x, y,
                text=label,
                fill="white",
                font=("Segoe UI", base_font_size, "bold"),
                tags=("token",)
            )
            text_ids.append(main_otid)
            text_offsets.append((0, 0))

        token = {
            "shape_id": shape_id,
            "text_ids": text_ids,
            "text_offsets": text_offsets,
            "shape": shape,
            "label": label,
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
                coords = self.canvas.coords(sid)
                if not coords:
                    continue
                x1_t, y1_t, x2_t, y2_t = coords
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
            coords = self.canvas.coords(token["shape_id"])
            if not coords:
                return False
            cx = (coords[0] + coords[2]) / 2
            cy = (coords[1] + coords[3]) / 2
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
        color = self.line_color
        created = []
        sign_lower = sign_type.lower()
        sc = getattr(self, 'pitch_scale', 20)
        size_value = max(6, int(self.sign_size_var.get())) if size is None else max(6, int(size))
        if sign_lower == "goal":
            gw = 1.6 * size_value
            gd = 0.65 * size_value
            gid = self.canvas.create_rectangle(x - gw/2, y - gd/2, x + gw/2, y + gd/2, outline=color, fill="black", width=2, tags=("sign",))
            created.append(gid)
        elif sign_lower == "x":
            s = size_value
            id1 = self.canvas.create_line(x-s, y-s, x+s, y+s, fill=color, width=2, tags=("sign",))
            id2 = self.canvas.create_line(x-s, y+s, x+s, y-s, fill=color, width=2, tags=("sign",))
            created.extend([id1, id2])
        elif sign_lower == "dot" or sign_lower == "circle":
            r = max(3, size_value // 2)
            id1 = self.canvas.create_oval(x-r, y-r, x+r, y+r, fill=color, outline=color, tags=("sign",))
            created.append(id1)
        elif sign_lower == "square":
            s = size_value
            id1 = self.canvas.create_rectangle(x-s, y-s, x+s, y+s, outline=color, width=2, tags=("sign",))
            created.append(id1)
        elif sign_lower == "triangle":
            s = size_value
            id1 = self.canvas.create_polygon(x, y-s, x-s, y+s, x+s, y+s, outline=color, fill="", width=2, tags=("sign",))
            created.append(id1)
        elif sign_lower == "plus":
            s = size_value
            id1 = self.canvas.create_line(x-s, y, x+s, y, fill=color, width=2, tags=("sign",))
            id2 = self.canvas.create_line(x, y-s, x, y+s, fill=color, width=2, tags=("sign",))
            created.extend([id1, id2])
        for cid in created:
            self.drawn_items[cid] = {"type": "sign", "sign_type": sign_type, "color": color, "size": size_value}
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
            self.place_sign_canvas(event.x, event.y, stype)
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
                self.canvas.move(token["shape_id"], dx, dy)
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
                                self.canvas.move(token["shape_id"], dx, dy)
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
                default_outline = token.get("outline", "#343a40")
                self.canvas.itemconfig(token["shape_id"], outline="#6c757d" if token.get("locked", False) else default_outline, width=2)
            except Exception:
                pass
        for cid in list(self.drawn_items.keys()):
            try:
                self.canvas.itemconfig(cid, fill=self.drawn_items[cid].get("color", self.line_color))
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
            self.canvas.move(old_shape_id, new_cx - old_cx, new_cy - old_cy)
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
        try:
            new_size = max(6, int(self.player_size_var.get()))
        except Exception:
            return
        for sid in list(self.selected_tokens):
            token = self.tokens.get(sid)
            if not token or token.get("is_ghost") or token.get("locked", False):
                continue
            old_shape_id = token.get("shape_id")
            if not old_shape_id:
                continue
            coords = self.canvas.coords(old_shape_id)
            if not coords:
                continue
            cx = (coords[0] + coords[2]) / 2.0
            cy = (coords[1] + coords[3]) / 2.0
            old_text_ids = list(token.get("text_ids", []))
            for tid in old_text_ids:
                try:
                    self.canvas.delete(tid)
                except Exception:
                    pass
            try:
                self.canvas.delete(old_shape_id)
            except Exception:
                pass
            self.tokens.pop(old_shape_id, None)
            for tid in old_text_ids:
                self.tokens.pop(tid, None)
            shape_id = self._create_token(cx, cy, token["label"], shape=token.get("shape", "circle"), color=token.get("color", "black"), outline=token.get("outline", "#343a40"), stipple=token.get("stipple", ""), size=new_size)
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
            for idx, selected_id in enumerate(self.selected_tokens):
                if selected_id == old_shape_id:
                    self.selected_tokens[idx] = shape_id
                    break
            self.highlight_selected()

    def highlight_selected(self):
        for token in self.tokens.values():
            sid = token["shape_id"]
            try:
                default_outline = token.get("outline", "#343a40")
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

    def _rescale_tokens(self, old_scale, old_ox, old_oy, new_scale, new_ox, new_oy):
        if not old_scale or old_scale <= 0 or not new_scale or new_scale <= 0: return
        ratio = new_scale / old_scale
        if abs(ratio - 1.0) < 1e-9 and old_ox == new_ox and old_oy == new_oy: return

        processed = set()
        for token in list(self.tokens.values()):
            sid = token.get("shape_id")
            if sid is None or sid in processed: continue
            processed.add(sid)
            coords = self.canvas.coords(sid)
            if not coords: continue
            cx, cy = (coords[0] + coords[2]) / 2, (coords[1] + coords[3]) / 2
            rx, ry = (cx - old_ox) / old_scale, (cy - old_oy) / old_scale
            new_cx, new_cy = new_ox + rx * new_scale, new_oy + ry * new_scale
            new_size = token.get("size", 14) * ratio
            token["size"] = new_size
            try:
                self.canvas.coords(sid, new_cx - new_size, new_cy - new_size, new_cx + new_size, new_cy + new_size)
            except Exception:
                pass
            for tid, (ox_off, oy_off) in zip(token.get("text_ids", []), token.get("text_offsets", [])):
                self.canvas.coords(tid, new_cx + ox_off, new_cy + oy_off)

    def on_window_resize(self, event):
        if event.widget == self.root:
            self.width = max(600, self.root.winfo_width() - 20)
            self.height = max(300, self.root.winfo_height() - 180)
            self.canvas.config(width=self.width, height=self.height)

            old_scale, old_ox, old_oy = getattr(self, "pitch_scale", None), getattr(self, "pitch_ox", None), getattr(self, "pitch_oy", None)
            self.redraw_canvas()
            if old_scale:
                self._rescale_tokens(old_scale, old_ox, old_oy, self.pitch_scale, self.pitch_ox, self.pitch_oy)

    def rotate_selected(self):
        for sid in self.selected_tokens:
            token = self.tokens.get(sid)
            if token and not token.get("locked", False):
                current_angle = token.get("angle", 0)
                new_angle = (current_angle + 45) % 360
                token["angle"] = new_angle
                messagebox.showinfo("Rotate", f"Rotated token {token['label']} to {new_angle}°")

    def toggle_rink_orientation(self):
        self.rotate_rink("vertical" if not self.rink_rotated else "horizontal")

    def rotate_rink(self, orientation):
        if orientation == "vertical":
            target_rotated = True
        else:
            target_rotated = False
        if target_rotated == self.rink_rotated:
            return

        old_scale = getattr(self, "pitch_scale", None)
        old_ox = getattr(self, "pitch_ox", None)
        old_oy = getattr(self, "pitch_oy", None)
        if old_scale is None:
            self.rink_rotated = target_rotated
            return

        rink_len = 20.0 if self.half_rink_var.get() else 40.0
        cx = self.pitch_ox + (rink_len * self.pitch_scale) / 2
        cy = self.pitch_oy + (20.0 * self.pitch_scale) / 2

        def rotate_point(px, py, cw=True):
            dx = px - cx
            dy = py - cy
            if cw:
                nx = cx + dy
                ny = cy - dx
            else:
                nx = cx - dy
                ny = cy + dx
            return nx, ny

        cw = True if target_rotated else False

        processed = set()
        for token in list(self.tokens.values()):
            sid = token.get("shape_id")
            if sid is None or sid in processed: continue
            processed.add(sid)
            coords = self.canvas.coords(sid)
            if not coords: continue
            px = (coords[0] + coords[2]) / 2
            py = (coords[1] + coords[3]) / 2
            nx, ny = rotate_point(px, py, cw=cw)
            w = coords[2] - coords[0]
            h = coords[3] - coords[1]
            new_coords = [nx - w/2, ny - h/2, nx + w/2, ny + h/2]
            try:
                self.canvas.coords(sid, *new_coords)
            except Exception:
                pass
            for tid, (ox_off, oy_off) in zip(token.get("text_ids", []), token.get("text_offsets", [])):
                try:
                    self.canvas.coords(tid, nx + ox_off, ny + oy_off)
                except Exception:
                    pass

        for cid in list(self.drawn_items.keys()):
            coords = self.canvas.coords(cid)
            if not coords: continue
            new_coords = []
            for i in range(0, len(coords), 2):
                px = coords[i]
                py = coords[i+1]
                nx, ny = rotate_point(px, py, cw=cw)
                new_coords.extend([nx, ny])
            try:
                self.canvas.coords(cid, *new_coords)
            except Exception:
                pass

        try:
            w_coords = self.canvas.bbox("watermark")
            if w_coords:
                wx = (w_coords[0] + w_coords[2]) / 2
                wy = (w_coords[1] + w_coords[3]) / 2
                nwx, nwy = rotate_point(wx, wy, cw=cw)
                ids = self.canvas.find_withtag("watermark")
                for iid in ids:
                    try:
                        self.canvas.coords(iid, nwx, nwy)
                    except Exception:
                        pass
        except Exception:
            pass

        self.rink_rotated = target_rotated
        self.redraw_canvas()

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
                "half_rink": self.half_rink_var.get(),
                "grid": self.grid_var.get(),
                "snap_player": self.snap_player_var.get(),
                "snap_angle": self.snap_angle_var.get(),
                "ghosting": self.ghosting_var.get(),
                "dont_bother_again": self.dont_bother_again,
                "menu_two_rows": self.menu_two_rows,
                "rink_rotated": self.rink_rotated
            }
            with open(self.config_path, "w") as f:
                json.dump(cfg, f, indent=2)
            messagebox.showinfo("Saved", "Current style saved as default.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save default style: {e}")

    def add_watermark(self):
        path = filedialog.askopenfilename(filetypes=[("Image Files","*.png;*.jpg;*.jpeg"), ("PNG", "*.png"), ("JPEG","*.jpg;*.jpeg")])
        if not path:
            return
        try:
            img = Image.open(path)
            max_w = int(self.pitch_scale * 12) if getattr(self, "pitch_scale", None) else 240
            ratio = min(1.0, max_w / img.width)
            new_w = int(img.width * ratio)
            new_h = int(img.height * ratio)
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            tkimg = ImageTk.PhotoImage(img)
            self.canvas.delete("watermark")
            cx = self.pitch_ox + ( (20.0 if self.half_rink_var.get() else 40.0) * self.pitch_scale ) / 2
            cy = self.pitch_oy + (20.0 * self.pitch_scale) / 2
            img_id = self.canvas.create_image(cx, cy, image=tkimg, tags=("watermark",))
            self._last_watermark_img = tkimg
            self.canvas.tag_lower("watermark", "pitch")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to add watermark: {e}")

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
        self._draw_pitch()
        self._update_roster()

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
        self.canvas.create_rectangle(hx1, hy1, hx2, hy2, fill="#ffffff", outline="", tags="pitch")

        vx1, vy1 = m2px(0, corner_r)
        vx2, vy2 = m2px(rink_len, rink_wid - corner_r)
        self.canvas.create_rectangle(vx1, vy1, vx2, vy2, fill="#ffffff", outline="", tags="pitch")

        tl_x1, tl_y1 = m2px(0, 0)
        tl_x2, tl_y2 = m2px(2*corner_r, 2*corner_r)
        self.canvas.create_arc(tl_x1, tl_y1, tl_x2, tl_y2, start=90, extent=90, fill="#ffffff", outline="", tags="pitch")
        tr_x1, tr_y1 = m2px(rink_len - 2*corner_r, 0)
        tr_x2, tr_y2 = m2px(rink_len, 2*corner_r)
        self.canvas.create_arc(tr_x1, tr_y1, tr_x2, tr_y2, start=0, extent=90, fill="#ffffff", outline="", tags="pitch")
        bl_x1, bl_y1 = m2px(0, rink_wid - 2*corner_r)
        bl_x2, bl_y2 = m2px(2*corner_r, rink_wid)
        self.canvas.create_arc(bl_x1, bl_y1, bl_x2, bl_y2, start=180, extent=90, fill="#ffffff", outline="", tags="pitch")
        br_x1, br_y1 = m2px(rink_len - 2*corner_r, rink_wid - 2*corner_r)
        br_x2, br_y2 = m2px(rink_len, rink_wid)
        self.canvas.create_arc(br_x1, br_y1, br_x2, br_y2, start=270, extent=90, fill="#ffffff", outline="", tags="pitch")

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

        self.canvas.create_arc(tl_x1, tl_y1, tl_x2, tl_y2, start=90, extent=90, style=tk.ARC, outline="#343a40", width=2.5, tags="pitch")
        self.canvas.create_arc(tr_x1, tr_y1, tr_x2, tr_y2, start=0, extent=90, style=tk.ARC, outline="#343a40", width=2.5, tags="pitch")
        self.canvas.create_arc(bl_x1, bl_y1, bl_x2, bl_y2, start=180, extent=90, style=tk.ARC, outline="#343a40", width=2.5, tags="pitch")
        self.canvas.create_arc(br_x1, br_y1, br_x2, br_y2, start=270, extent=90, style=tk.ARC, outline="#343a40", width=2.5, tags="pitch")

        if not is_half:
            cx, cy1 = m2px(20.0, 0)
            _, cy2 = m2px(20.0, 20.0)
            self.canvas.create_line(cx, cy1, cx, cy2, fill="#ced4da", width=2, tags="pitch")

        cc_px, cc_py = m2px(20.0, 10.0)
        c_radius_px = 3.0 * scale
        if is_half:
            self.canvas.create_arc(
                cc_px - c_radius_px, cc_py - c_radius_px,
                cc_px + c_radius_px, cc_py + c_radius_px,
                start=90, extent=180, outline="#ced4da", width=2, style=tk.ARC, tags="pitch"
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
            gl_y1, gl_y2 = m2px(goal_line_x, 10.0 - (cage_width/2))[1], m2px(goal_line_x, 10.0 + (cage_width/2))[1]
            gl_x = m2px(goal_line_x, 0)[0]
            self.canvas.create_line(gl_x, gl_y1, gl_x, gl_y2, fill="#000000", width=2.5, tags="pitch")
            
            cage_x1 = goal_line_x - cage_depth if is_left else goal_line_x
            cage_x2 = goal_line_x if is_left else goal_line_x + cage_depth
            cx1, cy1 = m2px(cage_x1, 10.0 - (cage_width/2))
            cx2, cy2 = m2px(cage_x2, 10.0 + (cage_width/2))
            self.canvas.create_rectangle(cx1, cy1, cx2, cy2, outline="black", fill="black", width=1, tags="pitch")

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

        self.draw_grid_points()

        if hasattr(self, "_last_watermark_img") and self._last_watermark_img:
            cx = self.pitch_ox + ( (20.0 if self.half_rink_var.get() else 40.0) * self.pitch_scale ) / 2
            cy = self.pitch_oy + (20.0 * self.pitch_scale) / 2
            self.canvas.create_image(cx, cy, image=self._last_watermark_img, tags=("watermark",))
            self.canvas.tag_lower("watermark", "pitch")

        self.canvas.tag_lower("pitch")

if __name__ == "__main__":
    root = tk.Tk()
    app = FloorballTacticsApp(root)
    root.mainloop()
