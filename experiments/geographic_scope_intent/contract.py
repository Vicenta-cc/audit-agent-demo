"""Isolated P0 candidate contract; never imports or dispatches business services.

The production configure class is parsed as AST and evaluated with only its
Pydantic dependencies. Only its update change whitelist gains the candidate
field. Production code and the production registry are untouched.
"""
from __future__ import annotations

import ast
import copy
import hashlib
from enum import Enum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator,
    model_validator,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONFIGURE_PATH = REPOSITORY_ROOT / "backend/investigation_creation/configure.py"
CONTRACTS_PATH = REPOSITORY_ROOT / "backend/investigation_creation/contracts.py"
TOOLS_PATH = REPOSITORY_ROOT / "backend/investigation_creation/tools.py"
CANDIDATE_VERSION = "geo-intent-contract-v1"


def _class(tree: ast.Module, name: str) -> ast.ClassDef:
    return next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)


def _isolated_namespace() -> tuple[dict, ast.Module]:
    """Evaluate only reviewed class nodes, never module imports or execute()."""
    tree = ast.parse(CONTRACTS_PATH.read_text(encoding="utf-8"))
    namespace = {
        "__name__": __name__, "BaseModel": BaseModel, "ConfigDict": ConfigDict,
        "Enum": Enum, "Literal": Literal, "Field": Field, "StrictInt": StrictInt,
        "StrictStr": StrictStr, "model_validator": model_validator,
    }
    nodes = [_class(tree, "StrictModel"), _class(tree, "Platform")]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(CONTRACTS_PATH), "exec"), namespace)
    return namespace, ast.parse(CONFIGURE_PATH.read_text(encoding="utf-8"))


_namespace, _configure_tree = _isolated_namespace()
StrictModel = _namespace["StrictModel"]
Platform = _namespace["Platform"]
_production_node = _class(_configure_tree, "ConfigureDraftInput")
exec(compile(ast.Module(body=[_production_node], type_ignores=[]), str(CONFIGURE_PATH), "exec"), _namespace)
OriginalConfigureInput = _namespace["ConfigureDraftInput"]
ORIGINAL_DESCRIPTION = next(
    ast.literal_eval(node.value)
    for node in _configure_tree.body
    if isinstance(node, ast.Assign)
    and any(isinstance(target, ast.Name) and target.id == "DESCRIPTION" for target in node.targets)
)


class SetScopeChange(StrictModel):
    action: Literal["set"]
    target_text: StrictStr = Field(min_length=1, max_length=200)
    source_quote: StrictStr = Field(min_length=1, max_length=4000)
    selection_policy: Literal["local_only", "local_first_with_supplement"]

    @field_validator("target_text", "source_quote")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("target and source quote must not be blank")
        return value  # Preserve original user text, including whitespace.


class ClearScopeChange(StrictModel):
    action: Literal["clear"]
    source_quote: StrictStr = Field(min_length=1, max_length=4000)

    @field_validator("source_quote")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source quote must not be blank")
        return value


ScopeChange = Annotated[SetScopeChange | ClearScopeChange, Field(discriminator="action")]


def _candidate_base():
    node = copy.deepcopy(_production_node)
    node.name = "_CandidateConfigureBase"
    # Keep the original validators; narrowly add the candidate to changed fields
    # so a scope-only update is valid. Fail closed if upstream changes this shape.
    changes = [
        child for child in ast.walk(node)
        if isinstance(child, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "changes" for target in child.targets)
    ]
    if len(changes) != 1 or not isinstance(changes[0].value, ast.Tuple):
        raise RuntimeError("configure validator changed; candidate needs manual review")
    changes[0].value.elts.append(ast.Constant(value="relevance_scope_change"))
    ast.fix_missing_locations(node)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(CONFIGURE_PATH), "exec"), _namespace)
    return _namespace[node.name]


class CandidateConfigureInput(_candidate_base()):
    # Default None represents omitted only. Original validator rejects explicit
    # null, and the schema below excludes null from provided values.
    relevance_scope_change: ScopeChange = Field(default=None, exclude_if=lambda value: value is None)


CANDIDATE_DESCRIPTION = ORIGINAL_DESCRIPTION + (
    "地域变化用可选 relevance_scope_change；省略保留现范围。"
    "set 要求 target_text、source_quote 和 selection_policy（local_only 或 local_first_with_supplement）；"
    "clear 只需 action 与取消要求的 source_quote。不传 null、消息 ID、行政代码或 hash。"
    "仅为隔离实验候选，不代表正式产品支持地域或已执行任务。"
)


def configure_schema() -> dict:
    schema = CandidateConfigureInput.model_json_schema()
    # Pydantic emits default null for an omitted non-nullable optional field.
    # Remove that misleading default; field omission is the keep operation.
    schema["properties"]["relevance_scope_change"].pop("default", None)
    # Match the official model-facing _creation_tool_schema projection: the
    # validator enum retains wb for historical input, but creation hides it.
    def remove_legacy_platform(node):
        if isinstance(node, dict):
            enum = node.get("enum")
            if isinstance(enum, list) and "wb" in enum:
                node["enum"] = [value for value in enum if value != "wb"]
            for value in node.values():
                remove_legacy_platform(value)
        elif isinstance(node, list):
            for value in node:
                remove_legacy_platform(value)

    remove_legacy_platform(schema)
    return schema


def source_metadata() -> dict:
    return {
        "candidate_version": CANDIDATE_VERSION,
        "configure_sha256": hashlib.sha256(CONFIGURE_PATH.read_bytes()).hexdigest(),
        "contracts_sha256": hashlib.sha256(CONTRACTS_PATH.read_bytes()).hexdigest(),
        "tools_sha256": hashlib.sha256(TOOLS_PATH.read_bytes()).hexdigest(),
        "configure_path": "backend/investigation_creation/configure.py",
        "contracts_path": "backend/investigation_creation/contracts.py",
        "tools_path": "backend/investigation_creation/tools.py",
        "extraction": "AST-only ConfigureDraftInput, DESCRIPTION, StrictModel and Platform; candidate change whitelist extended; model-facing wb removal mirrors _creation_tool_schema",
    }


def validate_source(case: dict, quote: str) -> dict:
    """Bind an exact excerpt to one accessible user message; not intent scoring.

    The fixture supplies access/round metadata explicitly. Missing metadata fails
    closed. Application turns and sequences are numeric fixture counters, never
    inferred from runtime model turn IDs.
    """
    if not isinstance(quote, str) or not quote.strip():
        return {"ok": False, "code": "SOURCE_EMPTY", "message_id": None}
    current = case.get("current_user") or {}
    turn = case.get("application_turn")
    sequence = current.get("sequence")
    identity_keys = ("principal_id", "conversation_id", "task_round")
    if (any(case.get(key) is None for key in identity_keys)
            or type(turn) is not int or type(sequence) is not int
            or turn < 0 or sequence < 0
            or current.get("role") != "user"
            or current.get("application_turn") != turn
            or any(current.get(key) != case[key] for key in identity_keys)):
        return {"ok": False, "code": "SOURCE_CONTEXT_INVALID", "message_id": None}
    candidates = []
    invalid_identity = False
    messages_by_id = {}
    for message in [*case.get("history", []), current]:
        if not isinstance(message, dict):
            continue
        message_id = message.get("message_id")
        if not isinstance(message_id, str) or not message_id:
            continue
        if message_id in messages_by_id:
            if messages_by_id[message_id] != message:
                invalid_identity = True
            continue
        messages_by_id[message_id] = message
        message_turn, message_sequence = message.get("application_turn"), message.get("sequence")
        if (message.get("role") != "user"
                or any(message.get(key) != case[key] for key in identity_keys)
                or type(message_turn) is not int or type(message_sequence) is not int
                or not 0 <= message_turn <= turn or not 0 <= message_sequence <= sequence):
            continue
        content = message.get("content")
        if isinstance(content, str) and quote in content:
            candidates.append(message)
    if invalid_identity:
        return {"ok": False, "code": "SOURCE_CONTEXT_INVALID", "message_id": None}
    if not candidates:
        return {"ok": False, "code": "SOURCE_NOT_FOUND", "message_id": None}
    if len(candidates) != 1:
        return {"ok": False, "code": "SOURCE_AMBIGUOUS", "message_id": None,
                "candidate_message_ids": [message["message_id"] for message in candidates]}
    message = candidates[0]
    return {"ok": True, "code": "SOURCE_VERIFIED", "message_id": message["message_id"],
            "sequence": message["sequence"], "application_turn": turn,
            "source_application_turn": message["application_turn"], "source_quote": quote}


# Controlled synthetic fixture, not a national geographic resolver. No codes or
# province expansion are guessed. Both 朝阳 interpretations remain unresolved.
_REGIONS = (
    ("克拉玛依市", "city", "新疆", ("克拉玛依", "克拉玛依市")),
    ("大庆市", "city", "黑龙江", ("大庆", "大庆市")),
    ("新疆", "province", "新疆", ("新疆", "新疆维吾尔自治区")),
    ("成都市", "city", "四川", ("成都", "成都市")),
    ("杭州市", "city", "浙江", ("杭州", "杭州市")),
    ("苏州市", "city", "江苏", ("苏州", "苏州市")),
    ("四川", "province", "四川", ("四川", "四川省")),
    ("广东", "province", "广东", ("广东", "广东省")),
    ("宁波市", "city", "浙江", ("宁波", "宁波市")),
    ("深圳市", "city", "广东", ("深圳", "深圳市")),
)


def normalize_target(text: str) -> dict:
    result = {"target_text": text, "fixture": True, "fixture_version": "geo-regions-synthetic-v2"}
    if not isinstance(text, str) or not text.strip():
        return {**result, "ok": False, "code": "REGION_UNRESOLVED"}
    cleaned = text.strip()
    if cleaned in {"朝阳", "朝阳区"}:
        return {**result, "ok": False, "code": "REGION_AMBIGUOUS",
                "candidates": ["北京市朝阳区", "辽宁省朝阳市"] if cleaned == "朝阳" else ["北京市朝阳区", "长春市朝阳区"]}
    for name, level, province, aliases in _REGIONS:
        if cleaned in aliases:
            return {**result, "ok": True, "code": "REGION_RESOLVED", "name": name,
                    "level": level, "province": province, "aliases": list(aliases)}
    return {**result, "ok": False, "code": "REGION_UNRESOLVED"}
