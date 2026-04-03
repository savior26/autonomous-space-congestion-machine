import * as PIXI from 'pixi.js';

export class MapEngine {
    constructor() {
        this.app = new PIXI.Application();
        this.initialized = false;
    }

    async init(containerId) {
        this.container = document.getElementById(containerId);

        if (!this.container) {
            throw new Error(`Container #${containerId} not found`);
        }

        // v8 async initialization
        await this.app.init({
            width: this.container.clientWidth,
            height: this.container.clientHeight,
            backgroundColor: 0x050a0f,
            antialias: true,
            resolution: window.devicePixelRatio || 1,
            autoDensity: true
        });
        
        this.container.appendChild(this.app.canvas);

        // Layers
        this.debrisLayer = new PIXI.Container(); 
        this.satLayer = new PIXI.Graphics();
        
        // FIX 2: Better Asset Loading
        // Inside your init() function in mapEngine.js:
        try {
            const texture = await PIXI.Assets.load('https://upload.wikimedia.org/wikipedia/commons/8/83/Equirectangular_projection_SW.jpg');
            this.bg = new PIXI.Sprite(texture);

            // FIX: Force anchor to center or top-left and match screen exactly
            this.bg.anchor.set(0); 
            this.bg.x = 0;
            this.bg.y = 0;
            this.bg.width = this.app.screen.width;
            this.bg.height = this.app.screen.height;
            this.bg.alpha = 0.4; // Boost alpha to 0.4 so it's clearly visible

            // Ensure background is at the very bottom (index 0)
            this.app.stage.addChildAt(this.bg, 0); 
    
            console.log("✅ Map Texture Loaded & Scaled to:", this.app.screen.width, "x", this.app.screen.height);
        } catch (e) {
            console.error("❌ Map Texture Failed:", e);
        }

        this.app.stage.addChild(this.bg, this.debrisLayer, this.satLayer);
        // Inside init(containerId) in mapEngine.js right before this.initialized = true

        // 1. Make the stage interactive
        this.app.stage.eventMode = 'static';
        
        // 2. Set center pivot for zooming
        this.app.stage.pivot.set(this.app.screen.width / 2, this.app.screen.height / 2);
        this.app.stage.position.set(this.app.screen.width / 2, this.app.screen.height / 2);

        // 3. Wheel event listener
        this.container.addEventListener('wheel', (e) => {
            e.preventDefault();
            
            const zoomFactor = 1.1;
            let newScale = this.app.stage.scale.x;
            
            if (e.deltaY < 0) {
                newScale *= zoomFactor; // Zoom in
            } else {
                newScale /= zoomFactor; // Zoom out
            }
            
            // Clamp zoom to prevent flipping or getting too small/large
            newScale = Math.max(1, Math.min(newScale, 10));
            
            this.app.stage.scale.set(newScale);
        }, { passive: false });
        // 3. Wheel event listener (Keep your existing wheel code here...)

        // 4. NEW: Clickable Button Listeners
        const zoomInBtn = document.getElementById('zoom-in');
        const zoomOutBtn = document.getElementById('zoom-out');

        const applyButtonZoom = (zoomIn) => {
            const zoomFactor = 1.3;
            let newScale = this.app.stage.scale.x;

            if (zoomIn) {
                newScale *= zoomFactor;
            } else {
                newScale /= zoomFactor;
            }

            // Clamp zoom to prevent flipping or getting too small/large (same as wheel)
            newScale = Math.max(1, Math.min(newScale, 10));
            this.app.stage.scale.set(newScale);
        };

        if (zoomInBtn) zoomInBtn.addEventListener('click', () => applyButtonZoom(true));
        if (zoomOutBtn) zoomOutBtn.addEventListener('click', () => applyButtonZoom(false));
        this.initialized = true;
    }

    project(lat, lon) {
        const w = this.app.screen.width || 800;
        const h = this.app.screen.height || 600;
        const x = (lon + 180) * (w / 360);
        const y = (90 - lat) * (h / 180);
        return { x, y };
    }

    drawOrbitPaths(pastPoints, futurePoints) {
        if (!this.orbitLayer) {
            this.orbitLayer = new PIXI.Graphics();
            // Pushes it to the absolute top of the stack so nothing hides it
            this.app.stage.addChild(this.orbitLayer); 
        }
        
        this.orbitLayer.clear();
        
        // Helper function to draw connected paths with smart edge-wrapping
        const tracePath = (points, isPast) => {
            if (!points || points.length < 2) return;
            
            this.orbitLayer.beginPath();
            let startPos = this.project(points[0].lat, points[0].lon);
            this.orbitLayer.moveTo(startPos.x, startPos.y);
            
            const w = this.app.screen.width || 800;
            
            for (let i = 1; i < points.length; i++) {
                const prev = points[i - 1];
                const curr = points[i];
                const pos = this.project(curr.lat, curr.lon);
                
                // Check if the path crossed the Date Line (left/right map edges)
                if (Math.abs(curr.lon - prev.lon) > 180) {
                    const prevPos = this.project(prev.lat, prev.lon);
                    
                    // Predict edge intersection to close the gap perfectly
                    if (curr.lon < prev.lon) {
                        // Crossing left to right
                        this.orbitLayer.lineTo(w, (prevPos.y + pos.y) / 2);
                        this.orbitLayer.moveTo(0, (prevPos.y + pos.y) / 2);
                    } else {
                        // Crossing right to left
                        this.orbitLayer.lineTo(0, (prevPos.y + pos.y) / 2);
                        this.orbitLayer.moveTo(w, (prevPos.y + pos.y) / 2);
                    }
                }
                
                // If it's the future path, we draw every even step to simulate a dash
                if (!isPast && i % 2 !== 0) {
                    this.orbitLayer.moveTo(pos.x, pos.y);
                } else {
                    this.orbitLayer.lineTo(pos.x, pos.y);
                }
            }
            
            // Apply line styles based on past/future paths
            if (isPast) {
                this.orbitLayer.stroke({ width: 3, color: 0x00ff88, alpha: 0.9 });
            } else {
                this.orbitLayer.stroke({ width: 3, color: 0x00ff88, alpha: 0.9, cap: 'round' });
            }
        };

        // Execute drawing
        tracePath(pastPoints, true);
        tracePath(futurePoints, false);
    }

    renderSnapshot(data, activeSatelliteId = null) {
        if (!this.initialized) return;

        // 1. Draw the dark terminator shadow based on current simulation timestamp
        this.drawTerminator(data.timestamp);

        // Clear previous frame
        this.satLayer.clear();
        this.debrisLayer.removeChildren();

        // 2. High performance render of 10k+ debris objects
        data.debris_cloud.forEach(d => {
            const pos = this.project(d[1], d[2]);
            const dot = new PIXI.Graphics();
            
            dot.rect(0, 0, 1.5, 1.5);
            dot.fill(0x666666);
            
            dot.position.set(pos.x, pos.y);
            this.debrisLayer.addChild(dot);
        });

        // 3. Constellation & Satellite Rendering
        data.satellites.forEach(sat => {
            const pos = this.project(sat.lat, sat.lon);
            
            const isSelected = sat.id === activeSatelliteId;
            
            if (isSelected) {
                // Draw a large, glowing radar pulse for the selected satellite
                this.satLayer.circle(pos.x, pos.y, 10);
                this.satLayer.fill({ color: 0x00f2ff, alpha: 0.25 });
                this.satLayer.stroke({ width: 1.5, color: 0x00f2ff, alpha: 0.8 });
                
                // Solid center for the selected satellite
                this.satLayer.circle(pos.x, pos.y, 4.5);
                this.satLayer.fill(0x00f2ff);
                this.satLayer.stroke({ width: 1, color: 0xffffff, alpha: 0.9 });
            } else {
                // Standard marker for the rest of the active constellation
                this.satLayer.circle(pos.x, pos.y, 3.5);
                this.satLayer.fill(0x00bbff);
                this.satLayer.stroke({ width: 1, color: 0x00f2ff, alpha: 0.4 });
            }
        });
    }

    // 1. Calculate the Sun's sub-point to find the center of the daylight hemisphere
    calculateSunPosition(date = new Date()) {
        const julianDate = (date.getTime() / 86400000) + 2440587.5;
        const daysSinceJ2000 = julianDate - 2451545.0;

        // Mean anomaly of the sun
        const g = (357.529 + 0.98560028 * daysSinceJ2000) % 360;
        // Mean longitude of the sun
        const q = (280.459 + 0.98564736 * daysSinceJ2000) % 360;
        // Geocentric apparent ecliptic longitude
        const L = (q + 1.915 * Math.sin(g * Math.PI / 180) + 0.020 * Math.sin(2 * g * Math.PI / 180)) % 360;

        // Obliquity of the ecliptic
        const e = 23.439 - 0.00000036 * daysSinceJ2000;

        // Celestial coordinates
        const ra = Math.atan2(Math.cos(e * Math.PI / 180) * Math.sin(L * Math.PI / 180), Math.cos(L * Math.PI / 180)) * 180 / Math.PI;
        const dec = Math.asin(Math.sin(e * Math.PI / 180) * Math.sin(L * Math.PI / 180)) * 180 / Math.PI;

        // Convert Right Ascension to Greenwich Hour Angle (Longitude)
        const gmst = (18.697374558 + 24.06570982441908 * daysSinceJ2000) % 24;
        const lon = (ra - gmst * 15) % 360;
        const normalizedLon = lon > 180 ? lon - 360 : (lon < -180 ? lon + 360 : lon);

        return { lat: dec, lon: normalizedLon };
    }

    // 2. Draw the polygon overlay for the night zone
    drawTerminator(timestamp) {
        if (!this.terminatorLayer) {
            this.terminatorLayer = new PIXI.Graphics();
            // Insert it above the background map but below the satellites/debris
            this.app.stage.addChildAt(this.terminatorLayer, 1);
        }
        
        this.terminatorLayer.clear();
        
        const date = timestamp ? new Date(timestamp) : new Date();
        const sun = this.calculateSunPosition(date);
        
        const points = [];
        const sunLatRad = sun.lat * Math.PI / 180;
        const sunLonRad = sun.lon * Math.PI / 180;

        // Trace points along the great circle boundary of night/day
        for (let lon = -180; lon <= 180; lon += 5) {
            const lonRad = lon * Math.PI / 180;
            const latRad = Math.atan(-Math.cos(lonRad - sunLonRad) / Math.tan(sunLatRad));
            const lat = latRad * 180 / Math.PI;
            
            const pos = this.project(lat, lon);
            points.push(pos.x, pos.y);
        }

        // Complete the polygon wrapping to the bottom or top of the map 
        // to fill the correct hemisphere based on the season (Summer/Winter)
        const mapW = this.app.screen.width;
        const mapH = this.app.screen.height;
        
        if (sun.lat > 0) {
            // Sun is in Northern hemisphere, Southern hemisphere has longer nights
            points.push(mapW, mapH, 0, mapH);
        } else {
            // Sun is in Southern hemisphere, Northern hemisphere has longer nights
            points.push(mapW, 0, 0, 0);
        }

        // Draw polygon with a highly styled dark night overlay
        this.terminatorLayer.poly(points);
        this.terminatorLayer.fill({ color: 0x050a14, alpha: 0.65 });
    }

}

/**
 * 3. BULLSEYE CHART (Standard Canvas API - No changes needed here)
 */
export class BullseyeChart {
    constructor(canvasId) {
        this.canvas = document.getElementById(canvasId);
        if (!this.canvas) return;
        this.ctx = this.canvas.getContext('2d');
        this.size = this.canvas.width;
        this.center = this.size / 2;
    }

    draw(proximityData = []) {
        if (!this.ctx) return;
        const ctx = this.ctx;
        ctx.clearRect(0, 0, this.size, this.size);

        ctx.strokeStyle = 'rgba(0, 242, 255, 0.2)';
        ctx.lineWidth = 1;
        [0.3, 0.6, 0.9].forEach(r => {
            ctx.beginPath();
            ctx.arc(this.center, this.center, this.center * r, 0, Math.PI * 2);
            ctx.stroke();
        });

        ctx.fillStyle = '#00f2ff';
        ctx.shadowBlur = 10;
        ctx.shadowColor = '#00f2ff';
        ctx.fillRect(this.center - 3, this.center - 3, 6, 6);
        ctx.shadowBlur = 0;

        proximityData.forEach(deb => {
            const x = this.center + deb.relX * (this.center / 5); 
            const y = this.center + deb.relY * (this.center / 5);
            
            ctx.fillStyle = deb.dist < 1.0 ? '#ff0055' : '#ffdd00';
            ctx.beginPath();
            ctx.arc(x, y, 3, 0, Math.PI * 2);
            ctx.fill();
        });
    }
}