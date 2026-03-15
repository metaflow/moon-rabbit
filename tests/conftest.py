import os
import sys

# Add project root to sys.path so tests can import project modules directly (data, storage, etc.)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
