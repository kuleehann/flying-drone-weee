# Earthquake Survivor Detector

## Project Structure

- `command_agent.py`
  - Starts a minimal JSON-RPC MCP client over stdio.
  - Launches `mcp_server.py` as a subprocess.
  - Discovers drones, assigns zones, runs battery-aware area scans, and produces a final mission debrief with Mistral.
  - Enters an interactive command prompt after autonomous mission completion.

- `mcp_server.py`
  - Implements the MCP server (`FastMCP`) and exposes drone tools such as:
    - `get_network_nodes`
    - `assign_zones`
    - `get_fleet_telemetry`
    - `move_to`
    - `charge_drone`
    - `thermal_scan`
    - `area_scan`
    - `get_mission_status`
  - Maintains shared in-memory world state and persists it to `world_state.json` for the GUI.
  - Includes a Windows event loop policy fix for stdio subprocess behavior.

- `gui.py`
  - Tkinter dashboard that polls `world_state.json` every 500 ms.
  - Shows 10x10 grid, zone boundaries, scanned cells, drone positions, battery labels, and confirmed thermal signatures.

- `simulation.py`
  - Standalone Mesa-based model (`DisasterModel`, `DroneAgent`) for direct simulation/testing independent of MCP and GUI.
  - Useful for experimenting with movement, battery cost, and zone assignment logic.

- `world_state.json`
  - Shared state snapshot written by `mcp_server.py` and read by `gui.py`.

## Requirements

- Python 3.10+
- Windows/macOS/Linux
- API key for Mistral (used by `command_agent.py`)

Install dependencies:

```bash
pip install -r requirements.txt
```

If you see an import error for `langchain_mistralai`, install it explicitly:

```bash
pip install langchain-mistralai
```

## Environment Setup

Create a `.env` file in the project root:

```env
MISTRAL_API_KEY=your_api_key_here
```

## Run the System

Use two terminals from the project root.

1. Start the live GUI:

```bash
python gui.py
```

2. Start the command agent:

```bash
python command_agent.py
```

What happens:
- `command_agent.py` starts `mcp_server.py` automatically.
- The agent performs discovery, zone assignment, scanning, recharge decisions, and mission completion checks.
- `gui.py` updates live as the mission state changes.

## Run Standalone Simulation

To test the Mesa model directly:

```bash
python simulation.py
```

This prints initial state, sample manual moves, scan results, and final state.

## Notes

- `mcp_server.py` uses stdio transport. Avoid printing to stdout there unless it is part of MCP JSON-RPC communication.
- `world_state.json` is mission state output and will change during runs.
- The mission can end with interactive mode in `command_agent.py`; type `q` to exit.

## Quick Troubleshooting

- GUI shows no updates:
  - Ensure `command_agent.py` is running (it launches the MCP server and updates state).
- MCP handshake or startup issues on Windows:
  - Confirm you are using a supported Python version and dependencies from `requirements.txt`.
- LLM report unavailable:
  - Check `MISTRAL_API_KEY` in `.env` and network/API access.
