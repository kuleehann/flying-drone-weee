"""
mcp_server.py — Drone Command MCP Server (Revised)

Exposes drone tools via MCP stdio transport.
Writes live world state to world_state.json so the GUI can read it.

Windows / Python 3.14 fix:
    - asyncio.WindowsProactorEventLoopPolicy is set before mcp.run()
    - No stdout prints anywhere (stdout is reserved for MCP JSON-RPC protocol)
    - _save_state() is NOT called at module level — only inside tool handlers
"""

import json
import os
import sys
import random
import math
from mcp.server.fastmcp import FastMCP

# ─────────────────────────────────────────────
# STATE FILE — shared with the GUI
# ─────────────────────────────────────────────
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "world_state.json")


def _save_state():
    """Persist the current world state to disk for the GUI to read."""
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(WORLD_STATE, f, indent=2)
    except Exception:
        pass  # Never crash the MCP server over a file write


# ─────────────────────────────────────────────
# WORLD STATE
# ─────────────────────────────────────────────
GRID_W = 10
GRID_H = 10

WORLD_STATE = {
    "grid_width":  GRID_W,
    "grid_height": GRID_H,
    "drones": {
        "alpha": {"x": 0, "y": 0, "battery": 100, "zone": None, "status": "idle"},
        "beta":  {"x": 0, "y": 0, "battery": 100, "zone": None, "status": "idle"},
        "gamma": {"x": 0, "y": 0, "battery": 100, "zone": None, "status": "idle"},
    },
    # Thermal signatures: list of {"x": int, "y": int}
    "thermal_signatures": [],
    # Cells that have been scanned: list of [x, y]
    "scanned_cells": [],
    # Zone assignments: {"drone_id": {"x_start", "y_start", "x_end", "y_end"}}
    "zones": {},
    # Mission status
    "mission_status": "awaiting_orders",   # awaiting_orders | in_progress | complete
    "found_signatures": [],                # confirmed detections
}

# Place 3–5 thermal signatures randomly across the grid
# NOTE: This is pure in-memory setup — no stdout, no file writes at import time
_num_sigs = random.randint(3, 5)
_placed: set = set()
while len(_placed) < _num_sigs:
    _sx = random.randint(0, GRID_W - 1)
    _sy = random.randint(0, GRID_H - 1)
    _placed.add((_sx, _sy))
for (_sx, _sy) in _placed:
    WORLD_STATE["thermal_signatures"].append({"x": _sx, "y": _sy})

# ─────────────────────────────────────────────
# MCP SERVER
# ─────────────────────────────────────────────
mcp = FastMCP("DroneCommandServer")


# ── DISCOVERY ──────────────────────────────────────────────────────────────────

@mcp.tool()
def get_network_nodes() -> str:
    """
    ALWAYS CALL THIS FIRST.
    Returns the list of active drone IDs on the network.
    Use these IDs for all subsequent tool calls.
    """
    ids = list(WORLD_STATE["drones"].keys())
    _save_state()
    return json.dumps({"active_drones": ids, "count": len(ids)})


# ── TELEMETRY ──────────────────────────────────────────────────────────────────

@mcp.tool()
def get_drone_telemetry(drone_id: str) -> str:
    """
    Returns the current position, battery level, and status of a specific drone.
    Args:
        drone_id: The drone identifier returned by get_network_nodes().
    """
    if drone_id not in WORLD_STATE["drones"]:
        return json.dumps({"error": f"Drone '{drone_id}' not found."})
    d = WORLD_STATE["drones"][drone_id]
    return json.dumps({
        "drone_id": drone_id,
        "x": d["x"], "y": d["y"],
        "battery": d["battery"],
        "status": d["status"],
        "zone": d["zone"],
    })


@mcp.tool()
def get_fleet_telemetry() -> str:
    """
    Returns position, battery, and status for ALL drones at once.
    Use this to get a full fleet overview in a single call.
    """
    fleet = {}
    for did, d in WORLD_STATE["drones"].items():
        fleet[did] = {
            "x": d["x"], "y": d["y"],
            "battery": d["battery"],
            "status": d["status"],
        }
    return json.dumps({"fleet": fleet})


# ── ZONE ASSIGNMENT ─────────────────────────────────────────────────────────────

@mcp.tool()
def assign_zones() -> str:
    """
    Automatically divides the 10x10 grid into equal rectangular zones —
    one zone per active drone — and returns the zone boundaries.
    Call this after get_network_nodes() to get each drone's assigned search area.
    Returns a mapping of drone_id -> {x_start, y_start, x_end, y_end}.
    """
    drones = list(WORLD_STATE["drones"].keys())
    n = len(drones)
    W, H = WORLD_STATE["grid_width"], WORLD_STATE["grid_height"]

    # Determine best column split: try to keep zones roughly square
    cols = math.ceil(math.sqrt(n))
    while n % cols != 0 and cols > 1:
        cols -= 1
    rows = math.ceil(n / cols)

    zone_w = W // cols
    zone_h = H // rows

    zones = {}
    for idx, did in enumerate(drones):
        col = idx % cols
        row = idx // cols
        x0 = col * zone_w
        y0 = row * zone_h
        x1 = (x0 + zone_w - 1) if col < cols - 1 else (W - 1)
        y1 = (y0 + zone_h - 1) if row < rows - 1 else (H - 1)
        zones[did] = {"x_start": x0, "y_start": y0, "x_end": x1, "y_end": y1}
        WORLD_STATE["drones"][did]["zone"] = zones[did]

    WORLD_STATE["zones"] = zones
    _save_state()
    return json.dumps({"zones": zones, "grid": f"{W}x{H}", "num_drones": n})


# ── MOVEMENT ────────────────────────────────────────────────────────────────────

@mcp.tool()
def move_to(drone_id: str, target_x: int, target_y: int) -> str:
    """
    Moves a drone to the specified coordinates.
    Battery cost: 2% per Manhattan-distance step.
    Args:
        drone_id:  Drone identifier.
        target_x:  Destination X (0–9).
        target_y:  Destination Y (0–9).
    """
    if drone_id not in WORLD_STATE["drones"]:
        return json.dumps({"error": f"Drone '{drone_id}' not found."})

    drone = WORLD_STATE["drones"][drone_id]
    dist = abs(target_x - drone["x"]) + abs(target_y - drone["y"])
    cost = dist * 2

    if drone["battery"] < cost:
        return json.dumps({
            "error": f"{drone_id} has insufficient battery ({drone['battery']}%) "
                     f"for this move (needs {cost}%)."
        })

    drone["x"] = target_x
    drone["y"] = target_y
    drone["battery"] -= cost
    drone["status"] = "moving"
    _save_state()
    return json.dumps({
        "success": True,
        "drone_id": drone_id,
        "new_position": {"x": target_x, "y": target_y},
        "battery_remaining": drone["battery"],
    })


@mcp.tool()
def charge_drone(drone_id: str) -> str:
    """
    Recharges a drone to 100% battery.
    The drone MUST already be at base position (0, 0).
    Args:
        drone_id: Drone identifier.
    """
    if drone_id not in WORLD_STATE["drones"]:
        return json.dumps({"error": f"Drone '{drone_id}' not found."})

    drone = WORLD_STATE["drones"][drone_id]
    if drone["x"] != 0 or drone["y"] != 0:
        return json.dumps({
            "error": f"{drone_id} cannot charge — it is at ({drone['x']},{drone['y']}). "
                     f"Move it to (0,0) first."
        })

    drone["battery"] = 100
    drone["status"] = "charged"
    _save_state()
    return json.dumps({"success": True, "drone_id": drone_id, "battery": 100})


# ── SCANNING ────────────────────────────────────────────────────────────────────

@mcp.tool()
def thermal_scan(drone_id: str) -> str:
    """
    Scans the drone's current cell for a thermal signature.
    Args:
        drone_id: Drone identifier.
    """
    if drone_id not in WORLD_STATE["drones"]:
        return json.dumps({"error": "Invalid drone ID."})

    drone = WORLD_STATE["drones"][drone_id]
    pos = {"x": drone["x"], "y": drone["y"]}
    cell = [drone["x"], drone["y"]]

    if cell not in WORLD_STATE["scanned_cells"]:
        WORLD_STATE["scanned_cells"].append(cell)

    found = pos in WORLD_STATE["thermal_signatures"]
    if found and pos not in WORLD_STATE["found_signatures"]:
        WORLD_STATE["found_signatures"].append(pos)

    _save_state()
    if found:
        return json.dumps({
            "alert": True,
            "message": f"THERMAL SIGNATURE DETECTED at ({drone['x']},{drone['y']}) by {drone_id}!",
            "location": pos,
        })
    return json.dumps({
        "alert": False,
        "message": f"Scan clear at ({drone['x']},{drone['y']}).",
    })


@mcp.tool()
def area_scan(drone_id: str, x_start: int, y_start: int, x_end: int, y_end: int) -> str:
    """
    Scans a rectangular zone from (x_start, y_start) to (x_end, y_end) inclusive.
    Battery cost: 2% per step to travel to x_start,y_start, then 1% per cell scanned.
    Updates scanned_cells and found_signatures in the shared state.
    Args:
        drone_id: Drone identifier.
        x_start:  Top-left X of the zone.
        y_start:  Top-left Y of the zone.
        x_end:    Bottom-right X of the zone.
        y_end:    Bottom-right Y of the zone.
    """
    if drone_id not in WORLD_STATE["drones"]:
        return json.dumps({"error": f"Drone '{drone_id}' not found."})

    drone = WORLD_STATE["drones"][drone_id]

    # Normalise bounds
    xs, xe = min(x_start, x_end), max(x_start, x_end)
    ys, ye = min(y_start, y_end), max(y_start, y_end)

    # Clamp to grid
    xs = max(0, min(xs, WORLD_STATE["grid_width"]  - 1))
    xe = max(0, min(xe, WORLD_STATE["grid_width"]  - 1))
    ys = max(0, min(ys, WORLD_STATE["grid_height"] - 1))
    ye = max(0, min(ye, WORLD_STATE["grid_height"] - 1))

    travel_dist = abs(xs - drone["x"]) + abs(ys - drone["y"])
    total_cells = (xe - xs + 1) * (ye - ys + 1)
    total_cost  = (travel_dist * 2) + total_cells

    if drone["battery"] < total_cost:
        return json.dumps({
            "error": f"{drone_id} has insufficient battery ({drone['battery']}%) "
                     f"for area scan (needs {total_cost}%)."
        })

    # Perform scan
    new_sigs = []
    for x in range(xs, xe + 1):
        for y in range(ys, ye + 1):
            cell = [x, y]
            if cell not in WORLD_STATE["scanned_cells"]:
                WORLD_STATE["scanned_cells"].append(cell)
            pos = {"x": x, "y": y}
            if pos in WORLD_STATE["thermal_signatures"]:
                if pos not in WORLD_STATE["found_signatures"]:
                    WORLD_STATE["found_signatures"].append(pos)
                new_sigs.append(f"({x},{y})")

    # Update drone position to end of zone
    drone["x"] = xe
    drone["y"] = ye
    drone["battery"] -= total_cost
    drone["status"] = "scanning"

    # Check if entire grid is now scanned
    total_grid_cells = WORLD_STATE["grid_width"] * WORLD_STATE["grid_height"]
    if len(WORLD_STATE["scanned_cells"]) >= total_grid_cells:
        WORLD_STATE["mission_status"] = "complete"
    else:
        WORLD_STATE["mission_status"] = "in_progress"

    _save_state()

    return json.dumps({
        "success": True,
        "drone_id": drone_id,
        "zone_scanned": {"x_start": xs, "y_start": ys, "x_end": xe, "y_end": ye},
        "cells_scanned": total_cells,
        "battery_remaining": drone["battery"],
        "thermal_signatures_found": new_sigs,
        "total_scanned_cells": len(WORLD_STATE["scanned_cells"]),
        "total_grid_cells": total_grid_cells,
        "mission_status": WORLD_STATE["mission_status"],
    })


# ── MISSION STATUS ──────────────────────────────────────────────────────────────

@mcp.tool()
def get_mission_status() -> str:
    """
    Returns the overall mission progress:
    how many cells have been scanned, confirmed thermal signatures,
    and whether the mission is complete.
    """
    total   = WORLD_STATE["grid_width"] * WORLD_STATE["grid_height"]
    scanned = len(WORLD_STATE["scanned_cells"])
    return json.dumps({
        "mission_status":        WORLD_STATE["mission_status"],
        "cells_scanned":         scanned,
        "total_cells":           total,
        "percent_complete":      round(scanned / total * 100, 1),
        "confirmed_signatures":  WORLD_STATE["found_signatures"],
        "total_signatures_found": len(WORLD_STATE["found_signatures"]),
    })


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    # Windows / Python 3.14 fix:
    # The MCP stdio transport uses asyncio subprocesses internally.
    # On Windows, ProactorEventLoop is required for subprocess stdio to work.
    if sys.platform == "win32":
        import asyncio
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    mcp.run("stdio")
