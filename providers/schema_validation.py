"""Small dependency-free JSON Schema validator for provider structured outputs."""

from __future__ import annotations

import math
from typing import Any


class SchemaValidationError(ValueError):
    """Raised when an LLM response does not conform to the requested schema."""


def validate_json_schema(value: object, schema: dict[str, Any], path: str = "$") -> None:
    """Validate the JSON Schema subset used by this project."""
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            raise SchemaValidationError(f"{path}: expected object")
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                raise SchemaValidationError(f"{path}: missing required field '{key}'")
        if schema.get("additionalProperties") is False:
            extras = sorted(set(value) - set(properties))
            if extras:
                raise SchemaValidationError(f"{path}: unexpected fields {extras}")
        for key, child in properties.items():
            if key in value:
                validate_json_schema(value[key], child, f"{path}.{key}")
    elif expected == "array":
        if not isinstance(value, list):
            raise SchemaValidationError(f"{path}: expected array")
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            raise SchemaValidationError(f"{path}: fewer than minItems")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            raise SchemaValidationError(f"{path}: more than maxItems")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                validate_json_schema(item, item_schema, f"{path}[{index}]")
    elif expected == "string":
        if not isinstance(value, str):
            raise SchemaValidationError(f"{path}: expected string")
    elif expected == "boolean":
        if not isinstance(value, bool):
            raise SchemaValidationError(f"{path}: expected boolean")
    elif expected == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise SchemaValidationError(f"{path}: expected integer")
        _validate_number_bounds(value, schema, path)
    elif expected == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SchemaValidationError(f"{path}: expected number")
        if not math.isfinite(float(value)):
            raise SchemaValidationError(f"{path}: expected finite number")
        _validate_number_bounds(float(value), schema, path)
    elif expected is not None:
        raise SchemaValidationError(f"{path}: unsupported schema type '{expected}'")

    if "enum" in schema and value not in schema["enum"]:
        raise SchemaValidationError(f"{path}: value is not in enum {schema['enum']}")


def _validate_number_bounds(value: float, schema: dict[str, Any], path: str) -> None:
    if "minimum" in schema and value < float(schema["minimum"]):
        raise SchemaValidationError(f"{path}: value is below minimum")
    if "maximum" in schema and value > float(schema["maximum"]):
        raise SchemaValidationError(f"{path}: value is above maximum")

