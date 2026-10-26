"""Source-tree convenience entry for the packaged save_generator CLI."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from save_generator import main

if __name__ == '__main__':
    main()
