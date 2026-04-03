# Stage 1: Build the Frontend with Vite
FROM ubuntu:22.04 AS frontend-builder

# Install Node.js
RUN apt-get update && apt-get install -y curl
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
RUN apt-get install -y nodejs

# Move into your actual frontend folder shown in your screenshot
WORKDIR /app/orbital-insight

# Copy frontend files and install dependencies
COPY orbital-insight/package*.json ./
RUN npm install

# Copy the rest of the frontend source and build it
COPY orbital-insight/ ./
RUN npm run build

# Stage 2: Final Image (Python + FastAPI)
FROM ubuntu:22.04

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install Python
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy all backend files (main.py, physics_engine.py, etc.)
COPY . .

# Copy your CSV file explicitly so FastAPI can find it
COPY ground_stations.csv .

# Pull the built static assets from Vite and place them in a 'static' folder
COPY --from=frontend-builder /app/orbital-insight/dist ./static

# Section 8 Requirement: Expose Port 8000
EXPOSE 8000

# Start script running on 0.0.0.0 (Mandatory requirement!)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]