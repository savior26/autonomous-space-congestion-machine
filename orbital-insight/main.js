import './style.css';
import axios from 'axios';
import { MapEngine } from './mapEngine';

const API_BASE = window.location.hostname === 'localhost' 
    ? 'http://localhost:8000/api' 
    : `${window.location.origin}/api`;
let map;
let activeSatelliteId = null;

async function init() {
    const mapContainer = document.getElementById('map-view');
    
    if (!mapContainer) {
        console.error("❌ Critical Error: 'map-view' element not found in the DOM.");
        return;
    }
    // 1. Initialize Pixi Engine
    map = new MapEngine();
    await map.init('map-view');

    // Auto-select first target if none is set
    activeSatelliteId = "SAT-001"; 

    // 2. Single Unified Polling Loop (Matches the 6.3 Snapshot Endpoint)
    setInterval(async () => {
        try {
            const res = await axios.get(`${API_BASE}/visualization/snapshot`);
            const data = res.data;

            const timelineRes = await axios.get(`${API_BASE}/visualization/timeline`);
            const timelineData = timelineRes.data;

            // Update all modules from the same data packet
            // New way: Now it knows which satellite to highlight!
            if (map) map.renderSnapshot(data, activeSatelliteId);
            updateUI(data);

            updateTimeline(data, timelineData);

            updateSystemTime(data.timestamp);
            drawCostAnalysisGraph(data.satellites);
            if (activeSatelliteId) {
                updateTelemetryAndRadar(activeSatelliteId, data.satellites);
            }

        } catch (err) {
            console.error("📡 GROUND STATION DISCONNECT:", err.message);
        }
    }, 1000);
}

/**
 * MODULE: RESOURCE HEATMAPS (6.2 Guidelines)
 */
function updateUI(data) {
    const list = document.getElementById('sat-list');
    if (!list) return;

    list.innerHTML = data.satellites.map(s => {
        const fuelPercent = Math.max(0, Math.min(100, (s.fuel_kg / 50) * 100));
        
        // Heatmap Logic: Shifts colors dynamically based on fuel health
        let heatColor = '#00ff88'; 
        if (fuelPercent < 30) heatColor = '#ff0055'; 
        else if (fuelPercent < 60) heatColor = '#ffdd00'; 

        const statusColor = s.status === 'OPERATIONAL' || s.status === 'NOMINAL' ? '#00f2ff' : '#ff0055';
        const activeClass = s.id === activeSatelliteId ? 'active-sat' : '';

        return `
            <div class="sat-item ${activeClass}" 
                 style="border-left: 3px solid ${statusColor}" 
                 onclick="window.selectSatellite('${s.id}')">
                <div class="sat-info">
                    <span class="sat-id">${s.id}</span>
                    <small style="color: ${statusColor}">${s.status}</small>
                </div>
                <div class="fuel-container">
                    <div class="fuel-bar" style="width: ${fuelPercent}%; background: ${heatColor}; box-shadow: 0 0 8px ${heatColor};"></div>
                </div>
                <div class="sat-stats">
                    <small style="color: ${heatColor}">FUEL: ${s.fuel_kg.toFixed(2)} kg</small>
                </div>
            </div>
        `;
    }).join('');
}

window.selectSatellite = (id) => {
    activeSatelliteId = id;
    console.log("Selected target:", id);
};

/**
 * MODULE: TELEMETRY & CONJUNCTION RADAR 
 */
async function updateTelemetryAndRadar(satId, satellitesArray) {
    const currentSat = satellitesArray.find(s => s.id === satId);
    
    if (currentSat) {
        document.getElementById('tel-lat').innerText = currentSat.lat.toFixed(4) + '°';
        document.getElementById('tel-lon').innerText = currentSat.lon.toFixed(4) + '°';
        
        // FIX: Check for both 'fuel_kg' AND 'mfuel' just in case!
        const fuel = currentSat.fuel_kg !== undefined ? currentSat.fuel_kg : currentSat.mfuel;
        document.getElementById('tel-fuel').innerText = fuel !== undefined ? `${fuel.toFixed(1)} kg` : '--';
        
        const statusEl = document.getElementById('tel-status');
        statusEl.innerText = currentSat.status;
        
        // Color-code the telemetry text based on conditions
        statusEl.className = ''; 
        if (currentSat.status === 'RETIRED' || currentSat.status === 'CRITICAL') statusEl.classList.add('critical');
        else if (currentSat.status === 'WARNING') statusEl.classList.add('warning');
    }

    // NEW: Draw the Cost Analysis Graph directly below!
    drawCostAnalysisGraph(satellitesArray);

    try {
        const res = await axios.get(`${API_BASE}/alerts/proximity/${satId}`);
        let radarData = res.data;
        
        // FAILSAFE: If the backend sent an object instead of a direct array, 
        // extract the array from its keys.
        if (!Array.isArray(radarData)) {
            radarData = radarData.active_alerts || radarData.proximity_list || [];
        }
        drawBullseye(radarData);
    } catch (e) {
        console.error("Radar failed:", e);
    }
    // Inside updateTelemetryAndRadar(satId, satellitesArray)...
    try {
        const trajRes = await axios.get(`${API_BASE}/visualization/trajectory/${satId}`);
        const trajData = trajRes.data; // 👈 Extract the data properly!
        
        if (map && trajData.past_points && trajData.future_points) {
            map.drawOrbitPaths(trajData.past_points, trajData.future_points);
        }
    } catch (e) {
        console.error("Trajectory failed:", e);
    }
}

/**
 * NEW MODULE: Δv COST ANALYSIS GRAPH
 * Plots "Fuel Consumed" vs "Collisions Avoided"
 */
function drawCostAnalysisGraph(satellitesArray) {
    const canvas = document.getElementById('cost-graph');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    
    // Dynamically match the styled width and height of the container
    const rect = canvas.parentElement.getBoundingClientRect();
    canvas.width = rect.width;
    canvas.height = rect.height;
    
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    
    const padding = 25;
    const graphWidth = canvas.width - (padding * 2);
    const graphHeight = canvas.height - (padding * 2);
    
    // Draw Grid Axes
    ctx.strokeStyle = 'rgba(0, 242, 255, 0.2)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padding, padding);
    ctx.lineTo(padding, canvas.height - padding);
    ctx.lineTo(canvas.width - padding, canvas.height - padding);
    ctx.stroke();
    
    // Axis Labels
    ctx.fillStyle = 'rgba(0, 242, 255, 0.6)';
    ctx.font = '9px monospace';
    ctx.textAlign = 'center';
    ctx.fillText('Collisions Avoided →', canvas.width / 2, canvas.height - 5);
    
    ctx.save();
    ctx.translate(10, canvas.height / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText('Fuel (kg) →', 0, 0);
    ctx.restore();
    
    // Gather and sort plot points
    let points = satellitesArray.map((s, index) => {
        const fuel = s.fuel_kg !== undefined ? s.fuel_kg : s.mfuel;
        const fuelConsumed = Math.max(0, 50 - fuel); 
        const simulatedAvoided = Math.floor(fuelConsumed * 0.8) + (index % 3); 
        
        return {
            x: padding + Math.min(1, simulatedAvoided / 20) * graphWidth, 
            y: (canvas.height - padding) - Math.min(1, fuelConsumed / 50) * graphHeight
        };
    });
    
    points.sort((a, b) => a.x - b.x);
    
    if (points.length > 1) {
        // Draw the line plot
        ctx.beginPath();
        ctx.moveTo(points[0].x, points[0].y);
        for (let i = 1; i < points.length; i++) {
            ctx.lineTo(points[i].x, points[i].y);
        }
        ctx.strokeStyle = '#00ff88';
        ctx.lineWidth = 2;
        ctx.stroke();
        
        // Draw physical node points
        points.forEach(pt => {
            ctx.beginPath();
            ctx.arc(pt.x, pt.y, 3, 0, Math.PI * 2);
            ctx.fillStyle = '#00f2ff';
            ctx.fill();
        });
    }
}
function drawBullseye(alerts) {
    const canvas = document.getElementById('bullseye');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    
    const center = canvas.width / 2;
    const maxRadius = center - 25; // Leaving padding for degree labels
    
    // Clear canvas
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    
    // 1. Draw Polar Grid Rings
    ctx.strokeStyle = 'rgba(0, 242, 255, 0.2)';
    ctx.lineWidth = 1;
    for (let r = 0.33; r <= 1.0; r += 0.33) {
        ctx.beginPath();
        ctx.arc(center, center, maxRadius * r, 0, Math.PI * 2);
        ctx.stroke();
    }
    
    // 2. Draw Crosshairs & Degree Labels
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.font = '10px monospace';
    
    for (let angleDeg = 0; angleDeg < 360; angleDeg += 30) {
        const rad = angleDeg * Math.PI / 180;
        
        // Draw the line
        ctx.beginPath();
        ctx.moveTo(center, center);
        ctx.lineTo(center + maxRadius * Math.cos(rad), center + maxRadius * Math.sin(rad));
        ctx.stroke();
        
        // Draw the text slightly outside the grid
        ctx.fillStyle = 'rgba(0, 242, 255, 0.6)';
        const textX = center + (maxRadius + 15) * Math.cos(rad);
        const textY = center + (maxRadius + 15) * Math.sin(rad);
        
        ctx.fillText(`${angleDeg}°`, textX, textY);
    }

    // 3. Map each debris hazard point
    // 3. Map each debris hazard point
    alerts.forEach(alert => {
        const distInMeters = alert.dist * 1000;
        
        // Use the proper trig coordinates from backend
        const angle = Math.atan2(alert.relY, alert.relX);
        
        // Scale relative to 10,000 meters (10km) to match the backend filter
        const normalizedDist = Math.min(distInMeters / 10000.0, 1.0); 
        const radius = normalizedDist * maxRadius;
        
        const x = center + radius * Math.cos(angle);
        const y = center + radius * Math.sin(angle);
        
        // 4. Color Code based on the 5km guidelines
        let color = "#00ff88"; // Safe (> 5km)
        if (distInMeters < 1000.0) {
            color = "#ff0055"; // Critical under 1km
        } else if (distInMeters < 5000.0) {
            color = "#ffdd00"; // Warning under 5km
        }
        
        // ... rest of your canvas drawing code stays exactly the same!
        
        // Draw connecting vector line to center
        ctx.beginPath();
        ctx.moveTo(center, center);
        ctx.lineTo(x, y);
        ctx.strokeStyle = color;
        ctx.lineWidth = 0.5;
        ctx.setLineDash([4, 4]); 
        ctx.stroke();
        ctx.setLineDash([]); 
        
        // Draw the debris target dot
        ctx.fillStyle = color;
        ctx.shadowBlur = 10;
        ctx.shadowColor = color;
        
        ctx.beginPath();
        ctx.arc(x, y, 4, 0, Math.PI * 2);
        ctx.fill();
        ctx.shadowBlur = 0; 
    });

    // 5. Draw selected satellite at center
    ctx.fillStyle = '#00f2ff';
    ctx.shadowBlur = 15;
    ctx.shadowColor = '#00f2ff';
    ctx.beginPath();
    ctx.arc(center, center, 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.shadowBlur = 0; 
}
/**
 * MODULE: MANEUVER TIMELINE (Gantt Scheduler)
 */
/**
 * MODULE: MANEUVER TIMELINE (Gantt Scheduler)
 */
function updateTimeline(data, timelineData) {
    const container = document.querySelector('.timeline-container');
    if (!container || !timelineData) return;

    let html = `<div class="timeline-grid">`;
    const currentTime = new Date(data.timestamp).getTime();
    
    // Defines a 30-minute window width for the entire scale
    const windowMs = 30 * 60 * 1000; 

    data.satellites.forEach(sat => {
        html += `
            <div class="timeline-row">
                <div class="sat-label">${sat.id}</div>
                <div class="timeline-track">`;

        // ⭐ NEW: Loop through actual scheduled burns from the backend!
        const activeBurns = timelineData.schedule.filter(b => b.satellite_id === sat.id);

        activeBurns.forEach(burn => {
            const burnStart = new Date(burn.time).getTime();
            const burnEnd = burnStart + (5 * 60 * 1000); // 5 minutes duration
            
            // Calculate percentage positions on the track
            const burnOffset = ((burnStart - currentTime) / windowMs) * 100;
            const burnWidth = ((burnEnd - burnStart) / windowMs) * 100;

            // Mandatory 600-second cooldown block
            const cooldownOffset = burnOffset + burnWidth;
            const cooldownWidth = (600000 / windowMs) * 100; 

            // Only draw it if it's within our visible 30-minute window
            if (burnOffset >= 0 && burnOffset < 100) {
                html += `<div class="event-block burn-active" style="left: ${burnOffset}%; width: ${Math.min(burnWidth, 100 - burnOffset)}%">Burn (${burn.deltaV} km/s)</div>`;
            }
            if (cooldownOffset >= 0 && cooldownOffset < 100) {
                html += `<div class="event-block cooldown-zone" style="left: ${cooldownOffset}%; width: ${Math.min(cooldownWidth, 100 - cooldownOffset)}%">CD</div>`;
            }
        });
        
        html += `
                </div>
            </div>
        `;
    });

    html += `</div>`;
    container.innerHTML = html;
}
function updateSystemTime(ts) {
    const clock = document.getElementById('system-time');
    if (clock) {
        const date = new Date(ts);
        clock.innerText = `SYNC_OK: ${date.toUTCString().slice(17, 25)}`;
    }
}


document.addEventListener('DOMContentLoaded', init);