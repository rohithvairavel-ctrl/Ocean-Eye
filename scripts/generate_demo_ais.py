import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.datasets import generate_ais

if __name__ == "__main__":
    print(generate_ais())
