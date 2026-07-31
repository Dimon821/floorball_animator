import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from PIL import Image, ImageTk
import math
import os
import json

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
        # label_moves: {label: (dx, dy)}
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
            self.app.canvas.move(sid, dx, dy)
            if "text_ids" in token:
                for tid in token["text_ids"]:
                    self.app.canvas.move(tid, dx, dy)

    def serialize(self):
        return {"type": "move_tokens", "moves": self.label_moves}

class DrawLineCommand(Command):
    def __init__(self, app, tool, x1, y1, x2, y2):
        self.app = app
        self.tool = tool
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2
        self.line_ids = []
        self.step_desc = f"{tool.capitalize()} ({int(x1)},{int(y1)} ➔ {int(x2)},{int(y2)})"
        self.drawing_data = {"tool": self.tool, "x1": self.x1, "y1": self.y1, "x2": self.x2, "y2": self.y2}

    def execute(self):
        self.line_ids = self.app.draw_tactical_line_canvas(self.tool, self.x1, self.y1, self.x2, self.y2, preview=False)
        self.app.drawings.append((self.line_ids, self.drawing_data))
        self.app.action_steps.append(self.step_desc)
        self.app.steps_listbox.insert(tk.END, self.step_desc)

    def undo(self):
        for pid in self.line_ids:
            self.app.canvas.delete(pid)
        
        # Remove from drawings list
        self.app.drawings = [d for d in self.app.drawings if d[0] != self.line_ids]
        
        # Remove from timeline
        if self.step_desc in self.app.action_steps:
            idx = self.app.action_steps.index(self.step_desc)
            self.app.action_steps.pop(idx)
            self.app.steps_listbox.delete(idx)

    def serialize(self):
        return {"type": "draw", "tool": self.tool, "x1": self.x1, "y1": self.y1, "x2": self.x2, "y2": self.y2}

class GroupCommand(Command):
    def __init__(self, app, labels, is_ungroup=False):
        self.app = app
        self.labels = set(labels)
        self.is_ungroup = is_ungroup
        self.affected_group = set()

    def execute(self):
        sids = {self.app._get_sid_by_label(l) for l in self.labels if self.app._get_sid_by_label(l)}
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
        self.root.title("Floorball Tactics App")
        self.root.geometry("1050x850")

        # State Variables
        self.width = 800
        self.height = 400
        self.tokens = {}        # {token_id: {'color': str, 'label': str, 'text_ids': list, 'shape': str, 'locked': bool}}
        self.drawings = []      # Stores tuples of ([canvas_ids], drawing_data_dict)
        self.action_steps = []  # Timeline sequence steps
        
        # Command History (Undo/Redo)
        self.undo_stack = []
        self.redo_stack = []
        
        self.selected_tokens = []      # list of selected shape ids
        self.selection_rect = None
        self.selection_start = None

        self.drag_start_positions = {} # Tracks initial coordinates when dragging starts
        self.drag_data = {"x": 0, "y": 0}
        self.active_tool = None  # None means default Select & Move mode ('pass', 'shot', 'dribble', 'run')
        self.temp_line_start = None
        self.current_preview_ids = []

        # Grid Snap Settings (Snap on release)
        self.GRID = 15
        self.grid_var = tk.BooleanVar(value=False)
        self.half_rink_var = tk.BooleanVar(value=False)

        # Grouping
        self.groups = []  # List of sets containing shape_ids

        # Color Choices
        self.color_options = ["black", "red", "blue", "green", "yellow", "white", "orange", "purple"]

        # Build UI Structure
        self._setup_ui()
        self._draw_pitch()
        self._update_roster() # Initialize default 5 attackers, 5 defenders, 1 ball

        # Keyboard Shortcuts
        self.root.bind("<Control-g>", lambda e: self.group_selected())
        self.root.bind("<Control-G>", lambda e: self.group_selected())
        self.root.bind("<Control-z>", self.undo)
        self.root.bind("<Control-Z>", self.undo)
        self.root.bind("<Control-y>", self.redo)
        self.root.bind("<Control-Y>", self.redo)

    # ---------------------------
    # COMMAND PIPELINE
    # ---------------------------
    def push_command(self, cmd, execute=True):
        if execute:
            cmd.execute()
        self.undo_stack.append(cmd)
        self.redo_stack.clear()

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

    # ---------------------------
    # MACROS
    # ---------------------------
    def save_macro(self):
        if not self.undo_stack:
            messagebox.showinfo("Empty", "No history available to save as macro.")
            return
        filepath = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON Files", "*.json")])
        if filepath:
            data = [cmd.serialize() for cmd in self.undo_stack]
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
                    self.push_command(DrawLineCommand(self, cmd_data["tool"], cmd_data["x1"], cmd_data["y1"], cmd_data["x2"], cmd_data["y2"]))
                elif ctype == "group":
                    self.push_command(GroupCommand(self, cmd_data["labels"], cmd_data.get("is_ungroup", False)))
                elif ctype == "lock":
                    self.push_command(LockCommand(self, cmd_data["labels"], cmd_data["lock_state"]))
                    
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load macro:\n{e}")


    def _setup_ui(self):
        # Main Workspace Container (Left Sidebar | Center Canvas)
        workspace = tk.Frame(self.root)
        workspace.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 1. LEFT SIDEBAR: Drawing Menu & Animation Menu
        left_sidebar = tk.Frame(workspace, width=280, bg="#e0e0e0", relief=tk.RAISED, borderwidth=1)
        left_sidebar.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))

        # Top Header: Drawing Menu
        tk.Label(left_sidebar, text="Drawing Menu", bg="#c0c0c0", font=("Arial", 11, "bold"), pady=4).pack(fill=tk.X)

        # --- SECTION: Half Rink Only (Above Roster) ---
        rink_opt_frame = tk.Frame(left_sidebar, bg="#e0e0e0", pady=3)
        rink_opt_frame.pack(fill=tk.X, padx=5, pady=2)
        tk.Checkbutton(rink_opt_frame, text="Half Rink Only", variable=self.half_rink_var, bg="#e0e0e0", command=self.redraw_canvas, font=("Arial", 9, "bold")).pack(anchor="w")

        # --- SECTION 1: Roster ---
        roster_frame = tk.LabelFrame(left_sidebar, text="Roster", bg="#e0e0e0", font=("Arial", 10, "bold"), padx=5, pady=5)
        roster_frame.pack(fill=tk.X, padx=5, pady=2)

        # Attackers (Square)
        att_row = tk.Frame(roster_frame, bg="#e0e0e0")
        att_row.pack(fill=tk.X, pady=2)
        tk.Label(att_row, text="■", bg="#e0e0e0", font=("Arial", 10, "bold"), width=3).pack(side=tk.LEFT)
        self.att_spinbox = tk.Spinbox(att_row, from_=1, to=10, width=3, command=self._update_roster)
        self.att_spinbox.delete(0, tk.END)
        self.att_spinbox.insert(0, "5")
        self.att_spinbox.pack(side=tk.LEFT, padx=2)
        self.att_spinbox.bind("<KeyRelease>", self._update_roster)
        
        self.att_color_var = tk.StringVar(value="black")
        self.att_color_menu = ttk.Combobox(att_row, textvariable=self.att_color_var, values=self.color_options, width=8, state="readonly")
        self.att_color_menu.pack(side=tk.RIGHT, padx=2)
        self.att_color_menu.bind("<<ComboboxSelected>>", lambda e: self._update_roster())

        # Defenders (Circle)
        def_row = tk.Frame(roster_frame, bg="#e0e0e0")
        def_row.pack(fill=tk.X, pady=2)
        tk.Label(def_row, text="●", bg="#e0e0e0", font=("Arial", 10, "bold"), width=3).pack(side=tk.LEFT)
        self.def_spinbox = tk.Spinbox(def_row, from_=1, to=10, width=3, command=self._update_roster)
        self.def_spinbox.delete(0, tk.END)
        self.def_spinbox.insert(0, "5")
        self.def_spinbox.pack(side=tk.LEFT, padx=2)
        self.def_spinbox.bind("<KeyRelease>", self._update_roster)

        self.def_color_var = tk.StringVar(value="black")
        self.def_color_menu = ttk.Combobox(def_row, textvariable=self.def_color_var, values=self.color_options, width=8, state="readonly")
        self.def_color_menu.pack(side=tk.RIGHT, padx=2)
        self.def_color_menu.bind("<<ComboboxSelected>>", lambda e: self._update_roster())

        # --- SECTION 2: Actions (Single Column) ---
        actions_frame = tk.LabelFrame(left_sidebar, text="Actions", bg="#e0e0e0", font=("Arial", 10, "bold"), padx=5, pady=5)
        actions_frame.pack(fill=tk.X, padx=5, pady=2)

        self.icon_pass = self._load_action_icon("img/arrows/dashed_arrow_wo_bg.png")
        self.icon_shot = self._load_action_icon("img/arrows/double_arrow_wo_bg.png")
        self.icon_dribble = self._load_action_icon("img/arrows/wiggel_arrow_wo_bg.png")
        self.icon_run = self._load_action_icon("img/arrows/standard_arrow_wo_bg.png")

        act_btn_frame = tk.Frame(actions_frame, bg="#e0e0e0")
        act_btn_frame.pack(fill=tk.X, pady=2)
        tk.Button(act_btn_frame, text="Pass", image=self.icon_pass, compound=tk.LEFT, command=lambda: self.set_tool("pass"), bg="#fff9c4", anchor="w").pack(fill=tk.X, pady=1)
        tk.Button(act_btn_frame, text="Shot", image=self.icon_shot, compound=tk.LEFT, command=lambda: self.set_tool("shot"), bg="#ffccbc", anchor="w").pack(fill=tk.X, pady=1)
        tk.Button(act_btn_frame, text="Dribble", image=self.icon_dribble, compound=tk.LEFT, command=lambda: self.set_tool("dribble"), bg="#d1c4e9", anchor="w").pack(fill=tk.X, pady=1)
        tk.Button(act_btn_frame, text="Run", image=self.icon_run, compound=tk.LEFT, command=lambda: self.set_tool("run"), bg="#c8e6c9", anchor="w").pack(fill=tk.X, pady=1)

        # --- SECTION 3: Alignment (Includes Snap to Grid) ---
        align_frame = tk.LabelFrame(left_sidebar, text="Alignment", bg="#e0e0e0", font=("Arial", 10, "bold"), padx=5, pady=5)
        align_frame.pack(fill=tk.X, padx=5, pady=2)

        row1 = tk.Frame(align_frame, bg="#e0e0e0")
        row1.pack(fill=tk.X, pady=2)
        tk.Button(row1, text="[ ⫲ ] Align H", command=lambda: self.align_tokens("horizontal"), width=10, font=("Arial", 8)).pack(side=tk.LEFT, padx=2)
        tk.Button(row1, text="[ ⫦ ] Align V", command=lambda: self.align_tokens("vertical"), width=10, font=("Arial", 8)).pack(side=tk.RIGHT, padx=2)

        row2 = tk.Frame(align_frame, bg="#e0e0e0")
        row2.pack(fill=tk.X, pady=2)
        tk.Button(row2, text="[ ⟷ ] Dist H", command=self.distribute_horizontally, width=10, font=("Arial", 8)).pack(side=tk.LEFT, padx=2)
        tk.Button(row2, text="[ ↕ ] Dist V", command=self.distribute_vertically, width=10, font=("Arial", 8)).pack(side=tk.RIGHT, padx=2)

        row3 = tk.Frame(align_frame, bg="#e0e0e0")
        row3.pack(fill=tk.X, pady=2)
        tk.Button(row3, text="Group", command=self.group_selected, width=10, font=("Arial", 8), bg="#d1c4e9").pack(side=tk.LEFT, padx=2)
        tk.Button(row3, text="Ungroup", command=self.ungroup_selected, width=10, font=("Arial", 8), bg="#ffccbc").pack(side=tk.RIGHT, padx=2)

        row4 = tk.Frame(align_frame, bg="#e0e0e0")
        row4.pack(fill=tk.X, pady=2)
        tk.Button(row4, text="Lock", command=self.lock_selected, width=10, font=("Arial", 8), bg="#ffecb3").pack(side=tk.LEFT, padx=2)
        tk.Button(row4, text="Unlock", command=self.unlock_selected, width=10, font=("Arial", 8), bg="#e0f7fa").pack(side=tk.RIGHT, padx=2)

        row5 = tk.Frame(align_frame, bg="#e0e0e0")
        row5.pack(fill=tk.X, pady=2)
        # BIND THE TOGGLE VISUALS METHOD HERE
        tk.Checkbutton(row5, text="Snap to Grid (on release)", variable=self.grid_var, bg="#e0e0e0", font=("Arial", 9), command=self.toggle_grid_visuals).pack(anchor="w")

        # --- SECTION 4: Animation Menu Header & Timeline ---
        tk.Label(left_sidebar, text="Animation Menu", bg="#c0c0c0", font=("Arial", 11, "bold"), pady=4).pack(fill=tk.X, pady=(8, 2))

        timeline_frame = tk.LabelFrame(left_sidebar, text="Timeline", bg="#e0e0e0", font=("Arial", 10, "bold"), padx=5, pady=5)
        timeline_frame.pack(fill=tk.X, padx=5, pady=2)

        self.steps_listbox = tk.Listbox(timeline_frame, font=("Arial", 9), selectmode=tk.SINGLE, height=5)
        self.steps_listbox.pack(fill=tk.BOTH, expand=True, pady=2)

        tl_btn_frame = tk.Frame(timeline_frame, bg="#e0e0e0")
        tl_btn_frame.pack(fill=tk.X, pady=2)
        tk.Button(tl_btn_frame, text="⬆️ Move Up", command=self.move_step_up, font=("Arial", 8)).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=1)
        tk.Button(tl_btn_frame, text="⬇️ Move Down", command=self.move_step_down, font=("Arial", 8)).pack(side=tk.RIGHT, expand=True, fill=tk.X, padx=1)

        # --- SECTION 5: History & Macros ---
        macro_frame = tk.LabelFrame(left_sidebar, text="History & Macros", bg="#e0e0e0", font=("Arial", 10, "bold"), padx=5, pady=5)
        macro_frame.pack(fill=tk.X, padx=5, pady=2)

        hist_btn_frame = tk.Frame(macro_frame, bg="#e0e0e0")
        hist_btn_frame.pack(fill=tk.X, pady=2)
        tk.Button(hist_btn_frame, text="↶ Undo (Ctrl+Z)", command=self.undo, font=("Arial", 8)).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=1)
        tk.Button(hist_btn_frame, text="↷ Redo (Ctrl+Y)", command=self.redo, font=("Arial", 8)).pack(side=tk.RIGHT, expand=True, fill=tk.X, padx=1)

        mac_btn_frame = tk.Frame(macro_frame, bg="#e0e0e0")
        mac_btn_frame.pack(fill=tk.X, pady=2)
        tk.Button(mac_btn_frame, text="💾 Save Macro", command=self.save_macro, font=("Arial", 8)).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=1)
        tk.Button(mac_btn_frame, text="📂 Load Macro", command=self.load_macro, font=("Arial", 8)).pack(side=tk.RIGHT, expand=True, fill=tk.X, padx=1)

        # 2. CENTER: Pitch Canvas
        self.canvas = tk.Canvas(workspace, width=self.width, height=self.height, bg="white", highlightbackground="black", highlightthickness=1)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Canvas Event Bindings
        self.canvas.bind("<ButtonPress-1>", self.on_canvas_press)
        self.canvas.bind("<B1-Motion>", self.on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_canvas_release)
        self.root.bind("<Configure>", self.on_window_resize)

    def _load_action_icon(self, filename):
        try:
            if os.path.exists(filename):
                img = Image.open(filename)
                img = img.resize((20, 16), Image.Resampling.LANCZOS)
                return ImageTk.PhotoImage(img)
        except Exception as e:
            print(f"Could not load icon {filename}: {e}")
        
        # Fallback blank icon if file is missing
        fallback = Image.new("RGBA", (20, 16), (200, 200, 200, 255))
        return ImageTk.PhotoImage(fallback)

    def set_tool(self, tool_name):
        if self.active_tool == tool_name:
            self.active_tool = None  
        else:
            self.active_tool = tool_name
        
        mode_desc = f"Active Tool: {self.active_tool.capitalize()}" if self.active_tool else "Active Mode: Select & Move"
        self.root.title(f"Floorball Tactics App - {mode_desc}")
        
    # ---------------------------
    # GRID VISUALIZATION
    # ---------------------------
    def toggle_grid_visuals(self):
        if self.grid_var.get():
            self.draw_grid_points()
        else:
            self.canvas.delete("grid_point")

    def draw_grid_points(self):
        self.canvas.delete("grid_point")
        if not self.grid_var.get():
            return
        
        w = self.width
        h = self.height
        r = 1 # Radius of the anchor point dots
        
        for x in range(0, w + self.GRID, self.GRID):
            for y in range(0, h + self.GRID, self.GRID):
                self.canvas.create_oval(x-r, y-r, x+r, y+r, fill="#d3d3d3", outline="#d3d3d3", tags=("grid_point",))
        
        # Push the grid dots behind tokens/lines, but keep the pitch lines at the absolute bottom
        self.canvas.tag_lower("grid_point")
        self.canvas.tag_lower("pitch")

    def _draw_pitch(self):
        self.canvas.delete("pitch")
        w, h = self.width, self.height
        is_half = self.half_rink_var.get()
        draw_w = w / 2 if is_half else w

        self.canvas.create_rectangle(10, 10, draw_w - 10, h - 10, outline="black", width=3, tags="pitch")

        if not is_half:
            self.canvas.create_line(w / 2, 10, w / 2, h - 10, fill="black", width=2, tags="pitch")
            self.canvas.create_oval(w / 2 - 5, h / 2 - 5, w / 2 + 5, h / 2 + 5, fill="black", tags="pitch")

        usable_half_width = (draw_w - 20) / 2.0
        scale = usable_half_width / 20.0  

        crease_depth_m = 4.0
        crease_height_m = 5.0
        backline_dist_m = 2.85
        goal_line_len_m = 1.6

        cx_left = 10 + backline_dist_m * scale
        crease_w = crease_depth_m * scale
        crease_h = crease_height_m * scale
        gl_h = goal_line_len_m * scale

        self.canvas.create_rectangle(
            cx_left, h / 2 - crease_h / 2,
            cx_left + crease_w, h / 2 + crease_h / 2,
            outline="black", width=1, tags="pitch"
        )

        self.canvas.create_line(
            cx_left, h / 2 - gl_h / 2,
            cx_left, h / 2 + gl_h / 2,
            fill="black", width=4, tags="pitch"
        )

        if not is_half:
            cx_right = w - (10 + backline_dist_m * scale)
            self.canvas.create_rectangle(
                cx_right - crease_w, h / 2 - crease_h / 2,
                cx_right, h / 2 + crease_h / 2,
                outline="black", width=1, tags="pitch"
            )
            self.canvas.create_line(
                cx_right, h / 2 - gl_h / 2,
                cx_right, h / 2 + gl_h / 2,
                fill="black", width=4, tags="pitch"
            )

    def redraw_canvas(self):
        self._draw_pitch()
        self.toggle_grid_visuals() # Ensure grid adjusts dynamically to resize or half-rink toggle

    def _update_roster(self, event=None):
        for token_id in list(self.tokens.keys()):
            data = self.tokens[token_id]
            self.canvas.delete(token_id)
            if "text_ids" in data and data["text_ids"]:
                for tid in data["text_ids"]:
                    self.canvas.delete(tid)
        self.tokens.clear()
        self.groups.clear()
        self.clear_selection()
        
        self.undo_stack.clear()
        self.redo_stack.clear()

        try:
            num_att = int(self.att_spinbox.get())
        except ValueError:
            num_att = 5
        try:
            num_def = int(self.def_spinbox.get())
        except ValueError:
            num_def = 5

        att_color = self.att_color_var.get()
        def_color = self.def_color_var.get()

        for i in range(1, num_att + 1):
            x = self.width * 0.3
            y = self.height * (i / (num_att + 1))
            self._create_token(x, y, f"A{i}", shape="square", color=att_color)

        for i in range(1, num_def + 1):
            x = self.width * 0.7
            y = self.height * (i / (num_def + 1))
            self._create_token(x, y, f"D{i}", shape="circle", color=def_color)

        self._create_token(self.width * 0.5, self.height * 0.5, "B", shape="ball", color="black")

    def _create_token(self, x, y, label, shape="circle", color="black"):
        if shape == "square":
            size = 14
            shape_id = self.canvas.create_rectangle(
                x-size, y-size, x+size, y+size,
                fill=color, outline="black", width=2,
                tags=("token",)
            )
        elif shape == "ball":
            radius = 6
            shape_id = self.canvas.create_oval(
                x-radius, y-radius, x+radius, y+radius,
                fill=color, outline="black", width=2,
                tags=("token",)
            )
        else:
            radius = 15
            shape_id = self.canvas.create_oval(
                x-radius, y-radius, x+radius, y+radius,
                fill=color, outline="black", width=2,
                tags=("token",)
            )

        text_ids = []
        if shape != "ball":
            for ox, oy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                otid = self.canvas.create_text(
                    x + ox, y + oy,
                    text=label,
                    fill="black",
                    font=("Arial", 9, "bold"),
                    tags=("token",)
                )
                text_ids.append(otid)
            
            main_otid = self.canvas.create_text(
                x, y,
                text=label,
                fill="white",
                font=("Arial", 9, "bold"),
                tags=("token",)
            )
            text_ids.append(main_otid)

        token = {
            "shape_id": shape_id,
            "text_ids": text_ids,
            "shape": shape,
            "label": label,
            "color": color,
            "locked": False,
        }

        self.tokens[shape_id] = token
        for tid in text_ids:
            self.tokens[tid] = token

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
                if dx != 0:
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
                if dy != 0:
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
                if dy != 0:
                    label_moves[t["label"]] = (0, dy)
        elif alignment_type == "vertical":
            avg_cx = sum((c[0] + c[2])/2 for c in valid_coords) / len(valid_coords)
            for t, c in zip(unique_tokens, coords_list):
                if not c: continue
                cx = (c[0] + c[2]) / 2
                dx = avg_cx - cx
                if dx != 0:
                    label_moves[t["label"]] = (dx, 0)
                    
        if label_moves:
            self.push_command(MoveTokensCommand(self, label_moves))

    def find_token_at(self, x, y):
        for token in self.tokens.values():
            sid = token["shape_id"]
            coords = self.canvas.coords(sid)
            if coords:
                x1, y1, x2, y2 = coords
                if x1 <= x <= x2 and y1 <= y <= y2:
                    return token, coords
        return None, None

    def adjust_endpoints(self, x1, y1, x2, y2):
        t1, c1 = self.find_token_at(x1, y1)
        t2, c2 = self.find_token_at(x2, y2)
        
        cx1 = (c1[0] + c1[2]) / 2 if c1 else x1
        cy1 = (c1[1] + c1[3]) / 2 if c1 else y1
        cx2 = (c2[0] + c2[2]) / 2 if c2 else x2
        cy2 = (c2[1] + c2[3]) / 2 if c2 else y2
        
        dx = cx2 - cx1
        dy = cy2 - cy1
        dist = math.hypot(dx, dy)
        if dist == 0:
            return x1, y1, x2, y2
        
        ux = dx / dist
        uy = dy / dist
        
        r1 = (c1[2] - c1[0]) / 2 if c1 else 0
        r2 = (c2[2] - c2[0]) / 2 if c2 else 0
        
        startx = cx1 + ux * r1 if t1 else x1
        starty = cy1 + uy * r1 if t1 else y1
        endx = cx2 - ux * r2 if t2 else x2
        endy = cy2 - uy * r2 if t2 else y2
        
        return startx, starty, endx, endy

    def on_canvas_press(self, event):
        if self.active_tool is None:
            if self.active_widget_is_token(event):
                clicked = self.tokens[self.canvas.find_withtag("current")[0]]["shape_id"]

                target_sids = {clicked}
                for g in self.groups:
                    if clicked in g:
                        target_sids.update(g)

                ctrl = (event.state & 0x4) != 0
                if ctrl:
                    for sid in target_sids:
                        if sid in self.selected_tokens:
                            self.selected_tokens.remove(sid)
                        else:
                            self.selected_tokens.append(sid)
                else:
                    self.selected_tokens = list(target_sids)

                self.highlight_selected()
                
                # Setup values for Drag & Drop Command processing
                self.drag_data["x"] = event.x
                self.drag_data["y"] = event.y
                self.drag_start_positions = {}
                for sid in self.selected_tokens:
                    token = self.tokens.get(sid)
                    if token:
                        self.drag_start_positions[sid] = self.canvas.coords(sid)
            else:
                self.clear_selection()
                self.selection_start = (event.x, event.y)
                self.selection_rect = self.canvas.create_rectangle(
                    event.x, event.y, event.x, event.y,
                    dash=(4,4), outline="blue"
                )
        else:
            self.temp_line_start = (event.x, event.y)
            self.current_preview_ids = []

    def on_canvas_drag(self, event):
        if self.selection_rect:
            self.canvas.coords(
                self.selection_rect,
                self.selection_start[0],
                self.selection_start[1],
                event.x,
                event.y
            )
            return

        if self.active_tool is None:
            dx = event.x - self.drag_data["x"]
            dy = event.y - self.drag_data["y"]

            moved_sids = set(self.selected_tokens)
            for sid in list(self.selected_tokens):
                for g in self.groups:
                    if sid in g:
                        moved_sids.update(g)

            for sid in moved_sids:
                token = self.tokens.get(sid)
                if token and token.get("locked", False):
                    continue
                # Physically move the items natively so the user sees live feedback
                self.canvas.move(token["shape_id"], dx, dy)
                if "text_ids" in token:
                    for tid in token["text_ids"]:
                        self.canvas.move(tid, dx, dy)

            self.drag_data["x"] = event.x
            self.drag_data["y"] = event.y

        elif self.active_tool is not None and self.temp_line_start:
            for pid in self.current_preview_ids:
                self.canvas.delete(pid)

            x1, y1 = self.temp_line_start
            x2, y2 = event.x, event.y
            adj_x1, adj_y1, adj_x2, adj_y2 = self.adjust_endpoints(x1, y1, x2, y2)

            self.current_preview_ids = self.draw_tactical_line_canvas(
                self.active_tool,
                adj_x1, adj_y1, adj_x2, adj_y2,
                preview=True
            )

    def on_canvas_release(self, event):
        if self.selection_rect:
            x1, y1 = self.selection_start
            x2, y2 = event.x, event.y

            xmin, xmax = min(x1, x2), max(x1, x2)
            ymin, ymax = min(y1, y2), max(y1, y2)

            self.canvas.delete(self.selection_rect)
            self.selection_rect = None
            self.selected_tokens.clear()

            visited = set()
            for token in self.tokens.values():
                sid = token["shape_id"]
                if sid in visited:
                    continue
                visited.add(sid)

                coords = self.canvas.coords(sid)
                if coords:
                    cx = (coords[0] + coords[2]) / 2
                    cy = (coords[1] + coords[3]) / 2
                    if xmin <= cx <= xmax and ymin <= cy <= ymax:
                        self.selected_tokens.append(sid)

            self.highlight_selected()
            return

        if self.active_tool is None:
            # Check Grid Snapping first so it gets factored into the move command
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
                            newx = round(cx / self.GRID) * self.GRID
                            newy = round(cy / self.GRID) * self.GRID
                            dx = newx - cx
                            dy = newy - cy
                            
                            self.canvas.move(token["shape_id"], dx, dy)
                            if "text_ids" in token:
                                for tid in token["text_ids"]:
                                    self.canvas.move(tid, dx, dy)

            # Compare final positions against drag_start_positions to generate a Command
            label_moves = {}
            for sid, old_coords in self.drag_start_positions.items():
                new_coords = self.canvas.coords(sid)
                if new_coords and old_coords != new_coords:
                    dx = new_coords[0] - old_coords[0]
                    dy = new_coords[1] - old_coords[1]
                    label = self.tokens[sid]["label"]
                    label_moves[label] = (dx, dy)
                    
            if label_moves:
                # We push the command but execute=False because the canvas objects 
                # are already physically at the new locations visually! 
                cmd = MoveTokensCommand(self, label_moves)
                self.push_command(cmd, execute=False)
                
            self.drag_start_positions.clear()

        if self.active_tool is not None and self.temp_line_start:
            x1, y1 = self.temp_line_start
            x2, y2 = event.x, event.y
            
            for pid in self.current_preview_ids:
                self.canvas.delete(pid)
            self.current_preview_ids = []

            adj_x1, adj_y1, adj_x2, adj_y2 = self.adjust_endpoints(x1, y1, x2, y2)
            
            # Offload drawing and history tracking entirely to the Command
            cmd = DrawLineCommand(self, self.active_tool, adj_x1, adj_y1, adj_x2, adj_y2)
            self.push_command(cmd, execute=True)
            
            self.temp_line_start = None

    def clear_selection(self):
        self.selected_tokens.clear()
        for token in self.tokens.values():
            sid = token["shape_id"]
            outline_color = "gray" if token.get("locked", False) else "black"
            self.canvas.itemconfig(sid, outline=outline_color, width=2)

    def highlight_selected(self):
        for token in self.tokens.values():
            sid = token["shape_id"]
            outline_color = "gray" if token.get("locked", False) else "black"
            self.canvas.itemconfig(sid, outline=outline_color, width=2)

        for sid in self.selected_tokens:
            token = self.tokens.get(sid)
            if token and token.get("locked", False):
                self.canvas.itemconfig(sid, outline="gray", width=4)
            else:
                self.canvas.itemconfig(sid, outline="dodgerblue", width=4)

    def draw_tactical_line_canvas(self, tool, x1, y1, x2, y2, preview=False):
        created_ids = []
        big_arrow = (16, 20, 8)

        if tool == "pass":
            lid = self.canvas.create_line(x1, y1, x2, y2, fill="black", width=2, dash=(4, 4), arrow=tk.LAST, arrowshape=big_arrow, tags=("tactic_line",))
            created_ids.append(lid)
        elif tool == "shot":
            dx = x2 - x1
            dy = y2 - y1
            dist = math.hypot(dx, dy)
            if dist > 0:
                unit_x = dx / dist
                unit_y = dy / dist
                nx = -unit_y * 3
                ny = unit_x * 3
                
                setback = 16
                x2_short = x2 - setback * unit_x
                y2_short = y2 - setback * unit_y

                l1 = self.canvas.create_line(x1 + nx, y1 + ny, x2_short + nx, y2_short + ny, fill="black", width=2, tags=("tactic_line",))
                l2 = self.canvas.create_line(x1 - nx, y1 - ny, x2_short - nx, y2_short - ny, fill="black", width=2, tags=("tactic_line",))
                
                arrow_len = 16
                arrow_width = 8
                base_x = x2 - arrow_len * unit_x
                base_y = y2 - arrow_len * unit_y
                p1_x = base_x - arrow_width * (-unit_y)
                p1_y = base_y - arrow_width * unit_x
                p2_x = base_x + arrow_width * (-unit_y)
                p2_y = base_y + arrow_width * unit_x
                arrow_id = self.canvas.create_polygon(x2, y2, p1_x, p1_y, p2_x, p2_y, fill="black", outline="black", tags=("tactic_line",))
                
                created_ids.extend([l1, l2, arrow_id])
            else:
                lid = self.canvas.create_line(x1, y1, x2, y2, fill="black", width=2, arrow=tk.LAST, arrowshape=big_arrow, tags=("tactic_line",))
                created_ids.append(lid)
        elif tool == "dribble":
            dx = x2 - x1
            dy = y2 - y1
            dist = math.hypot(dx, dy)
            if dist > 0:
                unit_x = dx / dist
                unit_y = dy / dist
                nx = -unit_y
                ny = unit_x
                amplitude = 14
                wavelength = 60
                
                end_straight_len = min(50, dist * 0.3)
                start_fade_s = dist - end_straight_len

                points = []
                step = 2
                for s in range(0, int(dist) + 1, step):
                    t_val = s / dist
                    bx = x1 + t_val * dx
                    by = y1 + t_val * dy
                    
                    if s <= start_fade_s or start_fade_s >= dist:
                        env = 1.0
                    else:
                        t_end = (s - start_fade_s) / end_straight_len
                        env = 0.5 * (1.0 + math.cos(t_end * math.pi))
                        
                    angle = (s / wavelength) * 2 * math.pi
                    offset = amplitude * env * math.sin(angle)
                    px_pt = bx + nx * offset
                    py_pt = by + ny * offset
                    points.extend([px_pt, py_pt])

                if len(points) >= 4:
                    lid = self.canvas.create_line(*points, fill="black", width=2, smooth=True, tags=("tactic_line",))
                    created_ids.append(lid)
                    
                    tx, ty = unit_x, unit_y
                    arrow_len = 16
                    arrow_width = 8
                    base_x = x2 - arrow_len * tx
                    base_y = y2 - arrow_len * ty
                    p1_x = base_x - arrow_width * (-ty)
                    p1_y = base_y - arrow_width * tx
                    p2_x = base_x + arrow_width * (-ty)
                    p2_y = base_y + arrow_width * tx
                    arrow_id = self.canvas.create_polygon(x2, y2, p1_x, p1_y, p2_x, p2_y, fill="black", outline="black", tags=("tactic_line",))
                    created_ids.append(arrow_id)
            else:
                lid = self.canvas.create_line(x1, y1, x2, y2, fill="black", width=2, arrow=tk.LAST, arrowshape=big_arrow, tags=("tactic_line",))
                created_ids.append(lid)
        elif tool == "run":
            lid = self.canvas.create_line(x1, y1, x2, y2, fill="black", width=5, arrow=tk.LAST, arrowshape=big_arrow, tags=("tactic_line",))
            created_ids.append(lid)
        return created_ids

    def active_widget_is_token(self, event):
        items = self.canvas.find_withtag("current")
        if items:
            item_id = items[0]
            return item_id in self.tokens or any(item_id in d.get("text_ids", []) for d in self.tokens.values())
        return False

    def on_window_resize(self, event):
        if event.widget == self.root:
            self.width = max(600, self.root.winfo_width() - 320)
            self.height = max(300, self.root.winfo_height() - 100)
            self.canvas.config(width=self.width, height=self.height)
            self.redraw_canvas()

    def move_step_up(self):
        selected_idx = self.steps_listbox.curselection()
        if not selected_idx or selected_idx[0] == 0:
            return
        idx = selected_idx[0]
        text = self.steps_listbox.get(idx)
        self.steps_listbox.delete(idx)
        self.steps_listbox.insert(idx - 1, text)
        self.steps_listbox.selection_set(idx - 1)
        
        self.action_steps[idx], self.action_steps[idx - 1] = self.action_steps[idx - 1], self.action_steps[idx]
        self.drawings[idx], self.drawings[idx - 1] = self.drawings[idx - 1], self.drawings[idx]

    def move_step_down(self):
        selected_idx = self.steps_listbox.curselection()
        if not selected_idx or selected_idx[0] == self.steps_listbox.size() - 1:
            return
        idx = selected_idx[0]
        text = self.steps_listbox.get(idx)
        self.steps_listbox.delete(idx)
        self.steps_listbox.insert(idx + 1, text)
        self.steps_listbox.selection_set(idx + 1)
        
        self.action_steps[idx], self.action_steps[idx + 1] = self.action_steps[idx + 1], self.action_steps[idx]
        self.drawings[idx], self.drawings[idx + 1] = self.drawings[idx + 1], self.drawings[idx]

if __name__ == "__main__":
    root = tk.Tk()
    app = FloorballTacticsApp(root)
    root.mainloop()
