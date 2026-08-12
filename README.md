# Floorball Tactics Studio

Floorball Tactics Studio is an interactive desktop application built with Python and Tkinter for creating, editing, animating, and presenting floorball tactical diagrams. It is designed for coaches, analysts, and players who need to visualize player movement, passing sequences, offensive and defensive positioning, and complete tactical plays.

The application combines an interactive tactical board with a command-based architecture that supports undo/redo, macro recording, keyframe animation, and animated GIF export.

---

## Table of Contents

- [Quick Start](#quick-start)
- [The Interface](#the-interface)
- [Features](#features)
  - [Interactive Playing Surface](#interactive-playing-surface)
  - [Player and Ball Management](#player-and-ball-management)
  - [Tactical Drawing Tools](#tactical-drawing-tools)
  - [Signs, Text, and Images](#signs-text-and-images)
  - [Tactics](#tactics)
  - [Timeline and Animation](#timeline-and-animation)
  - [Watermark](#watermark)
  - [Layout and Alignment](#layout-and-alignment)
  - [Appearance](#appearance)
  - [Undo, Redo, and Macros](#undo-redo-and-macros)
- [Workflows](#workflows)
- [Software Architecture](#software-architecture)
- [Keyboard Shortcuts](#keyboard-shortcuts)
- [Preferences and Configuration](#preferences-and-configuration)
- [Project Structure](#project-structure)
- [Installation](#installation)
- [Running the Checks](#running-the-checks)
- [Macro File Format](#macro-file-format)
- [Troubleshooting](#troubleshooting)
- [Known Limitations](#known-limitations)
- [License](#license)

---

## Quick Start

```bash
pip install Pillow
python3 floorball_studio/floorball_animator.py
```

Then, to build your first play:

1. Pick a formation for each team in **Tactics**, set the percentage, press **Apply**.
2. Drag a player where you want them. The drag is recorded in the timeline and becomes an animation step.
3. Draw a pass with the **Pass** tool — with **Snap Plr** on, the ends attach to the players.
4. Press **Play** in the timeline to watch it, or **Export** in General to write a GIF.
5. **Save** writes the whole thing — board, drawings, watermark, command log — to one JSON file.

---

## The Interface

The toolbar is always exactly two rows of boxes, with the timeline spanning both on the right.

| Box | Contents |
|-----|----------|
| **General** | Undo, Redo, Save, Export · Load, Watermark, Reset, Prefs |
| **Board Settings** | Full/Half, Arches, Goals, Snap Plr · Snap Ang, Snap Grd, Ghosting, Rotate |
| **Roster** | Attack count, shape, colour, player size · Defence count, shape, colour |
| **Tactics** | Attack: percentage, formation, Apply · Defence: percentage, formation, Apply |
| **Align & Distribute** | Align H, Align V · Dist H, Dist V · Group, Lock |
| **Signs** | Sign size and colour · Goal, X, Ball · Square, Triangle, Plus · Text, Image, text size |
| **Drawing Tools** | Select, Pass, Shot, Dribble, Run · Line, Bend, Box, Rect, Circle, Oval · Rotate Sel, Copy Style, Default, Delete · line type, thickness, colour |
| **Timeline** | The step list, and Add Step / Play / Pause / Stop / Up / Down / Del Step, with the Time slider |

Every button carries a tooltip describing what it does; hover for about half a second.

A menu bar above the toolbar holds **Edit** (copy, cut, paste, select all), **View** (rink orientation), **Menu** (toolbar rows and position, Preferences) and **File** (save macro, exit).

---

## Features

### Interactive Playing Surface

- Toggle between full-rink (40 × 20 m) and half-rink (20 × 20 m) views.
- Rotate the whole rink between landscape and portrait.
- Scaled floorball markings: boards with rounded corners, goal areas, goals, centre circle, and face-off crosses at the four corners, on the half line, and at the centre spot — all inset from the boards by the same distance as the goal line.
- Responsive canvas that resizes with the application window. Everything on the board is stored in **rink metres**, so players, drawings, and images keep their place on the rink through a resize, a rotation, or a switch to the half rink.
- Optional grid display (15 px) with snap-to-grid positioning.

### Player and Ball Management

- Configurable numbers of attacking and defending players, with per-team shape and colour.
- Player shapes: **circle, square, triangle, X, plus**. Squares and triangles are polygons rather than rectangles, which is what allows them to be rotated.
- Each player is drawn in three layers — fill, a thin dark edge, and a white ring outside it — so tokens stay legible over goal areas and on top of drawn lines.
- Selecting, moving, rotating (45° steps about the player's own centroid), grouping, and locking.
- Player size can be changed for the selection, or — with nothing selected — for every player at once. Tactical role labels survive a resize.
- **Ghosting** leaves a faded copy of a player where a drag began. Ghosts belong to the move that created them: one undo takes back the movement and the ghost together. Switching Ghosting off sweeps the board, and the right-click menu offers **Clear Ghosts**.
- **Snap Plr** attaches drawn line ends to nearby players, and snaps the **ball** to the middle of the nearest player edge — never to the centre, because a ball on a player's middle reads as them standing on it. The catch radius grows with the player, so large tokens are no harder to hit than small ones.

### Tactical Drawing Tools

| Tool | Draws |
|------|-------|
| **Pass** | Directional arrow |
| **Shot** | Double-line directional arrow |
| **Dribble** | Sinusoidal (wavy) movement path |
| **Run** | Solid directional arrow |
| **Line** | Plain straight line |
| **Bend** | A curve — click the start, the bend, then the end |
| **Box** / **Rect** | Filled box / rectangle outline |
| **Circle** / **Oval** | Circle / oval |

- Line type (Solid, Dashed, Dotted, Pass, Shot, Dribble, Run), thickness, and colour are configurable.
- **Bends stay editable.** Select a curve and an orange handle appears on its control point — drag it to reshape the arc while both ends stay exactly where you put them. With Arches on, a bend is drawn as two offset curves and both halves move together.
- **Snap Ang** constrains new lines to 45° angles.
- **Copy Style** picks up one player's colour and shape, then applies it to others by clicking them.

### Signs, Text, and Images

- Stamp markers: **Goal, X, Ball, Square, Triangle, Plus**. The Goal sign always matches the size of the goals drawn on the rink, and turns with it. The Ball is drawn as a floorball — a body with three holes, which keep the rink's colour rather than the sign's.
- Place **text labels** anywhere: pick the Text tool, click the board, type.
- Place **images** on the board — movable, and scalable by their corner handles, which re-render the bitmap rather than just stretching its anchor point.
- One **Size** field and one **colour** apply to both signs and text. Changing the size restamps the selected signs and retypes the selected labels; the separate Txt field remains for text-only adjustment (6–96 pt).

### Tactics

Two formation pickers — one for attackers, one for defenders — each with a percentage that sets how far up the rink the shape sits (0 % = own goal, 100 % = opponent goal).

| Formation | Roles | Players |
|-----------|-------|---------|
| Dice | LD, RD, C, LA, RA | 5 |
| House | LD, RD, LW, RW, T | 5 |
| Point | P, LW, RW, LA, RA | 5 |
| Umbrella | P, LW, RW, C, T | 5 |
| Square | LD, RD, LA, RA | 4 |
| Diamond | P, LW, RW, T | 4 |

Applying a four-player formation drops the team to four players; a five-player formation brings the fifth back. Slots are stored unitless — across (0 = left, 1 = right) and depth (0 = rearmost, 1 = most advanced), both measured in the team's own attacking direction — so one table serves either team, either rink orientation, and the half rink.

Each player keeps its **role label** (LD, RW, T …) on the token, while its internal identity (A1…A5, D1…D5) stays fixed so undo, macros, and lookups keep working.

### Timeline and Animation

The timeline and the animation are the same list. Every action that changes the board — a drag, a formation, a drawing — becomes a step, named after the action that made it and carrying its own time interval:

```text
0  Start                                    2.0s
1  Move A1 (+60,+30)                        2.0s
2  Attack Umbrella 60% [P, LW, RW, C, T]    2.0s
3  Step 3                                   2.0s
```

- **Add Step** freezes the board as a keyframe. The first one also records the opening slide, step 0, so there is always something to move *from*.
- **Play / Pause / Stop** — playback interpolates positions in rink metres at 25 fps, so it is correct at any window size and in either orientation. Pause keeps its place; Play resumes from there; Stop rewinds to the opening slide.
- A **red row** marks the step playback starts from. Click any step to move it.
- **Up / Down** and drag-and-drop reorder steps, carrying each step's board with it and renumbering from the top.
- **Double-click** a step to give it its own duration; the **Time** slider retimes them all.
- **Del Step** removes the step under the playhead.
- **Export** writes the sequence to an animated GIF by walking the board through every frame and capturing the canvas, so what is exported is what is on screen.

Playing or exporting without at least two steps, or with a step whose time is zero, raises a warning rather than doing nothing.

### Watermark

Load a club logo onto the rink, then place it in a preview dialog that draws the rink using the same projection as the board, so where you drop the logo is where it lands:

- **Drag** to move; drag a **corner** to resize; hold **Shift** to scale evenly.
- **Crop** to a region of the image — crops compose, and the kept region stays where you drew it.
- **Remove background** — the dominant colour is found from the luminance histogram and classified as light or dark; a slider controls how far into neighbouring shades the removal reaches. The cutoff is anchored on the detected peak rather than on pure white or black, so an off-white card is caught at the lowest setting. Existing transparency is preserved.
- **Opacity** slider, applied last so a removed background stays removed rather than fading back in.
- **Behind everything** — puts the logo under the rink markings and the players, but above the rink surface (which is opaque, so anything below it is invisible).
- **Reset Image**, **Remove**, **Cancel**, **Apply**.

The image travels inside the macro file as lossless base64 PNG, so a saved tactic carries its logo to another machine. Working copies are capped at 1200 px on the longest side. If a macro's logo cannot be found — no embedded copy and no file at the saved path — the application offers to substitute another image, which inherits the saved placement and settings.

### Layout and Alignment

- Multi-selection using box selection or `Ctrl + Click`.
- Horizontal and vertical alignment; equal horizontal and vertical distribution.
- Group and ungroup; lock and unlock. Locked players cannot be moved, resized, or deleted.
- Rotate the selection in 45° steps — players and signs alike.
- Resize handles on a selection; corner drags scale the contents.
- **Right-click menus**:

| Right-click on | Menu |
|----------------|------|
| A player | Rotate 45° · Change Colour… · Copy / Paste Style · Group · Lock/Unlock · Align Horizontally / Vertically · Copy · Cut · Delete |
| A sign, line, label or image | Rotate 45° · Change Colour… · Copy · Cut · Delete |
| Empty rink | Paste · Select All · Clear Ghosts · Undo · Redo · Preferences… |

### Appearance

**Colour themes** in Preferences apply to the whole board at once — attackers, defenders, lines, and signs — and repaint what is already there, not only what comes next.

| Theme | Attack | Defence | Notes |
|-------|--------|---------|-------|
| Classic (black) | `#000000` | `#000000` | Everything in black, as on a printed sheet |
| Red vs Blue | `#c92a2a` | `#1864ab` | The familiar two-team look |
| Blue vs Green | `#1864ab` | `#2b8a3e` | Cooler pairing, still high contrast |
| Slate vs Amber | `#343a40` | `#e8590c` | Muted, easy on the eye |
| Colour-blind: Blue / Orange | `#0072b2` | `#e69f00` | Okabe–Ito, safe for all red-green types |
| Colour-blind: Blue / Vermillion | `#0072b2` | `#d55e00` | Okabe–Ito, strong brightness separation |
| Colour-blind: Teal / Magenta | `#009e73` | `#cc79a7` | Okabe–Ito, also blue-yellow safe |
| Nijmegen Flames | `#e8262b` | `#4c4c4e` | Club crest red and slate, yellow marks |
| Nijmegen Hot Shots | `#e8262b` | `#111111` | Club logo red on black |

The colour-blind sets come from the Okabe–Ito palette, chosen because the usual red-versus-blue board is close to the pairing those readers cannot separate. Each set also differs in brightness, not only in hue, so it survives printing in grey.

Other appearance behaviour:

- The UI font is resolved against what is actually installed, rather than requesting a Windows font and silently falling back to a bitmap face.
- Tooltips on every toolbar button, including the colour swatches, which carry no label.
- The toolbar always lays out in exactly two rows of boxes; the timeline spans both on the right and takes the leftover width.

### Undo, Redo, and Macros

The application uses a command-based architecture that records editing operations. Each operation can be undone (`Ctrl + Z`), redone (`Ctrl + Y`), serialized into a JSON macro, and replayed later.

Undo takes back everything an action produced — the movement, the ghost it left, the timeline line, and the animation keyframe — as one step.

---

## Workflows

### Building a play

1. Set the roster: team sizes, shapes, colours.
2. Apply a formation to each team, adjusting the percentage until the shape sits where you want it.
3. Drag players into their exact positions. Each drag becomes a timeline step and an animation keyframe.
4. Draw passes, shots, runs and dribbles between them.
5. Reorder or retime steps in the timeline until the sequence plays the way you coach it.
6. **Save** to JSON, or **Export** to GIF.

### Reusing a play

**Load** replays the command log and then applies the board snapshot, so the players end up exactly where they were — even if the window is now a different size, or the rink is rotated the other way.

### Presenting

Set a **colour theme** the room can read, load a **watermark** for the club, and use **Play** with the rink rotated to whichever orientation suits the screen.

---

## Software Architecture

The application follows the **Command Pattern**, allowing editing operations to be treated as independent commands with execution, undo, and serialization capabilities.

```text
                        +-------------------+
                        |      Command      |
                        +-------------------+
                        | execute()         |
                        | undo()            |
                        | serialize()       |
                        +---------+---------+
                                  |
   +---------------+--------------+--------------+---------------+
   |               |              |              |               |
+-----------+ +-----------+ +-----------+ +-------------+ +--------------+
| MoveTokens| | DrawLine  | | Group     | | ApplyTactic | | SetWatermark |
| Command   | | Command   | | Command   | | Command     | | Command      |
+-----------+ +-----------+ +-----------+ +-------------+ +--------------+
| RotateTok | | MoveDrawn | | Lock      | | RotateDrawn |
| Command   | | Command   | | Command   | | Command     |
+-----------+ +-----------+ +-----------+ +-------------+
```

### Command Types

| Command | Records |
|---------|---------|
| `MoveTokensCommand` | Relative movement (`dx`, `dy`) for one or more tokens, plus any ghosts the move left behind and the timeline/animation step it produced. |
| `ApplyTacticCommand` | A formation change: the team, the shape, the percentage, each player's new role, and the movement itself. |
| `DrawLineCommand` | Drawing coordinates together with the selected tactical tool. |
| `MoveDrawnCommand` | Movement of drawn items (signs, lines, text, images). |
| `RotateTokensCommand` | Rotation of players about their own centroid. |
| `RotateDrawnCommand` | Rotation of drawn items about the centre of the selection. |
| `GroupCommand` | Creation or removal of token groups. |
| `LockCommand` | The locked state of tokens. |
| `SetWatermarkCommand` | The watermark image, its placement, and its crop/background/opacity settings. |

Commands are pushed with `execute=True` when the change has yet to happen, and `execute=False` when the canvas has already been updated live — a drag, for instance, moves the tokens frame by frame, so re-applying the delta would double it. Anything a command needs to log is written from a `record()` hook that runs either way.

### Positions in rink metres

Pixel coordinates are meaningless across a redraw, because the scale, the origin, and the orientation can all change. Every saved position is therefore converted to metres on a 40 × 20 m rink (20 × 20 for the half rink) and converted back when the board is drawn.

```text
landscape:  px = ox + mx·s          rotated:  px = ox + my·s
            py = oy + my·s                    py = oy + (rink_len − mx)·s
```

This is what lets a macro saved in one window size load correctly into another, and what makes animation playback correct in either orientation.

### Canvas item model

A player is not one canvas item but several: the coloured shape, a white ring, a dark edge, and up to nine text items that give the label its outline. An X or a Plus is a set of strokes. Anything that moves, rotates, scales, or deletes a player therefore acts on the whole set.

Two Tk details shape the code:

- `canvas.coords()` returns a bounding box only for ovals and rectangles. For a polygon it returns every vertex, and for a line every point. Reading it as a box is correct for a circle player and wrong for a square one, which is why the code uses `canvas.bbox()` throughout.
- Tk rectangles are axis-aligned by definition and cannot be rotated. Squares, triangles, and the Goal sign are therefore polygons.

### Toolbar layout engine

Sections are measured, then partitioned into rows by a dynamic-programming split that minimises the *widest* row — a greedy running total front-loads the early rows and dumps the remainder into the last one. Two rows is a guarantee rather than a preference, which is why the window has a minimum size: the bar cannot shed a row to cope with a narrow window.

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl + G` | Group selected tokens |
| `Ctrl + Shift + G` | Ungroup selected tokens |
| `Ctrl + Click` | Multi-select |
| Drag selection box | Select multiple items |
| `Ctrl + A` | Select all |
| `Ctrl + C` / `Ctrl + X` / `Ctrl + V` | Copy / Cut / Paste |
| `Delete` / `Backspace` | Delete the selection |
| `Ctrl + Z` | Undo |
| `Ctrl + Y`, `Ctrl + Shift + Z` | Redo |
| `Escape` | Cancel the active tool |
| Right-click | Context menu |
| `Shift` + corner drag | Scale evenly |
| Double-click a step | Set that step's own duration |

`Delete` and `Backspace` are ignored while the focus is in a text field, so editing a roster count or a tactic percentage never removes players from the rink.

---

## Preferences and Configuration

**Preferences** (General → Prefs, or Menu → Preferences…) covers:

- **Board** — half rink, curved arches, show goals, snap players, snap angles, snap to grid, ghosting.
- **Menu** — toolbar position (top/bottom) and rows (auto/one/two).
- **Colours** — the theme picker, plus individual pickers for attackers, defenders, lines, and signs.

Settings are written to `~/.floorball_tactics_config.json` as they change:

```json
{
  "line_color": "#000000",
  "line_thick": 2,
  "line_type": "Solid",
  "att_color": "#000000",
  "def_color": "#000000",
  "sign_color": "#000000",
  "att_tactic": "House",
  "def_tactic": "Dice",
  "att_pct": "70",
  "def_pct": "60",
  "half_rink": false,
  "grid": false,
  "snap_player": true,
  "snap_angle": false,
  "ghosting": false,
  "menu_rows_mode": "two",
  "menu_position": "top",
  "rink_rotated": false,
  "color_theme": "Classic (black)"
}
```

Only the theme's *name* is stored; the individual colours are saved alongside it, so a theme that a later version no longer ships falls back cleanly.

**Reset** (General) clears the board back to the starting formations — drawings, signs, watermark, timeline, animation steps, undo history and clipboard all go. It asks first, because it cannot be undone: the history is part of what it clears.

---

## Project Structure

```text
.
├── floorball_studio/
│   ├── floorball_animator.py   # The whole application
│   └── selfcheck.py            # Automated checks
├── start.sh
├── README.md
└── LICENSE.txt
```

The application is deliberately a single module. Everything shares one canvas and one coordinate system, and the earlier split into `app` / `canvas` / `geometry` / `alignment` modules mostly moved that shared state across file boundaries.

---

## Installation

### Requirements

- Python 3.8 or newer
- Tkinter (included with most Python distributions; on Debian/Ubuntu, `sudo apt install python3-tk`)
- Pillow
- Ghostscript — only for GIF export, which captures the canvas through PostScript (`sudo apt install ghostscript`)

Install the Python dependency:

```bash
pip install Pillow
```

Clone the repository and run the application:

```bash
git clone https://github.com/your-username/floorball-tactics-studio.git
cd floorball-tactics-studio
python3 floorball_studio/floorball_animator.py
```

The window opens at 1500 × 900 and will not go below 1400 × 700 — the toolbar is pinned to two rows and cannot fit in less.

---

## Running the Checks

`selfcheck.py` exercises the application headlessly and prints a pass/fail line per check, followed by how much of the code the run touched:

```bash
python3 floorball_studio/selfcheck.py
```

```text
423/423 checks passed

Function coverage: 178/178 (100%) of the app's functions ran
```

The checks are grouped into areas:

| Area | Covers |
|------|--------|
| A. Geometry | Metre↔pixel round trips in all four rink modes |
| B. Players | Every shape: item tracking, rotation round-trips, complete deletion |
| C. Selection | Select all, group, lock, align, distribute, the Delete key |
| D. Snapping | Grid, 45° angles, ball-to-edge, and the no-ops when switched off |
| E. Signs and drawings | Every sign and tool, bend editing, text, images, size and colour |
| F. Tactics | Every formation: roster resize, roles, timeline entry, board bounds |
| G. Undo and redo | Each command class round-trips; empty stacks are safe |
| H. Macros | Board snapshots survive loading into a different window size |
| I. Watermark | Histogram detection, keying, crop, opacity, layering, round trip |
| J. Toolbar | Two rows at every width, nothing squeezed below its size |
| K. Player resizing | All players, zero drift, no leftovers, labels preserved |
| L. Config and UI | Config round trip, tooltip coverage, context menus |
| M. Mouse | Real press/drag/release: move, box-select, cut/paste, draw, stamp |
| N. Dialogs | Colour pickers, save/load, watermark, preferences — all stubbed |
| O. Animation | Steps, times, playback, reordering, GIF export, warnings |

It redirects the configuration file and stubs every dialog, so a run has no side effects outside a temporary directory. It exits non-zero if anything fails, and reports which functions were never entered.

---

## Macro File Format

Recorded tactical sequences are stored as JSON. Version 2 files pair the command log with a snapshot of where everything actually stands, in rink metres.

```json
{
    "version": 2,
    "commands": [
        {
            "type": "move_tokens",
            "moves": { "A1": [45.0, -30.0] }
        },
        {
            "type": "tactic",
            "team": "att",
            "formation": "House",
            "percent": 70,
            "moves": { "A1": [12.0, -8.0] },
            "positions": { "A1": "LD" }
        },
        {
            "type": "draw",
            "tool": "pass",
            "x1": 240.0, "y1": 120.0,
            "x2": 330.0, "y2": 135.0,
            "extra": {}
        },
        {
            "type": "rotate_tokens",
            "labels": ["A1"],
            "degrees": 45
        },
        {
            "type": "group",
            "labels": ["A1", "A2"],
            "is_ungroup": false
        },
        {
            "type": "lock",
            "labels": ["D1"],
            "lock_state": true
        },
        {
            "type": "watermark",
            "watermark": {
                "mx": 20.0, "my": 10.0, "w_m": 12.0, "h_m": 6.0,
                "crop": null,
                "bg_tolerance": 12, "bg_mode": "light",
                "behind": true, "opacity": 100,
                "path": "club_logo.png",
                "image_format": "PNG",
                "png_base64": "iVBORw0KGgo..."
            }
        }
    ],
    "board": {
        "half_rink": false,
        "rink_rotated": false,
        "players": [
            { "label": "A1", "team": "att", "position": "LD",
              "shape": "Square", "color": "#000000", "mx": 12.5, "my": 6.0 }
        ],
        "watermark": null
    }
}
```

### Command types in a macro

| `type` | Fields |
|--------|--------|
| `move_tokens` | `moves` — label → `[dx, dy]` in pixels |
| `tactic` | `team`, `formation`, `percent`, `moves`, `positions` |
| `draw` | `tool`, `x1`, `y1`, `x2`, `y2`, `extra` (bend control point) |
| `move_drawn` | `moves` — canvas id → `[dx, dy]` |
| `rotate_tokens` | `labels`, `degrees` |
| `group` | `labels`, `is_ungroup` |
| `lock` | `labels`, `lock_state` |
| `watermark` | the full watermark block, image included |

The command log preserves the sequence of operations for replay; the board snapshot guarantees that the final positions are reproduced exactly, whatever size the window was when the file was written. The watermark image is written once — later edits supersede earlier ones, and the board's copy is dropped when a command already carries it.

Version 1 files — a bare list of commands, with no board snapshot — still load.

---

## Troubleshooting

**The GIF export says the board could not be captured.**
Ghostscript is missing. Tk cannot hand over a bitmap of a canvas directly, so export goes through PostScript, which Pillow renders with `gs`.

**The exported GIF has no watermark or placed images.**
Tk's canvas-to-PostScript export covers shapes and text only, not image items. The dialog says so when the export finishes.

**The file picker shows no images even though the folder has some.**
Fixed in current versions: the filter used semicolon-separated patterns, which is a Windows convention. On X11 that is one literal glob and matches nothing. Extensions are now separate patterns, with upper-case twins, because X11 globs are case-sensitive.

**The interface renders in blocky, terminal-like type.**
Also fixed: every widget used to request "Segoe UI", which exists only on Windows; elsewhere Tk silently substituted the `fixed` bitmap font. The font is now resolved against what is installed.

**The window will not get smaller.**
That is deliberate. The minimum is 1400 × 700, below which the toolbar cannot hold its two rows without squeezing a box out of view.

---

## Known Limitations

- Animation steps are **not** saved into macro files. A saved tactic reopens with its board, drawings and watermark, but the animation sequence has to be rebuilt.
- GIF export does not include watermark or placed images (see Troubleshooting).
- Every drag adds an animation step. Use **Del Step** or drag-reorder to prune a sequence built from many small adjustments.
- The toolbar cannot dock to the left or right; only top and bottom.

---

## License

This project is distributed under the MIT License. See the `LICENSE.txt` file for additional information.
