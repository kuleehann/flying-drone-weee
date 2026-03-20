"""
gui.py — Live 10x10 Drone Mission Grid GUI

Reads world_state.json every 500 ms and renders:
  • Zone boundaries (colour-coded per drone)
  • Scanned cells (light fill)
  • Drone positions (coloured circles with ID label)
  • Thermal signatures (red flame icon)
  • Confirmed detections (bright red with tick)
  • Mission status bar and legend

Run this in a separate terminal BEFORE or AFTER starting command_agent.py.
"""

import json
import os
import tkinter as tk
from tkinter import font as tkfont

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
STATE_FILE   = os.path.join(os.path.dirname(__file__), "world_state.json")
CELL_SIZE    = 64          # pixels per grid cell
GRID_W       = 10
GRID_H       = 10
PADDING      = 20          # canvas padding around the grid
POLL_MS      = 500         # how often to refresh (milliseconds)

# Colour palette — one per drone (up to 6)
DRONE_COLOURS = ["#4FC3F7", "#81C784", "#FFB74D", "#CE93D8", "#F48FB1", "#80DEEA"]
ZONE_FILL     = ["#1A3A4A", "#1A3A2A", "#3A2A1A", "#2A1A3A", "#3A1A2A", "#1A3A3A"]

BG_COLOUR     = "#0D1117"
GRID_LINE     = "#30363D"
CELL_UNSCANNED = "#161B22"
CELL_SCANNED   = "#1C2D1C"
THERMAL_COLOUR = "#FF4444"
CONFIRMED_COLOUR = "#FF6B6B"
TEXT_COLOUR   = "#E6EDF3"
MUTED_COLOUR  = "#8B949E"

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def cell_to_canvas(x: int, y: int):
    """Convert grid (x, y) to canvas pixel top-left corner."""
    # Grid y=0 is bottom-left; canvas y=0 is top-left — flip y
    cx = PADDING + x * CELL_SIZE
    cy = PADDING + (GRID_H - 1 - y) * CELL_SIZE
    return cx, cy


def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


# ─────────────────────────────────────────────
# MAIN GUI CLASS
# ─────────────────────────────────────────────

class DroneGridGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Earthquake Survivor Detector")
        self.root.configure(bg=BG_COLOUR)
        self.root.resizable(False, False)

        canvas_w = PADDING * 2 + GRID_W * CELL_SIZE
        canvas_h = PADDING * 2 + GRID_H * CELL_SIZE

        # ── Title bar ──────────────────────────────
        title_frame = tk.Frame(root, bg=BG_COLOUR)
        title_frame.pack(fill="x", padx=16, pady=(12, 0))

        tk.Label(
            title_frame, text="DRONE MISSION CONTROL",
            bg=BG_COLOUR, fg=TEXT_COLOUR,
            font=("Segoe UI", 14, "bold")
        ).pack(side="left")

        self.status_label = tk.Label(
            title_frame, text="● AWAITING ORDERS",
            bg=BG_COLOUR, fg=MUTED_COLOUR,
            font=("Segoe UI", 10, "bold")
        )
        self.status_label.pack(side="right")

        # ── Canvas ─────────────────────────────────
        self.canvas = tk.Canvas(
            root, width=canvas_w, height=canvas_h,
            bg=BG_COLOUR, highlightthickness=0
        )
        self.canvas.pack(padx=16, pady=8)

        # ── Info bar ───────────────────────────────
        info_frame = tk.Frame(root, bg="#161B22", bd=0)
        info_frame.pack(fill="x", padx=16, pady=(0, 4))

        self.progress_label = tk.Label(
            info_frame, text="Scanned: 0 / 100 cells  (0%)",
            bg="#161B22", fg=MUTED_COLOUR,
            font=("Segoe UI", 9), padx=10, pady=6
        )
        self.progress_label.pack(side="left")

        self.sig_label = tk.Label(
            info_frame, text="Thermal signatures found: 0",
            bg="#161B22", fg=MUTED_COLOUR,
            font=("Segoe UI", 9), padx=10, pady=6
        )
        self.sig_label.pack(side="left")

        # ── Legend ─────────────────────────────────
        legend_frame = tk.Frame(root, bg=BG_COLOUR)
        legend_frame.pack(fill="x", padx=16, pady=(0, 12))

        items = [
            (CELL_UNSCANNED, "Unscanned"),
            (CELL_SCANNED,   "Scanned"),
            (THERMAL_COLOUR, "Thermal sig."),
        ]
        for colour, label in items:
            box = tk.Frame(legend_frame, bg=colour, width=14, height=14)
            box.pack(side="left", padx=(0, 4), pady=2)
            tk.Label(
                legend_frame, text=label,
                bg=BG_COLOUR, fg=MUTED_COLOUR,
                font=("Segoe UI", 8)
            ).pack(side="left", padx=(0, 12))

        # Draw static grid lines once
        self._draw_static()

        # Start polling
        self.root.after(100, self._refresh)

    # ── STATIC ELEMENTS ────────────────────────────────────────────────────────

    def _draw_static(self):
        """Draw axis labels and outer border — called once."""
        c = self.canvas
        label_font = ("Segoe UI", 8)

        for x in range(GRID_W):
            cx, _ = cell_to_canvas(x, 0)
            c.create_text(
                cx + CELL_SIZE // 2,
                PADDING + GRID_H * CELL_SIZE + 10,
                text=str(x), fill=MUTED_COLOUR, font=label_font
            )
        for y in range(GRID_H):
            _, cy = cell_to_canvas(0, y)
            c.create_text(
                PADDING - 10,
                cy + CELL_SIZE // 2,
                text=str(y), fill=MUTED_COLOUR, font=label_font
            )

    # ── REFRESH LOOP ───────────────────────────────────────────────────────────

    def _refresh(self):
        state = load_state()
        if state:
            self._render(state)
        self.root.after(POLL_MS, self._refresh)

    # ── RENDER ─────────────────────────────────────────────────────────────────

    def _render(self, state: dict):
        c = self.canvas
        c.delete("dynamic")   # clear all dynamic elements each frame

        drones       = state.get("drones", {})
        zones        = state.get("zones", {})
        scanned      = [tuple(cell) for cell in state.get("scanned_cells", [])]
        thermals     = state.get("thermal_signatures", [])
        confirmed    = state.get("found_signatures", [])
        mission      = state.get("mission_status", "awaiting_orders")

        drone_ids    = list(drones.keys())
        drone_colour = {did: DRONE_COLOURS[i % len(DRONE_COLOURS)]
                        for i, did in enumerate(drone_ids)}
        zone_fill    = {did: ZONE_FILL[i % len(ZONE_FILL)]
                        for i, did in enumerate(drone_ids)}

        # ── 1. Draw zone backgrounds ──────────────────────────────────────────
        for did, zone in zones.items():
            zfill = zone_fill.get(did, "#1A2A1A")
            xs, ys = zone["x_start"], zone["y_start"]
            xe, ye = zone["x_end"],   zone["y_end"]
            # Draw each cell in zone
            for x in range(xs, xe + 1):
                for y in range(ys, ye + 1):
                    cx, cy = cell_to_canvas(x, y)
                    c.create_rectangle(
                        cx, cy, cx + CELL_SIZE, cy + CELL_SIZE,
                        fill=zfill, outline="", tags="dynamic"
                    )
            # Zone border
            bx0, by0 = cell_to_canvas(xs, ye)   # top-left in canvas coords
            bx1, by1 = cell_to_canvas(xe, ys)   # bottom-right in canvas coords
            c.create_rectangle(
                bx0, by0, bx1 + CELL_SIZE, by1 + CELL_SIZE,
                fill="", outline=drone_colour.get(did, "#FFFFFF"),
                width=2, dash=(6, 3), tags="dynamic"
            )
            # Zone label
            mid_x = (bx0 + bx1 + CELL_SIZE) // 2
            mid_y = (by0 + by1 + CELL_SIZE) // 2
            c.create_text(
                mid_x, mid_y,
                text=did.upper(),
                fill=drone_colour.get(did, "#FFFFFF"),
                font=("Segoe UI", 9, "bold"),
                tags="dynamic"
            )

        # ── 2. Draw grid cells ────────────────────────────────────────────────
        for x in range(GRID_W):
            for y in range(GRID_H):
                cx, cy = cell_to_canvas(x, y)
                fill = CELL_SCANNED if (x, y) in scanned else CELL_UNSCANNED
                c.create_rectangle(
                    cx, cy, cx + CELL_SIZE, cy + CELL_SIZE,
                    fill=fill, outline=GRID_LINE, width=1, tags="dynamic"
                )

        # ── 3. Draw thermal signatures (hidden until found) ───────────────────
        conf_set = {(s["x"], s["y"]) for s in confirmed}
        for sig in thermals:
            sx, sy = sig["x"], sig["y"]
            if (sx, sy) in conf_set:
                cx, cy = cell_to_canvas(sx, sy)
                # Confirmed — bright red cell
                c.create_rectangle(
                    cx + 2, cy + 2, cx + CELL_SIZE - 2, cy + CELL_SIZE - 2,
                    fill="#3D1010", outline=CONFIRMED_COLOUR, width=2, tags="dynamic"
                )
                c.create_text(
                    cx + CELL_SIZE // 2, cy + CELL_SIZE // 2,
                    text="🔥", font=("Segoe UI", 18), tags="dynamic"
                )

        # ── 4. Draw drones ────────────────────────────────────────────────────
        r = CELL_SIZE // 3   # circle radius
        for did, drone in drones.items():
            dx, dy = drone["x"], drone["y"]
            cx, cy = cell_to_canvas(dx, dy)
            centre_x = cx + CELL_SIZE // 2
            centre_y = cy + CELL_SIZE // 2
            colour = drone_colour.get(did, "#FFFFFF")

            # Outer glow
            c.create_oval(
                centre_x - r - 4, centre_y - r - 4,
                centre_x + r + 4, centre_y + r + 4,
                fill="", outline=colour, width=1,
                stipple="gray25", tags="dynamic"
            )
            # Body
            c.create_oval(
                centre_x - r, centre_y - r,
                centre_x + r, centre_y + r,
                fill=colour, outline="white", width=1, tags="dynamic"
            )
            # Drone ID label
            c.create_text(
                centre_x, centre_y,
                text=did[0].upper(),
                fill="black",
                font=("Segoe UI", 10, "bold"),
                tags="dynamic"
            )
            # Battery label below drone
            bat = drone.get("battery", 0)
            bat_colour = "#4CAF50" if bat > 50 else "#FF9800" if bat > 20 else "#F44336"
            c.create_text(
                centre_x, cy + CELL_SIZE - 8,
                text=f"{bat}%",
                fill=bat_colour,
                font=("Segoe UI", 7, "bold"),
                tags="dynamic"
            )

        # ── 5. Update status bar ──────────────────────────────────────────────
        total   = GRID_W * GRID_H
        scanned_count = len(scanned)
        pct     = round(scanned_count / total * 100, 1)

        status_map = {
            "awaiting_orders": ("● AWAITING ORDERS", MUTED_COLOUR),
            "in_progress":     ("● MISSION IN PROGRESS", "#4FC3F7"),
            "complete":        ("✔ MISSION COMPLETE", "#4CAF50"),
        }
        status_text, status_colour = status_map.get(
            mission, ("● UNKNOWN", MUTED_COLOUR)
        )
        self.status_label.config(text=status_text, fg=status_colour)
        self.progress_label.config(
            text=f"Scanned: {scanned_count} / {total} cells  ({pct}%)"
        )
        self.sig_label.config(
            text=f"Thermal signatures confirmed: {len(confirmed)}"
        )


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    root = tk.Tk()
    app = DroneGridGUI(root)
    root.mainloop()
