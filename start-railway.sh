#!/bin/bash

echo "🚀 Starting All Ten Nutrition API on Railway..."

# Get the port from Railway environment
PORT=${PORT:-5000}
echo "📡 Using port: $PORT"

# Start the Railway-optimized app
echo "🔧 Starting server..."
python app-railway.py 