#!/usr/bin/env python3
"""Render only Resources from a blueprint pinned to the documented source."""
import argparse
import json
from pathlib import Path
from refresh_glossary import _refresh_reference


def refresh_resources(root, version, sources):
    return _refresh_reference(root, version, sources, 'resources')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', choices=('released', 'coming-soon'), required=True)
    parser.add_argument('--sources', type=Path, required=True,
                        help='Absolute frozen public source directory with index.json and evidence files')
    args = parser.parse_args()
    if not args.sources.is_absolute():
        parser.error('--sources must be an absolute frozen directory')
    print(json.dumps(refresh_resources(Path(__file__).resolve().parents[1], args.version, args.sources)))


if __name__ == '__main__':
    main()
