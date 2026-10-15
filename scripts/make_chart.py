"""Create the chart image included in the project README."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/books_structured.csv")
    parser.add_argument("--output", default="artifacts/price_by_rating.png")
    args = parser.parse_args()
    grouped: dict[int, list[float]] = defaultdict(list)
    with Path(args.input).open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            grouped[int(row["rating"])].append(float(row["price_gbp"]))
    ratings = sorted(grouped)
    means = [sum(grouped[rating]) / len(grouped[rating]) for rating in ratings]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(7, 4.5))
    plt.bar(ratings, means, color="#4c78a8")
    plt.xlabel("Star rating")
    plt.ylabel("Average price (GBP)")
    plt.title("Average book price by rating")
    plt.xticks(ratings)
    plt.tight_layout()
    plt.savefig(output, dpi=150)
    print(f"Wrote chart to {output}")


if __name__ == "__main__":
    main()
