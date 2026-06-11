"""
Entry point for `python -m pipeline`.
"""
from pipeline._cli import parse_args
from pipeline._pipeline import build_pipeline

if __name__ == "__main__":
    build_pipeline(parse_args())
