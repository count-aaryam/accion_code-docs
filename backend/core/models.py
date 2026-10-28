from dataclasses import dataclass
from enum import StrEnum


class EntityKind(StrEnum):
    FILE = "File"
    CLASS = "Class"
    FUNCTION = "Function"
    METHOD = "Method"


class RelationshipKind(StrEnum):
    CONTAINS = "CONTAINS"
    IMPORTS = "IMPORTS"
    INHERITS = "INHERITS"
    CALLS = "CALLS"


class Resolution(StrEnum):
    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"
    BUILTIN = "BUILTIN"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class Entity:
    id: str
    kind: EntityKind
    name: str
    file_path: str
    start_line: int
    end_line: int
    content_hash: str
    qualified_name: str = ""


@dataclass(frozen=True)
class Relationship:
    kind: RelationshipKind
    source_id: str
    target_id: str | None
    target_name: str
    resolution: Resolution | None = Resolution.UNRESOLVED
    line: int | None = None
    module: str | None = None
    names: tuple[str, ...] = ()


@dataclass
class ParseResult:
    entities: list[Entity]
    relationships: list[Relationship]
    skipped_files: list[str]
