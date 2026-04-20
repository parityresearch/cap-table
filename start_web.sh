#!/bin/bash

# TSE Cap Table Web Server Startup Script

echo "🚀 Starting TSE Cap Table Recovery Engine..."
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found. Please install Python 3.9+"
    exit 1
fi

echo "✓ Python $(python3 --version)"

# Check Flask installation
if ! python3 -c "import flask" 2>/dev/null; then
    echo ""
    echo "📦 Installing dependencies..."
    pip install -r requirements_web.txt
fi

echo ""
echo "🌐 Starting Flask server on http://localhost:5000"
echo "Press Ctrl+C to stop"
echo ""

python3 app.py
