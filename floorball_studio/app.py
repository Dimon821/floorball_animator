"""
Floorball Tactics Studio - Main Application Class
This module contains the FloorballTacticsApp class.
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, colorchooser
from PIL import Image, ImageTk, ImageDraw
import math
import os
import json
import pathlib
import time

# Import command classes
from commands import MoveTokensCommand, MoveDrawnCommand, DrawLineCommand, GroupCommand, LockCommand

class FloorballTacticsApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Floorball Tactics Studio")
        self.root.geometry("1300x850")
        self.root.configure(bg="#f8f9fa")

        # Style configuration
        self.style = ttk.Style()
        self.style.theme_use("clam")
        self.style.configure(".", background="#f8f9fa", foreground="#212529", font=("Segoe UI", 9))
        self.style.configure("TLabel", background="#ffffff", foreground="#212529")
        self.style.configure("TLabelframe", background="#ffffff", bordercolor="#dee2e6", relief="solid")
        self.style.configure("TLabelframe.Label", background="#ffffff", foreground="#343a40", font=("Segoe UI", 9, "bold"))

        # State Variables
        self.width = 1120
        self.height = 560
        self.tokens = {}
        self.drawn_items = {}
        self.drawings = []
        self.action_steps = []
        self.clipboard = []
        self.copied_style = None
        self.paste_style_active = False
        self.dont_bother_again = False
        self.undo_stack = []
        self.redo_stack = []
        self.selected_tokens = []
        self.selected_drawn = set()
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
        self.line_color = "#000000"
        self.line_thick_var = tk.StringVar(value="2")
        self.line_type_var = tk.StringVar(value="Solid")
        self.att_color = "#000000"
        self.def_color = "#000000"
        self.groups = []
        self.tool_buttons = {}
        self.setting_buttons = {}
        self.sign_images = {}
        self.icon_cache = {}
        self.config_path = os.path.join(str(pathlib.Path.home()), ".floorball_tactics_config.json")
        self.menu_two_rows = False
        self.rink_rotated = False
        self._last_esc_time = 0.0

        # Load configuration
        self._load_config()

        # Build UI
        self._setup_ui()
        self._draw_pitch()
        self._update_roster()

        # Keyboard Shortcuts
        self._bind_keyboard_shortcuts()

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
