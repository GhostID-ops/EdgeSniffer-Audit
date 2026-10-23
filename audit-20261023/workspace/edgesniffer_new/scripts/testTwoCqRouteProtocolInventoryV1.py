#!/usr/bin/env python3

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import twoCqRouteProtocolInventoryV1 as subject


def main() -> None:
    jupiter = next(iter(subject.denominator.JUPITER))
    venue = "venue111111111111111111111111111111111"
    assert subject.protocol_candidates({jupiter}, {venue}, "ROUTER") == [venue]
    assert subject.protocol_candidates(
        {subject.denominator.PUMPSWAP}, set(), "PUMPSWAP"
    ) == [subject.denominator.PUMPSWAP]
    assert subject.protocol_candidates(
        {subject.denominator.PUMP}, set(), "NON_SOL_PUMP_V2"
    ) == [subject.denominator.PUMP]
    print("PASS")


if __name__ == "__main__":
    main()
