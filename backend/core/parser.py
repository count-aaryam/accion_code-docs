from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, replace
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from tree_sitter import Language, Parser
import tree_sitter_python

from backend.core.models import Entity, EntityKind, ParseResult, Relationship, RelationshipKind, Resolution

logger = logging.getLogger(__name__)
PY_BUILTINS = {"print", "str", "int", "float", "len", "list", "dict", "set", "tuple", "bool", "bytes", "open", "range", "super", "isinstance", "Exception"}


class LanguageParser(ABC):
    @abstractmethod
    def parse_file(self, path: Path, relative_path: str) -> tuple[list[Entity], list[Relationship]]:
        raise NotImplementedError


@dataclass
class _Call:
    source_id: str
    text: str
    line: int
    scope: tuple[str, ...]


@dataclass
class _Context:
    file: Entity
    source: bytes
    entities: list[Entity]
    relationships: list[Relationship]
    symbols: dict[str, Entity]
    aliases: dict[str, str]
    calls: list[_Call]
    bases: list[tuple[int, Entity, str]]
    ids: set[str]


class PythonTreeSitterParser(LanguageParser):
    def __init__(self) -> None:
        self._parser = Parser(Language(tree_sitter_python.language()))

    def parse_file(self, path: Path, relative_path: str) -> tuple[list[Entity], list[Relationship]]:
        source = path.read_bytes()
        tree = self._parser.parse(source)
        if tree.root_node.has_error:
            raise SyntaxError("tree-sitter reported syntax errors")
        file = Entity(f"file:{relative_path}", EntityKind.FILE, relative_path, relative_path, 1, len(source.splitlines()), _hash(source), "")
        context = _Context(file, source, [file], [], {}, {}, [], [], {file.id})
        self._walk(tree.root_node, context, (), file)
        self._resolve_calls(context)
        self._resolve_bases(context)
        return context.entities, context.relationships

    def _walk(self, node: Any, context: _Context, scope: tuple[str, ...], parent: Entity) -> None:
        current_scope, current_parent = scope, parent
        if node.type in {"class_definition", "function_definition"}:
            name = _text(node.child_by_field_name("name"), context.source)
            kind = EntityKind.CLASS if node.type == "class_definition" else EntityKind.FUNCTION
            if kind == EntityKind.FUNCTION and parent.kind == EntityKind.CLASS:
                kind = EntityKind.METHOD
            qualified = ".".join((*scope, name))
            start_node = node.parent if node.parent and node.parent.type == "decorated_definition" else node
            entity_id = f"{kind.value.lower()}:{context.file.file_path}::{qualified}"
            if entity_id in context.ids:
                entity_id += f"#{node.start_point[0] + 1}"
            context.ids.add(entity_id)
            entity = Entity(entity_id, kind, name, context.file.file_path, start_node.start_point[0] + 1, node.end_point[0] + 1, _hash(node.text), qualified)
            context.entities.append(entity)
            context.symbols[qualified] = entity
            context.relationships.append(Relationship(RelationshipKind.CONTAINS, parent.id, entity.id, name, None, None))
            if kind == EntityKind.CLASS:
                supers = node.child_by_field_name("superclasses")
                for base in supers.named_children if supers else []:
                    if base.type != "keyword_argument":
                        context.bases.append((base.start_point[0] + 1, entity, _text(base, context.source)))
            current_scope, current_parent = (*scope, name), entity
        elif node.type == "import_statement":
            for child in node.named_children:
                raw = _text(child, context.source)
                parts = raw.split(" as ", 1)
                module = parts[0].strip()
                alias = parts[1].strip() if len(parts) == 2 else module.split(".")[0]
                context.aliases[alias] = module
                context.relationships.append(Relationship(RelationshipKind.IMPORTS, context.file.id, None, module, Resolution.UNRESOLVED, child.start_point[0] + 1, module))
        elif node.type == "import_from_statement":
            module_node = node.child_by_field_name("module_name") or node.child_by_field_name("module")
            module = _text(module_node, context.source)
            relative_node = node.child_by_field_name("relative_import")
            if relative_node:
                module = _text(relative_node, context.source) + module
            names = []
            for child in node.named_children:
                text = _text(child, context.source)
                if child == module_node or text.startswith("from "):
                    continue
                name = text.split(" as ", 1)[0].strip()
                if name and name != module:
                    names.append(name)
                    context.aliases[text.split(" as ", 1)[-1].strip()] = f"{module}.{name}" if module else name
            context.relationships.append(Relationship(RelationshipKind.IMPORTS, context.file.id, None, module, Resolution.UNRESOLVED, node.start_point[0] + 1, module, tuple(names)))
        elif node.type == "call":
            function = node.child_by_field_name("function")
            context.calls.append(_Call(current_parent.id, _text(function, context.source), node.start_point[0] + 1, current_scope))
        for child in node.named_children:
            self._walk(child, context, current_scope, current_parent)

    def _resolve_calls(self, context: _Context) -> None:
        for call in context.calls:
            parts, target = call.text.split("."), None
            if len(parts) == 1:
                for end in range(len(call.scope), -1, -1):
                    # A class namespace is not a lexical enclosing scope for
                    # methods. Only function/method scopes participate here.
                    prefix = call.scope[:end]
                    if prefix and isinstance(context.symbols.get(".".join(prefix)), Entity) and context.symbols[".".join(prefix)].kind == EntityKind.CLASS:
                        continue
                    target = context.symbols.get(".".join((*prefix, call.text)))
                    if target:
                        break
            elif parts[0] in {"self", "cls"} and len(parts) == 2 and call.scope:
                target = context.symbols.get(".".join((*call.scope[:-1], parts[-1])))
            else:
                target = context.symbols.get(call.text)
            if target:
                resolution, target_id = Resolution.INTERNAL, target.id
            elif parts[0] in context.aliases:
                resolution, target_id = Resolution.EXTERNAL, None
            elif len(parts) == 1 and parts[0] in PY_BUILTINS:
                resolution, target_id = Resolution.BUILTIN, None
            else:
                resolution, target_id = Resolution.UNRESOLVED, None
            context.relationships.append(Relationship(RelationshipKind.CALLS, call.source_id, target_id, call.text, resolution, call.line))

    def _resolve_bases(self, context: _Context) -> None:
        for line, source, name in context.bases:
            target = context.symbols.get(name)
            imported_path = context.aliases.get(name.split(".")[0])
            module = imported_path.rsplit(".", 1)[0] if imported_path and "." in imported_path else imported_path
            resolution = Resolution.INTERNAL if target else Resolution.EXTERNAL if module else Resolution.UNRESOLVED
            context.relationships.append(Relationship(RelationshipKind.INHERITS, source.id, target.id if target else None, name, resolution, line, module))


class RepositoryParser:
    IGNORED_DIRS = {".git", "venv", ".venv", "node_modules", "build", "dist", "__pycache__", "site-packages"}

    def __init__(self, language_parsers: dict[str, LanguageParser] | None = None) -> None:
        self.language_parsers = language_parsers or {".py": PythonTreeSitterParser()}

    def parse(self, repo_path: str | Path) -> ParseResult:
        root, result = Path(repo_path), ParseResult([], [], [])
        for directory, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in self.IGNORED_DIRS]
            for filename in filenames:
                path, parser = Path(directory) / filename, self.language_parsers.get(Path(filename).suffix)
                if parser is None:
                    continue
                relative_path = path.relative_to(root).as_posix()
                try:
                    entities, relationships = parser.parse_file(path, relative_path)
                except (OSError, UnicodeDecodeError, SyntaxError) as exc:
                    logger.warning("Skipping %s: %s", relative_path, type(exc).__name__)
                    result.skipped_files.append(relative_path)
                    continue
                result.entities.extend(entities)
                result.relationships.extend(relationships)
        self._resolve_repository(result)
        return result

    @staticmethod
    def _resolve_repository(result: ParseResult) -> None:
        files = {entity.file_path: entity for entity in result.entities if entity.kind == EntityKind.FILE}
        modules = {}
        for path, entity in files.items():
            module = path[:-3].replace("/", ".") if path.endswith(".py") else path.replace("/", ".")
            if module.endswith(".__init__"):
                module = module[:-9]
            modules[module] = entity
            if path.endswith("/__init__.py"):
                modules[path[:-12].replace("/", ".")] = entity
        top_level = {(entity.file_path, entity.name): entity for entity in result.entities if entity.kind in {EntityKind.CLASS, EntityKind.FUNCTION} and "." not in entity.qualified_name}
        imports_by_file = {}
        for relationship in result.relationships:
            if relationship.kind == RelationshipKind.IMPORTS:
                source = files.get(relationship.source_id.removeprefix("file:"))
                if source:
                    imports_by_file.setdefault(source.file_path, []).append(relationship)
        updated = []
        for relationship in result.relationships:
            if relationship.kind == RelationshipKind.IMPORTS:
                target = modules.get((relationship.module or relationship.target_name).lstrip("."))
                if target:
                    relationship = replace(relationship, target_id=target.id, resolution=Resolution.INTERNAL)
                elif relationship.module and not relationship.module.startswith("."):
                    relationship = replace(relationship, resolution=Resolution.EXTERNAL)
            elif relationship.kind in {RelationshipKind.CALLS, RelationshipKind.INHERITS} and relationship.target_id is None:
                source_entity = next((entity for entity in result.entities if entity.id == relationship.source_id), None)
                if source_entity:
                    for imported in imports_by_file.get(source_entity.file_path, []):
                        if relationship.target_name in imported.names:
                            module_entity = modules.get((imported.module or "").lstrip("."))
                            target = top_level.get((module_entity.file_path, relationship.target_name)) if module_entity else None
                            if target:
                                relationship = replace(relationship, target_id=target.id, resolution=Resolution.INTERNAL)
                                break
            updated.append(relationship)
        result.relationships = updated


def parse_repository(repo_path: str | Path) -> ParseResult:
    return RepositoryParser().parse(repo_path)


def _text(node: Any, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8") if node else ""


def _hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
