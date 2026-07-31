# Floorball Tactics Board

Floorball Tactics Board is an interactive desktop application built with Python and Tkinter for creating, editing, and presenting floorball tactical diagrams. It is designed for coaches, analysts, and players who need to visualize player movement, passing sequences, offensive and defensive positioning, and complete tactical plays.

The application combines an interactive tactical board with a command-based architecture that supports undo/redo functionality, macro recording, and reproducible play sequences.

---

## Features

### Interactive Playing Surface

- Toggle between full-rink and half-rink views.
- Scaled floorball markings, including goals, crease, center line, and face-off points.
- Responsive canvas that resizes with the application window.
- Optional grid display with snap-to-grid positioning for accurate layouts.

### Player and Ball Management

- Configurable numbers of attacking and defending players.
- Custom team colors.
- Individual player tokens and ball placement.
- Support for selecting, moving, grouping, and locking tokens.
- Tactical drawings automatically connect to token boundaries instead of token centers.

### Tactical Drawing Tools

The application includes several drawing tools for representing common tactical actions:

- **Pass** — dashed directional arrow.
- **Shot** — double-line directional arrow.
- **Dribble** — sinusoidal (wavy) movement path.
- **Run** — solid directional arrow.

### Layout and Alignment

- Multi-selection using box selection or `Ctrl + Click`.
- Horizontal alignment.
- Vertical alignment.
- Equal horizontal distribution.
- Equal vertical distribution.
- Group and ungroup selected tokens.

### Undo, Redo, and Macros

The application uses a command-based architecture that records all editing operations.

Supported operations include:

- Token movement
- Tactical drawing creation
- Token grouping
- Token locking

Each operation can be:

- Undone (`Ctrl + Z`)
- Redone (`Ctrl + Y`)
- Serialized into JSON macros
- Replayed later as part of a tactical sequence

Timeline entries can also be reordered to modify the execution sequence of recorded plays.

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
       +--------------------+--------------------+--------------------+
       |                    |                    |                    |
+-------------+      +-------------+      +-------------+      +-------------+
| MoveTokens  |      | DrawLine    |      | Group       |      | Lock        |
| Command     |      | Command     |      | Command     |      | Command     |
+-------------+      +-------------+      +-------------+      +-------------+
```

### Command Types

#### MoveTokensCommand

Records relative movement (`dx`, `dy`) for one or more player tokens.

#### DrawLineCommand

Stores drawing coordinates together with the selected tactical tool (`pass`, `shot`, `dribble`, or `run`).

#### GroupCommand

Creates or removes token groups while preserving group membership information.

#### LockCommand

Stores and restores the locked state of player tokens.

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl + G` | Group selected tokens |
| `Ctrl + Shift + G` | Ungroup selected tokens |
| `Ctrl + Click` | Multi-select tokens |
| Drag Selection Box | Select multiple tokens |
| `Ctrl + Z` | Undo |
| `Ctrl + Y` | Redo |

---

## Project Structure

```text
.
├── main.py                # Main application and command implementation
├── README.md
└── img/
    └── arrows/
        ├── dashed_arrow_wo_bg.png
        ├── double_arrow_wo_bg.png
        ├── wiggel_arrow_wo_bg.png
        └── standard_arrow_wo_bg.png
```

---

## Installation

### Requirements

- Python 3.8 or newer
- Pillow

Install the required dependency:

```bash
pip install Pillow
```

Clone the repository:

```bash
git clone https://github.com/your-username/floorball-tactics-board.git
cd floorball-tactics-board
```

Verify that the following image assets are present:

```text
img/arrows/
├── dashed_arrow_wo_bg.png
├── double_arrow_wo_bg.png
├── wiggel_arrow_wo_bg.png
└── standard_arrow_wo_bg.png
```

Run the application:

```bash
python main.py
```

---

## Macro File Format

Recorded tactical sequences are stored as JSON. Each editing action is serialized as a command object.

Example:

```json
[
    {
        "type": "move_tokens",
        "moves": {
            "A1": [45.0, -30.0],
            "B": [90.0, 15.0]
        }
    },
    {
        "type": "draw",
        "tool": "pass",
        "x1": 240.0,
        "y1": 120.0,
        "x2": 330.0,
        "y2": 135.0
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
    }
]
```

This format enables tactical plays to be saved, shared, edited, and replayed while preserving the original sequence of operations.

---

## License

This project is distributed under the MIT License. See the `LICENSE` file for additional information.
