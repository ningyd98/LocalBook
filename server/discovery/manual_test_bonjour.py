"""
Manual verification script for Bonjour service discovery.

This script tests the BonjourService implementation by:
1. Starting the service on port 3780
2. Keeping it running for 30 seconds
3. During this time, you can verify discovery with:
   dns-sd -B _localbook._tcp

Usage:
    python3 manual_test_bonjour.py

Then in another terminal:
    dns-sd -B _localbook._tcp
"""

import sys
import time
from pathlib import Path

# Make the repository importable regardless of the caller's working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from server import __version__
    from server.discovery.bonjour import BonjourService

    print("Starting BonjourService on port 3780...")
    print(f"Version: {__version__}")
    print()
    print("To verify discovery, open another terminal and run:")
    print("  dns-sd -B _localbook._tcp")
    print()
    print("The service will run for 30 seconds...")
    print()

    service = BonjourService()

    try:
        service.start(port=3780, version=__version__)
        print("✓ Service started successfully")
        print()
        print("Service is now advertising on the local network.")
        print("Waiting 30 seconds...")

        for i in range(30, 0, -1):
            print(f"\r{i} seconds remaining...", end="", flush=True)
            time.sleep(1)

        print("\n\nStopping service...")
        service.stop()
        print("✓ Service stopped")

    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Stopping service...")
        service.stop()
        print("✓ Service stopped")

except ImportError as e:
    print(f"✗ Import error: {e}")
    print()
    print("The zeroconf library is not installed.")
    print("Install it with: uv pip install zeroconf")
    sys.exit(1)
except Exception as e:
    print(f"✗ Error: {e}")
    sys.exit(1)
