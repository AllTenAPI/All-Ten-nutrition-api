#!/bin/bash
echo "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "Starting the application..."
gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 