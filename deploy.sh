#!/bin/bash
echo "Starting All Ten Nutrition API deployment..."

# Install dependencies
echo "Installing dependencies..."
pip install Flask Flask-CORS gunicorn

# Start the application
echo "Starting the application..."
gunicorn app-simple:app --bind 0.0.0.0:$PORT --workers 1 