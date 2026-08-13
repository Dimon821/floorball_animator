# Floorball Tactics Studio

Floorball Tactics Studio is an interactive desktop application built with Python and Tkinter for creating, editing, animating, and presenting floorball tactical diagrams. It is designed for coaches, analysts, and players who need to visualize player movement, passing sequences, offensive and defensive positioning, and complete tactical plays.

The application combines an interactive tactical board with a command-based architecture that supports undo/redo, macro recording, grouped keyframe animation, and export to GIF, video or stills.

---

## Table of Contents

- [Quick Start](#quick-start)
- [The Interface](#the-interface)
- [Features](#features)
  - [Interactive Playing Surface](#interactive-playing-surface)
  - [Player and Ball Management](#player-and-ball-management)
  - [Tactical Drawing Tools](#tactical-drawing-tools)
  - [Shapes, Text, and Images](#shapes-text-and-images)
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
- [Play File Format](#play-file-format)
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
2. Drag the players into the exact positions you want to start from. This is **group 0** — the stage the animation begins at, not something it plays through.
3. Press **Add Group** to close it. Everything you do next — moving players, drawing a pass, stamping a sign — joins the new group and will happen *at the same time* when it plays.
4. Press **Play** to watch it, or **Export** in General to write a GIF, an MP4, or a PNG of whichever groups you pick.
5. **Save** writes the whole play — board, every mark in rink metres, the watermark, and the timeline with each group's duration and start time — to one JSON file that reopens as the same play.

---

## The Interface

The toolbar is always exactly two rows of boxes, with the timeline spanning both on the right.

| Box | Contents |
|-----|----------|
| **General** | Undo, Redo, Save, Export · Load, Watermark, Reset, Prefs |
| **Board Settings** | Rink: *field*, Rink: Half, Goals, Snap Plr · Snap Ang, Snap Grd, Ghosting, Rotate |
| **Roster** | Attack: count, shape, colour · Defence: count, shape, colour · player size in its own column |
| **Tactics** | Attack: percentage, formation, Apply · Defence: percentage, formation, Apply |
| **Align & Distribute** | Align H, Align V · Dist H, Dist V · Group, Lock |
| **Shapes** | Goal, X, Ball · Square, Triangle, Plus · Text, Image, Size + colour |
| **Drawing Tools** | Select, Pass, Shot, Dribble, Run · Line, Bend, Box, Rect, Circle, Oval · Rotate Sel, Copy Style, Default, Delete · line type, thickness, colour |
| **Timeline** | The group tree, the Time slider, and Play / Pause / Stop / Add Group / Delete / Up / Down |

Every button carries a tooltip describing what it does; hover for about half a second.

A menu bar above the toolbar holds **Edit** (copy, cut, paste, select all), **View** (rink orientation), **Menu** (toolbar rows and position, Preferences) and **File** (save macro, exit).

---

## Features

### Interactive Playing Surface

- **Three fields**, named after the game they are played with and chosen with the **Rink:** button in Board Settings, which rotates through them — **5v5** → **4v4** → **3v3** → 5v5. The same choice is in Preferences as a row of radio buttons. Choosing a field also sets both teams to the number of players it is played with: **five**, **four** and **three** a side respectively. Players are added or removed where they stand, so an arrangement survives the change.
- **The half rink** is the button beside it, **Rink: Half**, and it applies to whichever field is chosen: the board is drawn from the halfway line to one goal end, at full width. It is a view rather than a field of its own, so it leaves the number of players alone, and cycling the field keeps it.

| Field | Size | Half | Goal line | Goal area | Marks in from the long side | Penalty spot | Substitution zones |
|-------|------|------|-----------|-----------|------------------------------|--------------|--------------------|
| **5v5** | 40 × 20 m | 20 × 20 m | 2.85 m | 1 × 2.5 m and 4 × 5 m | 2.85 m | — | — |
| **4v4** | 27 × 15 m | 13.5 × 15 m | 1.8 m | 0.9 × 1.9 m | 1 m | 7 m | 5 m, meeting at the centre line |
| **3v3** | 22 × 11 m | 11 × 11 m | 2.5 m | 1 × 2.5 m | 2 m | 5 m | 4 m from the centre line, 4 m long |

The goal is the same on every field: **1.6 m** between the posts, 1.15 m to the crossbar, 0.65 m deep. The 3v3 figures are taken from the *NeFUB / IFF 3v3 Rules of the Game, Edition 2025* (rules 101–104); the 4v4 small-field figures from the NeFUB small-field diagram. Only the large rink has a centre circle — the 4v4 and 3v3 fields are marked with a centre point instead. A half rink has no centre line and only the far goal; its substitution zones, which straddle the centre line, are left off with it.
- Rotate the whole rink between landscape and portrait.
- Scaled floorball markings throughout: boards with rounded corners, goal areas, goals, the centre circle where the field has one, and face-off crosses at the corner spots, on the halfway line and at the centre spot.
- Responsive canvas that resizes with the application window. Everything on the board is stored in **rink metres**, so players, drawings, and images keep their place on the rink through a resize, a rotation, or a switch to another field.
- Optional grid display (15 px) with snap-to-grid positioning.
- **The rink's own fixtures can be removed.** Click a goal, a face-off cross, the centre line, the centre circle or the boards to select it, then press Delete or use the right-click menu. Removals are remembered by name rather than by canvas item, so they survive a resize, a rotation and a switch between fields; **Undo**, **Restore Rink Features** in the right-click menu, and **Reset** all bring them back. Select All deliberately leaves them out.

### Player and Ball Management

- Configurable numbers of attacking and defending players, with per-team shape and colour.
- Player shapes: **circle, square, triangle, X, plus**. Squares and triangles are polygons rather than rectangles, which is what allows them to be rotated.
- Each player is drawn in three layers — fill, a thin dark edge, and a white ring outside it — so tokens stay legible over goal areas and on top of drawn lines.
- Selecting, moving, rotating (45° steps about the player's own centroid), grouping, and locking.
- Player size can be changed for the selection, or — with nothing selected — for every player at once. Tactical role labels survive a resize.
- Changing a team's **colour, shape or count** leaves the players exactly where they stand, with their roles and the undo history intact.
- **Ghosting** leaves a faded copy of a player where a drag began. Ghosts belong to the move that created them: one undo takes back the movement and the ghost together. Switching Ghosting off sweeps the board, and the right-click menu offers **Clear Ghosts**.
- **Snap Plr** attaches drawn line ends to nearby players and snaps the **ball** to the middle of the nearest player edge — never to the centre, because a ball on a player's middle reads as them standing on it. The snap box comes from the player's *size* rather than the box each shape happens to paint, so a circle, square, triangle, X and plus of the same size all snap identically.
- Dragging a player **releases** an arrow snapped to it — repositioning a player is not a redraw of the play. Hold **Shift** while dragging and the arrow travels with them.

### Tactical Drawing Tools

| Tool | Draws |
|------|-------|
| **Pass** | Directional arrow |
| **Shot** | Double-line directional arrow |
| **Dribble** | Sinusoidal (wavy) movement path |
| **Run** | Solid directional arrow |
| **Line** | Plain straight line |
| **Bend** | A curve — click the start, the bend, then the end |
| **Box** / **Rect** | Square outline / rectangle outline |
| **Circle** / **Oval** | Circle / oval |

- Line type (Solid, Dashed, Dotted, Pass, Shot, Dribble, Run), thickness, and colour are configurable.
- Shapes are **outline only**; nothing is filled by default, and selecting or deselecting them does not fill them in.
- **Bends stay editable.** Select a curve and an orange handle appears on its control point — drag it to reshape the arc while both ends stay exactly where you put them. A bend is drawn at the same weight as a straight line, and takes part in the animation like every other arrow: it is drawn on along its own curve, not along the straight line between its control points.
- **Any line type can be bent.** The Bend tool takes whatever type is armed, so a pass, a shot, a dribble or a run can be drawn as a curve and comes out with that type's dashes, weight and arrowhead — while staying one editable curve.
- **Snap Ang** constrains new lines to 45° angles.
- **Copy Style** picks up one player's colour and shape, then applies it to others by clicking them.
- **Select** lights up blue whenever no other tool is armed, which is the board's select-and-move mode.
- **Delete** removes the selection — players, signs, lines, labels and pictures alike, and **undo brings a deleted player back**, in their place, with the roster count following the board.
- **Everything drawn stays editable.** Select any mark and drag a corner handle to resize it: signs, text labels, pictures, boxes, circles and multi-part arrows alike. The handle follows the pointer one-for-one — Shift keeps the proportions.
- **A mark is selected whole.** Clicking one hole of a ball, one stroke of an X or one shaft of a shot picks up the entire drawing, so moving, resizing, recolouring and deleting act on the mark rather than on the piece under the pointer.
- Arrows attached to a player keep their shape when the player carries them along. Every tool draws its arrow differently — a shot is two shafts plus a separate arrowhead, a dribble is one long wave — and each point moves in proportion to how far along the arrow it lies, so the head stays a head and the far end stays put.

### Shapes, Text, and Images

- Stamp markers: **Goal, X, Ball, Square, Triangle, Plus**. The Goal sign always matches the size of the goals drawn on the rink, and turns with it. The **Ball** is a plain filled dot.
- Place **text labels** anywhere: pick the Text tool, click the board, type.
- Place **images** on the board — movable, and scalable by their corner handles, which re-render the bitmap rather than just stretching its anchor point.
- One **Size** dial and one **colour** serve the whole box. Changing the size restamps the selected signs, retypes the selected labels, and sets the size a new label is typed at.

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

Applying a four-player formation drops the team to four players; a five-player formation brings the fifth back. Slots are stored unitless — across (0 = left, 1 = right) and depth (0 = rearmost, 1 = most advanced), both measured in the team's own attacking direction — so one table serves either team, either rink orientation, and any of the three fields or their halves. House puts its defenders out by the boards rather than tucked in beside each other, which is what makes the shape read as a house.

Each player keeps its **role label** (LD, RW, T …) on the token, while its internal identity (A1…A5, D1…D5) stays fixed so undo, macros, and lookups keep working. The timeline names players the way the rink does, so a step reads `Move LD, RD` rather than `Move A1, A2`.

### Timeline and Animation

The timeline and the animation are the same thing: a tree of **groups**. A group is a set of actions that happen **at the same time** when the animation plays.

```text
▾ 0  Group 0                             2.0s
       Move RD
       Pass
▾ 1  Breakout                            1.0s
       Move LD
       Move LW
       Sign Goal
```

- Group headings are **bold**, and every group can be **collapsed and expanded**. A redraw remembers which ones you had folded.
- **Group 0 is the board as it stands.** Arranging players is setup, not choreography — the animation begins at the arrangement instead of playing back how you reached it.
- Moves, formations, arrows, signs, labels and images all join the **open** group. **Add Group** closes it and starts the next one.
- **Double-click a group** to give it a name of your own and its own time in one dialog. A name you set is never overwritten by the next action that joins the group.
- The **Time** slider retimes every group at once, and reaches **0 s** — a group at zero is an instant cut to the next one.
- A **red row** marks the group playback starts from. Click any row to move it.
- **Up / Down** and dragging a group row reorder the sequence. Dragging one of the *action* rows drops that action into whichever group you release it over.
- **Delete** removes whatever the timeline has picked: a group row takes the whole group with it, an action row takes only that action out of its group — and the arrow, sign or label it stands for off the board. With nothing picked it removes the group under the playhead. Either way the other groups keep their names.
- **Play / Pause / Stop** — playback interpolates positions in rink metres at 25 fps, so it is correct at any window size and in either orientation. Pause keeps its place; Play resumes from there; Stop rewinds to group 0.
- Drawings take part: anything from a later group stays hidden until the animation reaches it, and a line belonging to the group being entered is **drawn on** over that group's time, following its own length so a curve follows its curve.
- **An arrow snapped to a player keeps pointing at them while they run.** The attached end travels with the player through the whole group and the far end stays where the play put it. Which end is the attached one, and the line the arrow runs along, are measured from the drawing as it stands — not from the pixels the tool drew it between, which a resize, a zoom or a change of field leaves describing a line that is no longer there. On the board a plain drag still lets go of the arrow, because that is repositioning rather than choreography.
- **An arrow drawn in the same group as a move is laid out where it will finish**, and grows from its final tail position along its final path — rather than sliding across the rink while it grows, which is what happens if both of its ends are tracked live.
- **Export** walks the board through every frame and captures the canvas, so what is exported is what is on screen. One dialog covers every format:

| | Formats | What is written |
|---|---------|-----------------|
| **Moving** | GIF, MP4, WebM, AVI, MOV | The whole sequence, at 25 fps |
| **Still** | PNG, JPEG | One image per animation group you tick — a single group keeps the filename you typed, several are numbered `name_00`, `name_01`, … |

  Video needs a frame writer: **imageio** (`pip install imageio imageio-ffmpeg`) or **OpenCV** (`pip install opencv-python`), whichever is present. Without either, GIF and the stills still work and the dialog says what to install.

Playing or exporting with fewer than two groups, or with every group at zero seconds, raises a warning rather than doing nothing.

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
- Resize handles on a selection; corner drags scale the contents, including the bitmap of a placed image.
- **Right-click menus**:

| Right-click on | Menu |
|----------------|------|
| A player | Rotate 45° · Change Colour… · Copy / Paste Style · Group · Lock/Unlock · Align Horizontally / Vertically · Copy · Cut · Delete |
| A sign, line, label or image | Rotate 45° · Change Colour… · Copy · Cut · Delete |
| A goal, cross, line or the boards | Delete *(named for what it is)* · Restore Rink Features · Undo |
| Empty rink | Paste · Select All · Clear Ghosts · Restore Rink Features · Undo · Redo · Preferences… |

### Appearance

The board is signed **© Simon Wagener** in its bottom-left corner. It is drawn on the canvas rather than in the surrounding chrome, so it travels with an exported frame, and it is not a selectable board item.

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
| Nijmegen Flames | `#e8262b` | `#4c4c4e` | Club colours on the players only; arrows and marks stay black |
| Nijmegen Hot Shots | `#e8262b` | `#111111` | Club logo red on black |

The colour-blind sets come from the Okabe–Ito palette, chosen because the usual red-versus-blue board is close to the pairing those readers cannot separate. Each set also differs in brightness, not only in hue, so it survives printing in grey.

Other appearance behaviour:

- The UI font is resolved against what is actually installed, rather than requesting a Windows font and silently falling back to a bitmap face.
- Tooltips on every toolbar button, including the colour swatches, which carry no label.
- The toolbar always lays out in exactly two rows of boxes; the timeline spans both on the right and takes the leftover width.

### Undo, Redo, and Macros

The application uses a command-based architecture that records editing operations. Each operation can be undone (`Ctrl + Z`), redone (`Ctrl + Y`), serialized into a JSON macro, and replayed later.

**A saved file is the play, not a recipe for re-enacting it.** Version 3 files carry every mark in rink metres, every player's position, and the whole timeline — each group's name, its duration, the second it starts and ends at, and the positions it ends on. Opening a file rebuilds that directly instead of replaying the command log, which is why a play reopens identically whatever the window size, field or zoom. Replaying pixel coordinates is what used to scatter the arrows and collapse every group into one.

**Saving offers to tidy the recording.** The command log kept alongside the play contains things nobody ever saw: a formation replaced by another for the same team *in the same animation group*, moves of players since taken off the board, and runs of consecutive moves that are one displacement in the end. Save offers to leave those out and says how many it found. Because the log is history rather than what the file is rebuilt from, tidying cannot change the play — the check suite proves it by saving the same play both ways, reopening each, exporting both to GIF and comparing every frame.

Undo takes back everything an action produced — the movement, the ghost it left, its line in the group, and the drawing it made — as one step. Undoing one action inside a group leaves the group in place and simply removes that action from it.

---

## Workflows

### Building a play

1. Set the roster: team sizes, shapes, colours.
2. Apply a formation to each team, adjusting the percentage until the shape sits where you want it.
3. Drag players into their exact starting positions. This is group 0, the stage the animation opens on.
4. Press **Add Group**, then make the first phase happen: move the players involved, draw the pass, stamp a mark. All of it plays together.
5. Repeat for each phase. Rename and retime groups by double-clicking them.
6. **Save** to JSON, or **Export** to GIF, video or stills.

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
| `MoveTokensCommand` | Relative movement (`dx`, `dy`) for one or more tokens, whether lines attached to them travel too, any ghosts the move left behind, and the group entry it produced. |
| `ApplyTacticCommand` | A formation change: the team, the shape, the percentage, each player's new role, and the movement itself. |
| `DrawLineCommand` | Drawing coordinates together with the selected tactical tool, and the group the drawing belongs to. |
| `MoveDrawnCommand` | Movement of drawn items (signs, lines, text, images). |
| `RotateTokensCommand` | Rotation of players about their own centroid. |
| `RotateDrawnCommand` | Rotation of drawn items about the centre of the selection, with the pivot recorded so undo turns back about the same point. |
| `GroupCommand` | Creation or removal of token groups. |
| `LockCommand` | The locked state of tokens. |
| `SetWatermarkCommand` | The watermark image, its placement, and its crop/background/opacity settings. |

Commands are pushed with `execute=True` when the change has yet to happen, and `execute=False` when the canvas has already been updated live — a drag, for instance, moves the tokens frame by frame, so re-applying the delta would double it. Anything a command needs to log is written from a `record()` hook that runs either way.

### Positions in rink metres

Pixel coordinates are meaningless across a redraw, because the scale, the origin, the orientation and the field itself can all change. Every saved position is therefore converted to metres on the current field — 40 × 20, 20 × 20, 22 × 11 or 27 × 15 m — and converted back when the board is drawn. Switching fields is exactly this conversion, which is why the players stay where they belong on the rink instead of where their pixels were.

```text
landscape:  px = ox + mx·s          rotated:  px = ox + my·s
            py = oy + my·s                    py = oy + (rink_len − mx)·s
```

This is what lets a macro saved in one window size load correctly into another, and what makes animation playback correct in either orientation.

### Canvas item model

A player is not one canvas item but several: the coloured shape, a white ring, a dark edge, and up to nine text items that give the label its outline. An X or a Plus is a set of strokes. Anything that moves, rotates, scales, or deletes a player therefore acts on the whole set.

Three Tk details shape the code:

- `canvas.coords()` returns a bounding box only for ovals and rectangles. For a polygon it returns every vertex, and for a line every point. Reading it as a box is correct for a circle player and wrong for a square one, which is why the code uses `canvas.bbox()` throughout — and why snapping uses a box derived from the player's size rather than from any single item.
- Tk rectangles are axis-aligned by definition and cannot be rotated. Squares, triangles, and the Goal sign are therefore polygons.
- A canvas item's colour lives in `fill`, in `outline`, or in both, depending on the shape. Each drawn item records which of the two it actually uses, so recolouring and selection highlighting never flood an outline-only shape solid.

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
| `Delete` | Delete the selection now |
| `Backspace` | Remove a mark *in the play* — it stays on the board and goes when its group comes up |
| `Ctrl + +` / `Ctrl + -` / `Ctrl + 0` | Zoom in / out / back to the whole rink |
| Wheel / `Shift` + wheel | Scroll a zoomed board up-down / left-right |
| `Ctrl + Z` | Undo |
| `Ctrl + Y`, `Ctrl + Shift + Z` | Redo |
| `Escape` | Cancel the active tool |
| Right-click | Context menu |
| `Shift` + drag a player | Bring its attached lines along |
| `Shift` + corner drag | Scale evenly |
| Double-click a group | Rename it and set its time |

`Delete` and `Backspace` are ignored while the focus is in a text field, so editing a roster count or a tactic percentage never removes players from the rink.

**Two ways to remove a mark.** `Delete` takes it off the board there and then. `Backspace` writes the removal into the play instead: the mark stays on the rink while you work, the timeline gains a *Remove …* line in the open group, and during the animation it disappears when that group comes up. Deleting that timeline row calls the removal off. Players have no such half-state, so `Backspace` deletes them outright.

**Zoom** multiplies the scale of the same rink-metre projection, so players, drawings and images all keep their place on the rink through it. `Ctrl + 0` fits the whole thing back into the canvas and re-centres it. Zoom is also in the **View** menu.

**Scrolling** a zoomed board: the **wheel** moves it up and down, **Shift + wheel** moves it sideways. It stops at the edge of the rink, and while the whole rink already fits on screen there is nothing hidden, so the wheel does nothing.

---

## Preferences and Configuration

**Preferences** (General → Prefs, or Menu → Preferences…) covers:

- **Board** — the field (5v5 / 4v4 / 3v3), the half rink, show goals, snap players, snap angles, snap to grid, ghosting.
- **Menu** — toolbar position (top/bottom) and rows (auto/one/two).
- **Colours** — the theme picker, plus individual pickers for attackers, defenders, lines, and signs.

The dialog applies as you go, so **Cancel** puts back every setting it changed, including the colour theme; **Save & Close** keeps them.

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
  "rink_mode": "5v5",
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

**Reset** (General) clears the board back to the starting formations — drawings, signs, watermark, timeline, animation groups, undo history and clipboard all go. It asks first, because it cannot be undone: the history is part of what it clears.

---

## Project Structure

```text
.
├── floorball_studio/
│   ├── floorball_animator.py   # The whole application
│   ├── selfcheck.py            # Automated checks
│   └── to_do_list.md           # Roadmap
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
- Ghostscript — for every export, which captures the canvas through PostScript (`sudo apt install ghostscript`)
- imageio or OpenCV — only for video export (`pip install imageio imageio-ffmpeg`, or `pip install opencv-python`)

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
751/751 checks passed

Function coverage: 263/263 (100%) of the app's functions ran
```

The checks are grouped into areas:

| Area | Covers |
|------|--------|
| A. Geometry | Metre↔pixel round trips on every field, in both orientations |
| B. Players | Every shape: item tracking, rotation round-trips, complete deletion |
| C. Selection | Select all, group, lock, align, distribute, ghosts, the Delete key, undoing a player deletion |
| D. Snapping | Grid, 45° angles, ball-to-edge, identical snapping across shapes, attachment on Shift |
| E. Shapes and drawings | Every sign and tool, bend editing and weight, corner resizing of every mark, whole-mark selection, text, images, size and colour, outline-only shapes |
| F. Tactics | Every formation: roster resize, roles, timeline entry, board bounds |
| G. Undo and redo | Each command class round-trips; empty stacks are safe |
| H. Play files | The whole play out and back into a different window size: marks in metres, groups, times, attachments, pictures; tidying the record and proving both files export identical GIFs |
| I. Watermark | Histogram detection, keying, crop, opacity, layering, round trip |
| J. Toolbar | Two rows at every width, nothing squeezed below its size |
| K. Player resizing | All players, zero drift, no leftovers, labels preserved |
| L. Config and UI | Config round trip, roster in place, tooltip coverage, context menus |
| M. Mouse | Real press/drag/release: move, box-select, cut/paste, draw, stamp |
| N. Dialogs | Colour pickers, save/load, watermark, preferences and its Cancel — all stubbed |
| O. Animation | Groups, times, simultaneity, playback, attached arrows tracking their player, Backspace removals, reordering, export to every format, warnings |
| P. Rink fixtures | Selecting and deleting goals, crosses, lines and boards; survival across redraws |
| Q. Rink sizes | All three fields and their halves measured in metres: sizes, goal lines, areas, spots, substitution zones, team sizes, the field and half buttons, zoom |

It redirects the configuration file and stubs every dialog, so a run has no side effects outside a temporary directory. It exits non-zero if anything fails, and reports which functions were never entered.

---

## Play File Format

A saved play is JSON. **Version 3 is the play itself, not a recipe for re-enacting it**: where every mark sits in rink metres, every player's position, and the whole timeline with each group's duration and the second it starts at. Nothing is replayed when a file is opened, which is what makes a reopened play identical to the one that was saved — same arrows, same groups, same times — whatever the window size, field or zoom.

```json
{
    "version": 3,
    "app": "Floorball Tactics Studio",
    "saved_at": "2026-08-13T11:20:41",

    "board": {
        "rink_mode": "5v5",
        "half_rink": false,
        "rink_rotated": false,
        "hidden_pitch_parts": [],
        "players": [
            { "label": "A1", "team": "att", "position": "LD",
              "shape": "Square", "color": "#000000", "mx": 12.5, "my": 6.0 }
        ],
        "watermark": null
    },

    "drawings": [
        {
            "kind": "line",
            "points_m": [[12.5, 6.0], [18.2, 9.4]],
            "style": { "fill": "#000000", "width": "4.0", "dash": "4 4",
                       "arrow": "last", "arrowshape": "14 18 7" },
            "meta": { "type": "tactic_line", "tool": "pass",
                      "color": "#000000", "color_options": ["fill"],
                      "anim_group": 1, "anim_action": "Pass" },
            "data": { "tool": "pass", "x1_m": 12.5, "y1_m": 6.0,
                      "x2_m": 18.2, "y2_m": 9.4, "extra": {} }
        },
        {
            "kind": "image",
            "points_m": [[20.0, 10.0]],
            "style": { "anchor": "center" },
            "meta": { "type": "image", "sign_type": "image" },
            "image": { "png_base64": "iVBORw0KGgo...", "w_m": 4.0, "h_m": 2.0 }
        }
    ],

    "attachments": { "A1": { "start": [0], "end": [] } },

    "animation": {
        "fps": 25,
        "playhead": 0,
        "total_seconds": 2.0,
        "groups": [
            { "index": 0, "name": "Group 0", "starts_at": 0.0,
              "duration": 2.0, "ends_at": 0.0, "actions": ["Pass"],
              "board": { "players": [ { "label": "A1", "mx": 12.5, "my": 6.0 } ] } },
            { "index": 1, "name": "Move A1", "starts_at": 0.0,
              "duration": 0.8, "ends_at": 0.8, "actions": ["Move A1"],
              "board": { "players": [ { "label": "A1", "mx": 16.7, "my": 8.1 } ] } }
        ]
    },

    "commands": [ { "type": "move_tokens", "moves": { "A1": [45.0, -30.0] } } ]
}
```

| Section | What it holds |
|---------|---------------|
| `board` | The field, its orientation, which fixtures were removed, every player in rink metres, and the watermark |
| `drawings` | Every mark: its canvas kind, its points **in rink metres**, the exact style it is drawn with, which animation group it belongs to, and — for a picture — the image itself as base64 PNG |
| `attachments` | Which arrows are snapped to which player, by position in `drawings` |
| `animation` | Every group: name, duration, `starts_at` / `ends_at` on the play's clock, the actions in it, and the positions it ends on |
| `commands` | The command log — the history of how the play was built. Kept for reading and for `Ctrl + Z` style replay, but **nothing about reopening a file depends on it** |

Because the file is the play rather than a recipe, the **Tidy the recording** option at save time cannot change what comes back: it shortens `commands` only. The check suite proves this by saving the same play tidied and untidied, reopening both, exporting each to GIF, and comparing every frame.

Version 1 files (a bare list of commands) and version 2 files (commands plus a board snapshot) still load; they are replayed as before.

### Command types in the log

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

**The exported GIF or video has no watermark or placed images.**
Tk's canvas-to-PostScript export covers shapes and text only, not image items. The dialog says so when the export finishes.

**Playing the animation shows the setup moves.**
It should not: group 0 holds the board as it stands, so arranging the players before pressing **Add Group** is setup rather than choreography. If a move is animating that you meant as setup, it joined a later group — drag its row into group 0, or undo it and make it before the group was added.

**The file picker shows no images even though the folder has some.**
Fixed in current versions: the filter used semicolon-separated patterns, which is a Windows convention. On X11 that is one literal glob and matches nothing. Extensions are now separate patterns, with upper-case twins, because X11 globs are case-sensitive.

**The interface renders in blocky, terminal-like type.**
Also fixed: every widget used to request "Segoe UI", which exists only on Windows; elsewhere Tk silently substituted the `fixed` bitmap font. The font is now resolved against what is installed.

**The window will not get smaller.**
That is deliberate. The minimum is 1400 × 700, below which the toolbar cannot hold its two rows without squeezing a box out of view.

---

## Known Limitations

- Animation groups are **not** saved into macro files. A saved tactic reopens with its board, drawings and watermark, but the sequence has to be rebuilt.
- Exports do not include the watermark or placed images (see Troubleshooting).
- Video export needs imageio or OpenCV installed; GIF and still export do not.
- Moving an action into another group moves its row and its drawing, but not the board positions: a group's snapshot is the end state of everything in it, so a move's effect on player positions stays with the group it was recorded in.
- The toolbar cannot dock to the left or right; only top and bottom.

---

## License

This project is distributed under the MIT License. See the `LICENSE.txt` file for additional information.
