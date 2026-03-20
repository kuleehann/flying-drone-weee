import mesa


# ─────────────────────────────────────────────
# 1. DRONE AGENT
# ─────────────────────────────────────────────

class DroneAgent(mesa.Agent):
    """A Mesa agent representing a rescue drone."""

    def __init__(self, unique_id, model):
        super().__init__(model)
        self.unique_id = unique_id
        self.battery   = 100
        self.zone      = None    # Assigned search zone: {"x_start", "y_start", "x_end", "y_end"}
        self.status    = "idle"  # idle | moving | scanning | charging | complete

    def move_to(self, x: int, y: int) -> str:
        """Moves the drone to (x, y). Costs 2% battery per Manhattan step."""
        if self.battery <= 0:
            return f"Error: Drone {self.unique_id} has a dead battery."

        dist = abs(x - self.pos[0]) + abs(y - self.pos[1])
        cost = dist * 2

        if self.battery < cost:
            return (f"Error: Drone {self.unique_id} has insufficient battery "
                    f"({self.battery}%) for this move (needs {cost}%).")

        self.model.grid.move_agent(self, (x, y))
        self.battery -= cost
        self.status = "moving"
        return (f"Success: Drone {self.unique_id} moved to ({x},{y}). "
                f"Battery: {self.battery}%")

    def get_battery_status(self) -> str:
        """Returns the current battery level."""
        return f"Drone {self.unique_id}: {self.battery}% battery at {self.pos}"

    def thermal_scan(self) -> str:
        """Scans the current cell for thermal signatures."""
        x, y = self.pos
        if {"x": x, "y": y} in self.model.thermal_signatures:
            return f"🚨 THERMAL SIGNATURE at ({x},{y}) detected by Drone {self.unique_id}!"
        return f"Scan clear at ({x},{y})."


# ─────────────────────────────────────────────
# 2. DISASTER MODEL
# ─────────────────────────────────────────────

class DisasterModel(mesa.Model):
    """
    A 10×10 disaster zone with drones and hidden thermal signatures.
    Drones are assigned non-overlapping zones and scan in parallel.
    """

    def __init__(self, num_drones: int = 3, width: int = 10, height: int = 10):
        super().__init__()
        self.grid   = mesa.space.MultiGrid(width, height, torus=False)
        self.drones = {}

        # Place thermal signatures randomly (3–5)
        import random, math
        num_sigs = random.randint(3, 5)
        self.thermal_signatures = []
        placed = set()
        while len(placed) < num_sigs:
            sx = random.randint(0, width  - 1)
            sy = random.randint(0, height - 1)
            placed.add((sx, sy))
        for (sx, sy) in placed:
            self.thermal_signatures.append({"x": sx, "y": sy})

        # Create drones and place them at (0,0)
        for i in range(num_drones):
            drone = DroneAgent(i, self)
            self.drones[i] = drone
            self.grid.place_agent(drone, (0, 0))

        # Assign zones
        self._assign_zones(num_drones, width, height)

    def _assign_zones(self, n: int, W: int, H: int):
        """Divide the grid into n equal rectangular zones."""
        import math
        cols = math.ceil(math.sqrt(n))
        while n % cols != 0 and cols > 1:
            cols -= 1
        rows = math.ceil(n / cols)
        zone_w = W // cols
        zone_h = H // rows

        for idx, drone in self.drones.items():
            col = idx % cols
            row = idx // cols
            x0 = col * zone_w
            y0 = row * zone_h
            x1 = (x0 + zone_w - 1) if col < cols - 1 else (W - 1)
            y1 = (y0 + zone_h - 1) if row < rows - 1 else (H - 1)
            drone.zone = {"x_start": x0, "y_start": y0, "x_end": x1, "y_end": y1}

    def get_status(self) -> str:
        """Returns the current location, battery, and zone of all drones."""
        lines = ["─── FLEET STATUS ───"]
        for d_id, drone in self.drones.items():
            lines.append(
                f"Drone {d_id}: pos={drone.pos}  battery={drone.battery}%  "
                f"zone={drone.zone}  status={drone.status}"
            )
        return "\n".join(lines)

    def command_move(self, drone_id: int, x: int, y: int) -> str:
        if drone_id not in self.drones:
            return f"Error: Drone {drone_id} does not exist."
        return self.drones[drone_id].move_to(x, y)


# ─────────────────────────────────────────────
# 3. STANDALONE TEST
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("Initialising DisasterModel (3 drones, 10×10 grid)...")
    model = DisasterModel(num_drones=3, width=10, height=10)

    print("\nINITIAL STATE:")
    print(model.get_status())

    print(f"\nThermal signatures hidden at: {model.thermal_signatures}")

    print("\nMANUAL COMMAND TEST:")
    print(model.command_move(drone_id=0, x=5, y=5))
    print(model.command_move(drone_id=1, x=2, y=8))
    print(model.drones[0].thermal_scan())

    print("\nFINAL STATE:")
    print(model.get_status())