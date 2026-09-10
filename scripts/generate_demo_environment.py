import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.datasets import generate_environment

if __name__ == "__main__":
    generate_environment()
    print("Created datasets/environment/demo.json")
