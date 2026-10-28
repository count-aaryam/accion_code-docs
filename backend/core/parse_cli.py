import argparse
import logging

from backend.core.parser import parse_repository
from backend.core.storage import save_parse_result


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse a repository with tree-sitter")
    parser.add_argument("repo", help="Path to the repository to parse")
    parser.add_argument("-o", "--output", default="parsed-output.json", help="JSON output path")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = parse_repository(args.repo)
    output = save_parse_result(result, args.output)
    print(f"Saved {len(result.entities)} entities and {len(result.relationships)} relationships to {output}")


if __name__ == "__main__":
    main()
