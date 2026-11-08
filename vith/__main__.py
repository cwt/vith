"""
Make `python -m vith` an alias for running `vith`.
"""

from .entry_points.run_vith import run

if __name__ == "__main__":
    run()
