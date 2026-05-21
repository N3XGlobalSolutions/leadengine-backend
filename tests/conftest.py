"""Pytest configuration for QBOT backend tests."""

import sys
import os

# Add the parent directory to the path so we can import the app module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set environment variable to use mock services for testing
os.environ.setdefault("USE_MOCK_SERVICES", "true")
