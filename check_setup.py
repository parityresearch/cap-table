#!/usr/bin/env python3
"""
Quick start and testing script for the web interface
"""

import subprocess
import sys
from pathlib import Path

def check_python():
    """Verify Python 3.9+"""
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 9):
        print(f"❌ Python 3.9+ required. You have {version.major}.{version.minor}")
        return False
    print(f"✓ Python {version.major}.{version.minor}.{version.micro}")
    return True

def check_dependencies():
    """Check if Flask is installed"""
    try:
        import flask
        print(f"✓ Flask {flask.__version__}")
        return True
    except ImportError:
        print("❌ Flask not installed. Run: pip install -r requirements_web.txt")
        return False

def check_sample_csv():
    """Verify sample CSV exists"""
    sample = Path(__file__).parent / "sample_cap_table.csv"
    if sample.exists():
        print(f"✓ Sample CSV: {sample}")
        return True
    else:
        print(f"⚠ Sample CSV not found at {sample}")
        return False

def main():
    print("🔍 TSE Cap Table Web Interface - Pre-flight Check\n")
    
    checks = [
        ("Python version", check_python),
        ("Flask installed", check_dependencies),
        ("Sample CSV", check_sample_csv),
    ]
    
    results = []
    for name, check_fn in checks:
        print(f"\n📋 {name}...")
        results.append(check_fn())
    
    print("\n" + "="*50)
    if all(results):
        print("✅ All checks passed! Ready to start.")
        print("\n🚀 To start the web server, run:")
        print("   python3 app.py")
        print("\nThen open: http://localhost:5000")
    else:
        print("⚠ Some checks failed. Please fix issues above.")
        sys.exit(1)

if __name__ == "__main__":
    main()
