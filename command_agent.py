import os
import sys
import json
import asyncio
from dotenv import load_dotenv
from langchain_mistralai import ChatMistralAI
from langchain_core.messages import HumanMessage, SystemMessage

load_dotenv()

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
MCP_SERVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py")
PYTHON_CMD = "py" if sys.platform == "win32" else "python3"


# ─────────────────────────────────────────────
# MINIMAL MCP JSON-RPC CLIENT
# Uses pure asyncio.create_subprocess_exec — no anyio, no mcp SDK client
# ─────────────────────────────────────────────

class MCPClient:
    """
    Minimal MCP client over stdio using pure asyncio subprocess.
    Implements only what is needed: initialize handshake + call_tool.
    """

    def __init__(self, command: str, args: list):
        self._command = command
        self._args = args
        self._proc = None
        self._req_id = 0

    async def start(self):
        """Spawn the MCP server process and perform the initialize handshake."""
        self._proc = await asyncio.create_subprocess_exec(
            self._command, *self._args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,  # suppress server log noise
        )

        # MCP initialize request
        init_resp = await self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "DroneCommandAgent", "version": "1.0"},
        })
        if "error" in init_resp:
            raise RuntimeError(f"MCP initialize failed: {init_resp['error']}")

        # Send initialized notification (no response expected)
        await self._notify("notifications/initialized", {})

    async def list_tools(self) -> list:
        """Return list of tool names available on the server."""
        resp = await self._request("tools/list", {})
        return [t["name"] for t in resp.get("result", {}).get("tools", [])]

    async def call_tool(self, name: str, arguments: dict = None) -> dict:
        """Call a named tool and return the parsed JSON result."""
        resp = await self._request("tools/call", {
            "name": name,
            "arguments": arguments or {},
        })
        if "error" in resp:
            return {"error": resp["error"].get("message", str(resp["error"]))}
        content = resp.get("result", {}).get("content", [])
        if content:
            text = content[0].get("text", "{}")
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw": text}
        return {}

    async def stop(self):
        """Terminate the server process."""
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=3.0)
            except Exception:
                pass

    # ── internal ────────────────────────────────────────────────────────────

    async def _send(self, obj: dict):
        line = json.dumps(obj) + "\n"
        self._proc.stdin.write(line.encode())
        await self._proc.stdin.drain()

    async def _recv(self) -> dict:
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                raise ConnectionError("MCP server closed stdout unexpectedly.")
            text = line.decode().strip()
            if not text:
                continue
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue  # skip non-JSON lines (e.g. logging output)

    async def _request(self, method: str, params: dict) -> dict:
        self._req_id += 1
        req_id = self._req_id
        await self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        # Read responses until we get the one matching our request ID
        while True:
            resp = await self._recv()
            if resp.get("id") == req_id:
                return resp

    async def _notify(self, method: str, params: dict):
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def banner(text: str, char: str = "═", width: int = 60):
    print(f"\n{char * width}\n  {text}\n{char * width}")


def manhattan(x1, y1, x2, y2) -> int:
    return abs(x2 - x1) + abs(y2 - y1)


def scan_cost(drone: dict, zone: dict) -> int:
    travel = manhattan(drone["x"], drone["y"], zone["x_start"], zone["y_start"])
    cells  = (zone["x_end"] - zone["x_start"] + 1) * (zone["y_end"] - zone["y_start"] + 1)
    return travel * 2 + cells


def return_cost(drone: dict) -> int:
    return manhattan(drone["x"], drone["y"], 0, 0) * 2


# ─────────────────────────────────────────────
# MISSION LOGIC
# ─────────────────────────────────────────────

async def run_mission(client: MCPClient, llm: ChatMistralAI):
    mission_log    = []
    all_found_sigs = []

    # ── Step 1: Discover drones ──────────────────────────────────────────────
    banner("STEP 1 — Fleet Discovery", "─")
    discovery = await client.call_tool("get_network_nodes")
    drone_ids = discovery["active_drones"]
    print(f"[AGENT] Active drones discovered: {drone_ids}")

    # ── Step 2: Assign zones ─────────────────────────────────────────────────
    banner("STEP 2 — Zone Assignment", "─")
    zone_data = await client.call_tool("assign_zones")
    zones = zone_data["zones"]
    for did, z in zones.items():
        print(f"  {did:10s} → x:{z['x_start']}-{z['x_end']}  y:{z['y_start']}-{z['y_end']}")

    # ── Step 3: Autonomous mission loop ──────────────────────────────────────
    banner("STEP 3 — Autonomous Mission", "─")

    pending_scans = [{"drone_id": did, **zones[did]} for did in drone_ids]
    iteration = 0
    max_iter  = 30

    while pending_scans and iteration < max_iter:
        iteration += 1
        print(f"\n[CYCLE {iteration}] Pending zone segments: {len(pending_scans)}")

        fleet_data = await client.call_tool("get_fleet_telemetry")
        fleet = fleet_data["fleet"]
        next_pending = []

        for seg in pending_scans:
            did   = seg["drone_id"]
            drone = fleet[did]
            zone  = {k: seg[k] for k in ("x_start", "y_start", "x_end", "y_end")}

            needed  = scan_cost(drone, zone)
            ret_bat = return_cost(drone)

            print(f"  [{did}] battery={drone['battery']}%  "
                  f"scan_need={needed}%  return_cost={ret_bat}%  "
                  f"zone=({zone['x_start']},{zone['y_start']})-({zone['x_end']},{zone['y_end']})")

            # ── Case 1: enough battery to scan directly ──────────────────────
            if drone["battery"] >= needed:
                print(f"  [{did}] Scanning zone directly...")
                result = await client.call_tool("area_scan", {
                    "drone_id": did,
                    "x_start": zone["x_start"], "y_start": zone["y_start"],
                    "x_end":   zone["x_end"],   "y_end":   zone["y_end"],
                })
                if "error" in result:
                    print(f"  [{did}] Scan error: {result['error']}")
                    next_pending.append(seg)
                else:
                    sigs = result.get("thermal_signatures_found", [])
                    print(f"  [{did}] Done. Battery={result['battery_remaining']}%  "
                          f"Signatures={sigs or 'none'}")
                    mission_log.append(
                        f"Drone {did} scanned ({zone['x_start']},{zone['y_start']}) to "
                        f"({zone['x_end']},{zone['y_end']}). "
                        f"Found: {sigs if sigs else 'nothing'}. "
                        f"Battery remaining: {result['battery_remaining']}%."
                    )
                    all_found_sigs.extend(sigs)
                fleet = (await client.call_tool("get_fleet_telemetry"))["fleet"]
                drone = fleet[did]

            # ── Case 2: low battery — return to base and recharge ────────────
            elif drone["battery"] >= ret_bat:
                print(f"  [{did}] Low battery. Returning to base (costs {ret_bat}%)...")
                move_result = await client.call_tool("move_to", {
                    "drone_id": did, "target_x": 0, "target_y": 0
                })
                if "error" in move_result:
                    print(f"  [{did}] Move error: {move_result['error']}")
                    next_pending.append(seg)
                    continue

                charge_result = await client.call_tool("charge_drone", {"drone_id": did})
                if "error" in charge_result:
                    print(f"  [{did}] Charge error: {charge_result['error']}")
                    next_pending.append(seg)
                    continue

                print(f"  [{did}] Recharged to 100%.")
                mission_log.append(f"Drone {did} returned to base and recharged to 100%.")

                fleet = (await client.call_tool("get_fleet_telemetry"))["fleet"]
                drone = fleet[did]

                needed_now = scan_cost(drone, zone)
                if drone["battery"] >= needed_now:
                    print(f"  [{did}] Scanning zone after recharge...")
                    result = await client.call_tool("area_scan", {
                        "drone_id": did,
                        "x_start": zone["x_start"], "y_start": zone["y_start"],
                        "x_end":   zone["x_end"],   "y_end":   zone["y_end"],
                    })
                    if "error" in result:
                        print(f"  [{did}] Scan error after recharge: {result['error']}")
                        next_pending.append(seg)
                    else:
                        sigs = result.get("thermal_signatures_found", [])
                        print(f"  [{did}] Done. Battery={result['battery_remaining']}%  "
                              f"Signatures={sigs or 'none'}")
                        mission_log.append(
                            f"Drone {did} (recharged) scanned ({zone['x_start']},{zone['y_start']}) to "
                            f"({zone['x_end']},{zone['y_end']}). "
                            f"Found: {sigs if sigs else 'nothing'}. "
                            f"Battery remaining: {result['battery_remaining']}%."
                        )
                        all_found_sigs.extend(sigs)
                else:
                    print(f"  [{did}] Zone too large for full battery. Splitting...")
                    mid_y = (zone["y_start"] + zone["y_end"]) // 2
                    next_pending.append({**seg, "y_end": mid_y})
                    if mid_y + 1 <= zone["y_end"]:
                        next_pending.append({**seg, "y_start": mid_y + 1})

            # ── Case 3: cannot reach base — partial scan from here ───────────
            else:
                available = drone["battery"]
                print(f"  [{did}] Cannot reach base ({ret_bat}% needed, {available}% left). "
                      f"Partial scan from current position...")
                max_cells = available
                scan_xe   = min(zone["x_end"], drone["x"] + max_cells - 1)
                scan_ye   = drone["y"]
                if max_cells > 0 and scan_xe >= drone["x"]:
                    result = await client.call_tool("area_scan", {
                        "drone_id": did,
                        "x_start": drone["x"], "y_start": drone["y"],
                        "x_end":   scan_xe,    "y_end":   scan_ye,
                    })
                    sigs = result.get("thermal_signatures_found", [])
                    print(f"  [{did}] Partial scan done. Signatures={sigs or 'none'}")
                    all_found_sigs.extend(sigs)
                    remainder = {**seg, "x_start": scan_xe + 1}
                    if remainder["x_start"] <= remainder["x_end"]:
                        next_pending.append(remainder)
                else:
                    print(f"  [{did}] Drone stranded with {available}% battery. Skipping.")

        pending_scans = next_pending
        await asyncio.sleep(0.2)

    # ── Step 4: Verify completion ─────────────────────────────────────────────
    status_data = await client.call_tool("get_mission_status")

    banner("STEP 4 — Mission Status", "─")
    print(f"  Status   : {status_data['mission_status'].upper()}")
    print(f"  Coverage : {status_data['cells_scanned']}/{status_data['total_cells']} cells "
          f"({status_data['percent_complete']}%)")
    print(f"  Confirmed signatures: {status_data['confirmed_signatures']}")

    # ── Step 5: LLM mission report ────────────────────────────────────────────
    banner("STEP 5 — Generating Final Mission Report", "─")
    report_prompt = (
        "Write a concise, structured final mission debrief report.\n\n"
        "MISSION LOG:\n"
        + "\n".join(f"- {e}" for e in mission_log)
        + f"\n\nMISSION STATUS: {status_data['mission_status']}\n"
        f"CELLS SCANNED: {status_data['cells_scanned']} / {status_data['total_cells']} "
        f"({status_data['percent_complete']}%)\n"
        f"CONFIRMED THERMAL SIGNATURES: {status_data['confirmed_signatures']}\n\n"
        "Sections required: Mission Summary, Zone Coverage, Thermal Findings, Recommendations.\n"
        "Be concise and professional."
    )
    try:
        response = await llm.ainvoke([
            SystemMessage(content="You are a military-style drone mission debrief officer."),
            HumanMessage(content=report_prompt),
        ])
        banner("FINAL MISSION REPORT", "═")
        print(response.content)
        banner("END OF REPORT", "─")
    except Exception as e:
        print(f"[WARN] LLM report unavailable: {e}")
        banner("FINAL MISSION REPORT (RAW)", "═")
        print(f"Mission status : {status_data['mission_status']}")
        print(f"Coverage       : {status_data['percent_complete']}%")
        print(f"Signatures     : {status_data['confirmed_signatures']}")
        banner("END OF REPORT", "─")


# ─────────────────────────────────────────────
# ASYNC MAIN
# ─────────────────────────────────────────────

async def main():
    banner("DRONE COMMAND SYSTEM — INITIALISING")
    print("\n[SYSTEM] Connecting to Drone MCP Server...")
    print("[INFO]  Launch gui.py in a separate terminal to watch the live grid.\n")

    client = MCPClient(PYTHON_CMD, [MCP_SERVER])

    try:
        await client.start()

        tools = await client.list_tools()
        print(f"[SYSTEM] Uplink established. Tools available: {tools}")

        llm = ChatMistralAI(model="mistral-small-latest", temperature=0)

        banner("AUTONOMOUS MISSION STARTING")
        await run_mission(client, llm)

        # ── Interactive command mode ──────────────────────────────────────────
        print("\n[SYSTEM] Mission complete. Entering interactive mode. Type 'q' to quit.")
        while True:
            try:
                cmd = input("\nCommander > ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if cmd.lower() in ("q", "quit", "exit", ""):
                break
            try:
                interp = await llm.ainvoke([
                    SystemMessage(content=(
                        "You are a drone command interpreter. "
                        "Available tools: get_network_nodes, get_drone_telemetry, "
                        "get_fleet_telemetry, assign_zones, move_to, charge_drone, "
                        "thermal_scan, area_scan, get_mission_status. "
                        "Respond with a brief plain-English action plan. Do not call tools."
                    )),
                    HumanMessage(content=cmd),
                ])
                print(f"\n[AGENT] {interp.content}")
            except Exception as e:
                print(f"[ERROR] {e}")

    finally:
        await client.stop()
        print("\n[SYSTEM] Commander Protocol terminated. Goodbye.")


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    asyncio.run(main())