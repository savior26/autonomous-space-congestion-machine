import numpy as np
from sklearn.neighbors import KDTree
from collections import defaultdict
from datetime import datetime
import math

# Physical Constants (As per Section 3.2)
MU = 398600.4418  # Earth's gravitational parameter (km^3/s^2)
R_E = 6378.137    # Earth's equatorial radius (km)
J2 = 1.08263e-3   # J2 perturbation coefficient
G0 = 9.80665      # Standard gravity (m/s^2) for fuel calc

# Satellite Specs (As per Section 5.1)
M_DRY = 500.0     # kg
ISP = 300.0       # seconds

def parse_time(ts_str):
    # 1. 'Z' ko hatayein
    # 2. '.' se split karke sirf pehla hissa (YYYY-MM-DDTHH:MM:SS) lein
    clean_ts = ts_str.replace("Z", "").split(".")[0]
    return datetime.strptime(clean_ts, "%Y-%m-%dT%H:%M:%S")

def format_time(dt_obj):
    return dt_obj.strftime("%Y-%m-%dT%H:%M:%S")+"Z"

# J2 acceleration logic
def get_j2_acceleration(r_vec):
    """
    Calculates the acceleration vector due to J2 perturbation.
    r_vec: [x, y, z] in km
    """
    x, y, z = r_vec
    r_mag = np.linalg.norm(r_vec)
    
    # Pre-calculating common terms for speed
    factor = (1.5) * J2 * MU * (R_E**2) / (r_mag**5)
    z_sq_r_sq = (z**2) / (r_mag**2)
    
    ax = x * (5 * z_sq_r_sq - 1)
    ay = y * (5 * z_sq_r_sq - 1)
    az = z * (5 * z_sq_r_sq - 3)
    
    return factor * np.array([ax, ay, az])

def get_total_acceleration(r_vec):
    """
    Total acceleration = Basic Gravity + J2 Effect
    """
    r_mag = np.linalg.norm(r_vec)
    
    # Basic Two-Body Gravity: a = -mu/r^3 * r
    a_gravity = -(MU / (r_mag**3)) * r_vec
    
    # J2 Perturbation
    a_j2 = get_j2_acceleration(r_vec)
    
    return a_gravity + a_j2

#RK4 integrator(movement engine)
def propagate_state(state, dt):
    """
    Propagates the satellite state forward by dt seconds using RK4.
    state: [x, y, z, vx, vy, vz]
    """
    r_0 = state[:3] # Position
    v_0 = state[3:] # Velocity

    # Step k1
    k1_v = v_0
    k1_a = get_total_acceleration(r_0)

    # Step k2
    k2_v = v_0 + 0.5 * dt * k1_a
    k2_a = get_total_acceleration(r_0 + 0.5 * dt * k1_v)

    # Step k3
    k3_v = v_0 + 0.5 * dt * k2_a
    k3_a = get_total_acceleration(r_0 + 0.5 * dt * k2_v)

    # Step k4
    k4_v = v_0 + dt * k3_a
    k4_a = get_total_acceleration(r_0 + dt * k3_v)

    # Final weighted average to update position and velocity
    new_r = r_0 + (dt / 6.0) * (k1_v + 2*k2_v + 2*k3_v + k4_v)
    new_v = v_0 + (dt / 6.0) * (k1_a + 2*k2_a + 2*k3_a + k4_a)

    return np.concatenate([new_r, new_v])

def find_potential_collisions(satellites_pos, debris_pos, threshold):
    tree = KDTree(debris_pos)
    indices = tree.query_radius(satellites_pos, r=threshold)
    
    results = []
    for sat_idx, debris_indices in enumerate(indices):
        for deb_idx in debris_indices:
            # We already know they are within 'threshold' because of query_radius
            d = np.linalg.norm(satellites_pos[sat_idx] - debris_pos[deb_idx])
            results.append((sat_idx, deb_idx, d))
                
    return results


def get_rtn_matrix(r, v):
    """ECI se RTN mein badalne ke liye Rotation Matrix"""
    u_r = r / np.linalg.norm(r)
    h = np.cross(r, v)
    u_n = h / np.linalg.norm(h)
    u_t = np.cross(u_n, u_r)
    
    # Yeh matrix ECI vector ko RTN mein badal deti hai
    return np.array([u_r, u_t, u_n])

def apply_burn(r_eci, v_eci, dv_rtn):
    """RTN burn ko ECI velocity mein add karna"""
    u_r, u_t, u_n = get_rtn_matrix(r_eci, v_eci)
    
    # Convert RTN burn vector to ECI vector
    dv_eci = (dv_rtn[0] * u_r) + (dv_rtn[1] * u_t) + (dv_rtn[2] * u_n)
    return v_eci + dv_eci
    
def calculate_fuel_spent(m_current, dv_mag):
    """
    Tsiolkovsky Equation: Delta_m = m * (1 - exp(-dv / (Isp * g0)))
    """
    # ISP = 300, G0 = 9.80665
    # m_spent = mass of fuel consumed
    #m_current=initial mass of satellite before burn including fuel mass (wet mass)
    m_spent = m_current * (1 - np.exp(-dv_mag * 1000 / (300.0 * 9.80665))) # dv in km/s to m/s
    return m_spent

W_EARTH = 7.292115e-5 

def get_ecef_from_eci(r_eci, seconds_since_epoch):
    """
    Rotates an ECI position vector into the ECEF frame 
    based on the time elapsed since the simulation start (Epoch).
    """
    theta = W_EARTH * seconds_since_epoch
    c, s = np.cos(theta), np.sin(theta)
    
    # Rotation matrix around the Z-axis (Earth's axis)
    # This converts the inertial frame to the rotating frame
    rotation_matrix = np.array([
        [ c, s, 0],
        [-s, c, 0],
        [ 0, 0, 1]
    ])
    
    return rotation_matrix @ r_eci

def lla_to_ecef(lat, lon, alt_m):
    """
    Converts Latitude, Longitude, and Altitude (meters) to ECEF (km).
    Using a spherical Earth model as permitted by hackathon constraints.
    """
    R_EARTH = 6378.137 # km
    lat_rad = np.radians(lat)
    lon_rad = np.radians(lon)
    alt_km = alt_m / 1000.0
    
    r = R_EARTH + alt_km
    x = r * np.cos(lat_rad) * np.cos(lon_rad)
    y = r * np.cos(lat_rad) * np.sin(lon_rad)
    z = r * np.sin(lat_rad)
    
    return np.array([x, y, z])

def calculate_elevation(sat_eci, gs_lla, seconds_since_epoch):
    """
    Calculates the elevation angle of a satellite relative to a ground station.
    
    Args:
        sat_eci: [x, y, z] position in ECI (km)
        gs_lla: Dict with {'Latitude', 'Longitude', 'Elevation_m'}
        seconds_since_epoch: Time in seconds since simulation start.
        
    Returns:
        float: Elevation angle in degrees.
    """
    # 1. Convert Satellite ECI to ECEF (Account for Earth rotation)
    sat_ecef = get_ecef_from_eci(np.array(sat_eci), seconds_since_epoch)
    
    # 2. Convert Ground Station LLA to ECEF
    gs_ecef = lla_to_ecef(gs_lla['Latitude'], gs_lla['Longitude'], gs_lla['Elevation_m'])
    
    # 3. Calculate the Range Vector (Station -> Satellite)
    rho = sat_ecef - gs_ecef
    
    # 4. Calculate the Zenith Vector (The "Up" direction at the station)
    # In a spherical model, the 'Up' vector is simply the normalized ECEF position of the GS.
    zenith = gs_ecef / np.linalg.norm(gs_ecef)
    
    # 5. Calculate Elevation Angle
    # Angle = 90 - angle between Zenith and Range vector
    rho_unit = rho / np.linalg.norm(rho)
    
    # Dot product gives cos(theta), which is sin(elevation)
    sin_el = np.dot(rho_unit, zenith)
    
    # Clip to avoid math domain errors
    el_rad = np.arcsin(np.clip(sin_el, -1.0, 1.0))
    
    return np.degrees(el_rad)

def cartesian_to_lat_lon(r):
    """
    Converts KM Cartesian [x, y, z] to Geodetic [Lat, Lon, Alt]
    Note: For a hackathon, a simple spherical conversion is often accepted.
    """
    x, y, z = r
    dist_orb = np.linalg.norm(r)
    
    lat = math.degrees(math.asin(z / dist_orb))
    lon = math.degrees(math.atan2(y, x))
    alt = dist_orb - 6371.0  # Earth radius in km
    
    return round(lat, 3), round(lon, 3), round(alt, 2)

def get_grid_cell(position, cell_size=50.0):
    """
    Computes a unique integer tuple for a 3D point based on cell size.
    This acts as a spatial hash.
    """
    x, y, z = position['x'], position['y'], position['z']
    return (int(x // cell_size), int(y // cell_size), int(z // cell_size))

def compute_evasive_maneuver(sat_r, sat_v):
    """
    Computes a simple Delta-V vector to dodge a collision by pushing 
    the satellite forward in its velocity vector direction.
    """
    # 1. Find the unit velocity vector (Prograde direction)
    v_mag = np.linalg.norm(sat_v)
    prograde_unit = sat_v / v_mag
    
    # 2. Apply a standard small delta-v (5 m/s = 0.005 km/s)
    dv_magnitude = 0.005 
    dv_eci = prograde_unit * dv_magnitude
    
    # 3. Convert ECI burn vector to RTN since your app maps it that way
    u_r, u_t, u_n = get_rtn_matrix(sat_r, sat_v)
    dv_r = np.dot(dv_eci, u_r)
    dv_t = np.dot(dv_eci, u_t)
    dv_n = np.dot(dv_eci, u_n)
    
    return np.array([dv_r, dv_t, dv_n])
