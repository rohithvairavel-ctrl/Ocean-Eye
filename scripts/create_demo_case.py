import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.datasets import create_demo

if __name__ == "__main__":
    print(create_demo())
