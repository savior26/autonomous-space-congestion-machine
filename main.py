from fastapi import FastAPI
from sklearn.neighbors import KDTree
from pydantic import BaseModel

import numpy as np

from datetime import timedelta

from physics_engine import propagate_state, find_potential_collisions, parse_time,format_time,apply_burn,calculate_fuel_spent, calculate_elevation,cartesian_to_lat_lon,get_rtn_matrix, compute_evasive_maneuver

import pandas as pd

from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Allows your Vite dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# CSV Load karein
ground_stations_df = pd.read_csv("ground_stations.csv")
GROUND_STATIONS = ground_stations_df.to_dict('records')
# --- Shared State (Memory mein data store karne ke liye) ---
class SpaceState:
    def __init__(self):
        self.satellites = {}
        self.debris = {}    
        self.current_time = "2026-03-12T08:00:00Z"
        self.epoch_start = None
        self.scheduled_burns = {}
        self.last_burn_recorded = {} # To track the 600s cooldown
state_manager = SpaceState()
# --- Models for API Request Body ---
class Vector3(BaseModel):
    x: float
    y: float
    z: float
class TelemetryObject(BaseModel):
    id: str
    type: str
    r: Vector3
    v: Vector3

class TelemetryRequest(BaseModel):
    timestamp: str
    objects: list[TelemetryObject]
# --- API Endpoints ---

@app.get("/")
def home():
    return {"message": "ACM API is Online", "version": "1.0"}

@app.post("/api/telemetry")
async def ingest_telemetry(data: TelemetryRequest):
    """
    Section 4.1: Telemetry receive karna aur process karna
    """
    state_manager.current_time = data.timestamp
    new_dt = parse_time(data.timestamp)

    # 2. Set or Reset Epoch (Time Zero for Earth Rotation)
    if state_manager.epoch_start is None:
        state_manager.epoch_start = new_dt
    elif new_dt < state_manager.epoch_start:
        # If we get a timestamp from the past, the simulation likely reset
        state_manager.epoch_start = new_dt
    count=0
    for obj in data.objects:
        pos = np.array([obj.r.x, obj.r.y, obj.r.z])
        vel = np.array([obj.v.x, obj.v.y, obj.v.z])
        if obj.type.upper() == "DEBRIS":
            state_manager.debris[obj.id] = {"r": pos, "v": vel}
            count+=1
        elif obj.type.upper() == "SATELLITE":
            if obj.id not in state_manager.satellites:
                state_manager.satellites[obj.id] = {
                    "r": pos,
                    "v": vel,
                    "nominal_r": pos.copy(),
                    "nominal_v": vel.copy(),
                    "mass": 550.0,
                    "fuel": 50.0,
                    "status": "OPERATIONAL" # Sets the default state
                }
            else:
                state_manager.satellites[obj.id]["r"] = pos
                state_manager.satellites[obj.id]["v"] = vel
            count+=1
   
    cdm_count = 0
    
    return {
    "status": "ACK",
    "processed_count": count,
    "active_cdm_warnings": cdm_count # Returns everything within 5km

}

# --- Models for Step Request ---

class StepRequest(BaseModel):
    step_seconds: int

@app.post("/api/simulate/step")

async def simulation_step(data: StepRequest):

    start_dt = parse_time(state_manager.current_time)

    total_seconds = data.step_seconds

    dt = 60.0  # Integration step size

   

    # Initialize counters for the response

    collisions_detected = 0

    maneuvers_executed = 0

    # Main Simulation Loop

    for elapsed in range(0, total_seconds, int(dt)):

        current_step_size = min(dt, total_seconds - elapsed)

       

        # Calculate current 'now' and the specific 'next_tick' for this window

        now = start_dt + timedelta(seconds=elapsed)

        next_tick = now + timedelta(seconds=current_step_size)



        # --- 1. MANEUVER EXECUTION ---

        for sat_id, active_burns in state_manager.scheduled_burns.items():

            sat = state_manager.satellites.get(sat_id)

            if not sat or sat.get("status") == "RETIRED":

                continue

            for burn in active_burns[:]:

                b_time = parse_time(burn.burnTime)

               

                # Execute if burn time falls within this 60s window

                if now <= b_time < next_tick:

                    sat = state_manager.satellites[sat_id]

                    # Check if already retired

                    if sat.get("status") == "RETIRED":

                        active_burns.remove(burn)

                        continue

                    # Calculate required fuel BEFORE applying the burn

                    dv_mag = np.linalg.norm([burn.deltaV_vector.x, burn.deltaV_vector.y, burn.deltaV_vector.z])

                    m_required = calculate_fuel_spent(sat["mass"], dv_mag)



                    # --- THE FUEL GUARD ---

                    if (sat["fuel"] - m_required)>=2.5:

                        # 1. Apply Physics

                        dv_vector = np.array([burn.deltaV_vector.x, burn.deltaV_vector.y, burn.deltaV_vector.z])

                        sat["v"] = apply_burn(sat["r"], sat["v"], dv_vector)

                        # 2. Update Mass and Fuel (Subtract precisely what was calculated)

                        sat["mass"] -= m_required

                        sat["fuel"] -= m_required

                        # Log for Cooldown and API response

                        state_manager.last_burn_recorded[sat_id] = b_time

                        maneuvers_executed += 1

                        active_burns.remove(burn)

                        print(f"SUCCESS: Burn executed for {sat_id}. Fuel remaining: {sat['fuel']:.2f}kg")

                   

                    else:

                        # --- CORRECTED GRAVEYARD LOGIC ---

                        sat["status"] = "RETIRED"

                        gy_dv = 0.15 # km/s

                        m_gy = calculate_fuel_spent(sat["mass"], gy_dv)

                       

                        # Use whatever fuel is left for the graveyard boost

                        actual_gy_fuel = min(sat["fuel"], m_gy)

                        sat["v"] = apply_burn(sat["r"], sat["v"], np.array([0, gy_dv, 0]))

                        sat["fuel"] -= actual_gy_fuel

                        sat["mass"] -= actual_gy_fuel

                        active_burns.remove(burn)

                        print(f"RETIRED: {sat_id} moved to Graveyard Orbit.")



        # --- 2. PHYSICS PROPAGATION ---

        # Update Satellites

       

        for sat in state_manager.satellites.values():

   

            # A. PROPAGATE REAL SATELLITE

            new_real = propagate_state(np.concatenate([sat["r"], sat["v"]]), current_step_size)

            sat["r"], sat["v"] = new_real[:3].copy(), new_real[3:].copy()

            if sat.get("status") != "RETIRED":

                nominal_state = np.concatenate([sat["nominal_r"], sat["nominal_v"]])

                new_nom = propagate_state(nominal_state, current_step_size)

                sat["nominal_r"] = new_nom[:3].copy()

                sat["nominal_v"] = new_nom[3:].copy()

            else:

                # Optional: If retired, we can set nominal to the current position

                # so drift calculation becomes 0

                sat["nominal_r"] = sat["r"].copy()



        # Update Debris

        for deb in state_manager.debris.values():

            state = np.concatenate([deb["r"], deb["v"]])

            new_state = propagate_state(state, current_step_size)

            deb["r"], deb["v"] = new_state[:3].copy(), new_state[3:].copy()



        # --- 3. COLLISION DETECTION ---

        # Get current positions for the KD-Tree

        if len(state_manager.satellites) > 0 and len(state_manager.debris) > 0:

            # Use np.vstack to force a clean (N, 3) matrix

            # This prevents the "VisibleDeprecationWarning" or "Object Array" errors

            sat_pos = np.vstack([s["r"] for s in state_manager.satellites.values()])

            deb_pos = np.vstack([d["r"] for d in state_manager.debris.values()])

   

            # Ensure data type is float64 for high-precision physics

            sat_pos = sat_pos.astype(np.float64)

            deb_pos = deb_pos.astype(np.float64)



            # Now find_potential_collisions will receive clean, 2D numeric matrices

            found = find_potential_collisions(sat_pos, deb_pos, threshold=0.100)

            collisions_detected += len(found)

   

    # Update Global Simulation Time

    new_timestamp_dt = start_dt + timedelta(seconds=total_seconds)

    state_manager.current_time = format_time(new_timestamp_dt)



    return {

        "status": "STEP_COMPLETE",

        "new_timestamp": state_manager.current_time,

        "collisions_detected": collisions_detected,

        "maneuvers_executed": maneuvers_executed

    }



class Maneuver(BaseModel):
    burn_id: str
    burnTime: str
    deltaV_vector: Vector3



class ManeuverRequest(BaseModel):
    satelliteId: str
    maneuver_sequence: list[Maneuver]

@app.post("/api/maneuver/schedule")
async def schedule_maneuver(data: ManeuverRequest):

    sat = state_manager.satellites.get(data.satelliteId)

    # 1. PRE-VALIDATION

    if not sat:
        return {
            "status": "REJECTED",
            "validation": {
                "ground_station_los": False,
                "sufficient_fuel": False,
                "projected_mass_remaining_kg": 0.0
            }
        }

    if sat.get("status") == "RETIRED":
        return {"status": "REJECTED",
            "validation": {
                "ground_station_los": False,
                "sufficient_fuel": False,
                "projected_mass_remaining_kg": round(sat["mass"], 2)
            }
        }

    now_dt = parse_time(state_manager.current_time)
    sim_offset = (now_dt - state_manager.epoch_start).total_seconds()

    # 2. LOS CHECK

    is_visible = any(
        calculate_elevation(sat["r"], gs, sim_offset) >= gs['Min_Elevation_Angle_deg']
        for gs in GROUND_STATIONS
    )

    if not is_visible:
        return {
            "status": "REJECTED",
            "validation": {
                "ground_station_los": False,
                "sufficient_fuel": True,
                "projected_mass_remaining_kg": round(sat["mass"], 2)
            }
        }

    # 3. SEQUENCE PREPARATION

    sorted_new_burns = sorted(data.maneuver_sequence, key=lambda x: parse_time(x.burnTime))
    existing_queue = state_manager.scheduled_burns.get(data.satelliteId, [])

    # Determine the time of the very last event (scheduled or recorded)
    if existing_queue:
        # Accessing via .burnTime (Pydantic object)
        last_event_time = parse_time(existing_queue[-1].burnTime)

    else:
        last_event_time = state_manager.last_burn_recorded.get(
            data.satelliteId,
            now_dt - timedelta(seconds=601)
        )

    # 4. COMPREHENSIVE FUEL & COOLDOWN VALIDATION
    temp_mass = sat["mass"]

    for b in existing_queue:
        dv = np.linalg.norm([b.deltaV_vector.x, b.deltaV_vector.y, b.deltaV_vector.z])
        temp_mass -= calculate_fuel_spent(temp_mass, dv)

    # Now, validate the NEW burns

    new_sequence_fuel = 0.0
    for burn in sorted_new_burns:

        burn_dt = parse_time(burn.burnTime)

        # A. Latency (10s)

        if burn_dt < now_dt + timedelta(seconds=10):
            return {"status": "REJECTED",
                "validation": {
                    "ground_station_los": True,
                    "sufficient_fuel": True,
                    "projected_mass_remaining_kg": round(temp_mass, 2)
                }
            }

        # B. Cooldown (600s)
        if (burn_dt - last_event_time).total_seconds() < 600:
            return {"status": "REJECTED",
                "validation": {
                    "ground_station_los": True,
                    "sufficient_fuel": True,
                    "projected_mass_remaining_kg": round(temp_mass, 2)
                }
            }

        # C. Thruster Limit (15m/s)
        dv_mag = np.linalg.norm([burn.deltaV_vector.x, burn.deltaV_vector.y, burn.deltaV_vector.z])
        if dv_mag > 0.015:
            return{
                "status": "REJECTED",
                "validation": {
                    "ground_station_los": True,
                    "sufficient_fuel": True,
                    "projected_mass_remaining_kg": round(temp_mass, 2)
                }
            }
    
        # D. Incremental Fuel Math
        burn_fuel = calculate_fuel_spent(temp_mass, dv_mag)
        new_sequence_fuel += burn_fuel
        temp_mass -= burn_fuel # Mass drops for the next burn in the sequence
        last_event_time = burn_dt



    # 5. FINAL FUEL GUARD (Check against current available fuel)

    
    current_fuel = sat["fuel"]

    projected_fuel_after_all = current_fuel - (sat["mass"] - temp_mass)

    if projected_fuel_after_all < 2.5:
        return {
            "status": "REJECTED",
            "validation": {
                "ground_station_los": True,
                "sufficient_fuel": False,
                "projected_mass_remaining_kg": round(temp_mass, 2)
            }
        }

    # 6. COMMIT

    if data.satelliteId not in state_manager.scheduled_burns:
        state_manager.scheduled_burns[data.satelliteId] = []
    state_manager.scheduled_burns[data.satelliteId].extend(sorted_new_burns)
    
    
    return {
        "status": "SCHEDULED",
        "validation": {
            "ground_station_los": True,
            "sufficient_fuel": True,
            "projected_fuel_remaining_kg": round(projected_fuel_after_all, 3)
        }
    }
    
# --- NEW VISUALIZATION ENDPOINTS ---

@app.get("/api/visualization/snapshot")
async def get_snapshot():
    """
    Section 6.3: Optimized Snapshot for PixiJS Frontend
    """
    satellites_out = []
    for s_id, s in state_manager.satellites.items():
        lat, lon, alt = cartesian_to_lat_lon(s["r"])
        satellites_out.append({
            "id": s_id,
            "lat": lat,
            "lon": lon,
            "fuel_kg": round(s["fuel"], 2),
            "status": s["status"]
        })

    # Optimized debris cloud: [ID, Lat, Lon, Alt]
    debris_out = []
    for d_id, d in state_manager.debris.items():
        lat, lon, alt = cartesian_to_lat_lon(d["r"])
        debris_out.append([d_id, lat, lon, alt])

    return {
        "timestamp": state_manager.current_time,
        "satellites": satellites_out,
        "debris_cloud": debris_out
    }

@app.get("/api/alerts/proximity/{satellite_id}")
async def get_proximity_data(satellite_id: str):
    target = state_manager.satellites.get(satellite_id)
    if not target:
        return {"error": "Satellite not found"}
        
    proximity_list = []
    
    # 1. Extract debris data for the KDTree
    debris_ids = list(state_manager.debris.keys())
    if not debris_ids:
        return []
        
    deb_pos = np.vstack([d["r"] for d in state_manager.debris.values()]).astype(np.float64)
    
    # 2. Use KD-Tree to grab only debris within 10km (Super fast!)
    tree = KDTree(deb_pos)
    # query_radius expects a 2D array for the target
    indices = tree.query_radius(target["r"].reshape(1, -1), r=10.0)[0]
    
    # 3. Get the RTN matrix to project 3D space onto the satellite's horizon
    rtn_matrix = get_rtn_matrix(target["r"], target["v"])

    for idx in indices:
        d_id = debris_ids[idx]
        deb_r = deb_pos[idx]
        
        rel_vec_eci = deb_r - target["r"]
        dist = np.linalg.norm(rel_vec_eci)
        
        # Transform the relative ECI vector into the satellite's local RTN frame
        rel_vec_rtn = rtn_matrix @ rel_vec_eci
        
        # We use Radial (Up/Down) and Transverse (Forward/Backward) for our 2D radar!
        proximity_list.append({
            "id": d_id,
            "relX": round(rel_vec_rtn[1], 3), # Transverse (along-track)
            "relY": round(rel_vec_rtn[0], 3), # Radial (height difference)
            "dist": round(dist, 3) # True 3D distance
        })
            
    return proximity_list

@app.get("/api/visualization/trajectory/{satellite_id}")
async def get_satellite_trajectory(satellite_id: str):
    target = state_manager.satellites.get(satellite_id)
    if not target:
        return {"error": "Satellite not found"}
        
    r0 = target["r"] # Current position vector
    v0 = target["v"] # Current velocity vector
    
    # Standard gravitational parameter for Earth (km^3/s^2)
    mu = 398600.44 
    
    # Calculate orbital parameters
    r_mag = np.linalg.norm(r0)
    v_mag = np.linalg.norm(v0)
    
    # Specific angular momentum
    h = np.cross(r0, v0)
    h_mag = np.linalg.norm(h)
    
    # Semi-major axis
    a = 1.0 / (2.0 / r_mag - (v_mag**2) / mu)
    
    past_points = []
    future_points = []
    
    # Earth rotation speed in radians per second
    earth_rotation_rate = 7.2921159e-5
    
    # Calculate positions for every 2 minutes across 90 minutes (45 steps)
    for step in range(-45, 46):
        dt = step * 120 # 2 minutes in seconds
        
        # Kepler propagation (Approximated $f$ and $g$ functions for short windows)
        f = 1.0 - (mu / (r_mag**3)) * (dt**2) / 2.0
        g = dt - (mu / (r_mag**3)) * (dt**3) / 6.0
        
        # New inertial position
        r_new = f * r0 + g * v0
        
        # Factor in Earth's rotation so the ground track curves!
        theta = earth_rotation_rate * dt
        rotation_matrix = np.array([
            [np.cos(theta), np.sin(theta), 0],
            [-np.sin(theta), np.cos(theta), 0],
            [0, 0, 1]
        ])
        r_rotated = rotation_matrix.dot(r_new)
        
        lat, lon,alt = cartesian_to_lat_lon(r_rotated)
        
        point = {"lat": round(lat, 3), "lon": round(lon, 3)}
        
        if step < 0:
            past_points.append(point)
        elif step > 0:
            future_points.append(point)
        else:
            # Current point goes in both to connect the lines
            past_points.append(point)
            future_points.append(point)
            
    return {
        "past_points": past_points,
        "future_points": future_points
    }

@app.get("/api/visualization/timeline")
async def get_maneuver_timeline():
    """
    Fetches all scheduled maneuvers across all satellites 
    so the frontend timeline can display them!
    """
    timeline_events = []
    
    # Loop through all scheduled burns in memory
    for sat_id, burns in state_manager.scheduled_burns.items():
        for burn in burns:
            # Calculate the magnitude of the burn
            dv_mag = np.linalg.norm([
                burn.deltaV_vector.x, 
                burn.deltaV_vector.y, 
                burn.deltaV_vector.z
            ])
            
            timeline_events.append({
                "satellite_id": sat_id,
                "burn_id": burn.burn_id,
                "time": burn.burnTime,
                "deltaV": round(dv_mag, 4),
                "type": "AUTOMATED" if "AUTO_EVADE" in burn.burn_id else "MANUAL"
            })
            
    # Sort them in order so the earliest maneuvers appear first
    timeline_events.sort(key=lambda x: parse_time(x["time"]))
    
    return {
        "current_sim_time": state_manager.current_time,
        "schedule": timeline_events
    }
app.mount("/", StaticFiles(directory="orbital-insight", html=True), name="static")