from dataclasses import dataclass
from enum import StrEnum


class EntityKind(StrEnum):
    FILE = "File"
    CLASS = "Class"
    FUNCTION = "Function"
    METHOD = "Method"
    INTERFACE = "Interface"
    TYPE_ALIAS = "TypeAlias"
    ENUM = "Enum"
    EXTERNAL = "External"
    UNRESOLVED = "Unresolved"


class RelationshipKind(StrEnum):
    CONTAINS = "CONTAINS"
    IMPORTS = "IMPORTS"
    INHERITS = "INHERITS"
    CALLS = "CALLS"
    IMPLEMENTS = "IMPLEMENTS"
    RENDERS = "RENDERS"
    EXPORTS = "EXPORTS"


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
    language: str = "python"
    exported: bool = False
    export_name: str = ""
    is_default_export: bool = False
    properties: dict = None

    def __post_init__(self):
        if self.properties is None:
            object.__setattr__(self, "properties", {})


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
    type_only: bool = False
    resolved: bool | None = None


@dataclass
class ParseResult:
    entities: list[Entity]
    relationships: list[Relationship]
    skipped_files: list[str]
    skip_reasons: dict[str, str] = None

    def __post_init__(self):
        if self.skip_reasons is None:
            self.skip_reasons = {}
