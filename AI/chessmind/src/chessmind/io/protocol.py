from dataclasses import asdict
import json

from ..engine.engine import EngineResponse


def response_to_dict(response: EngineResponse) -> dict:
    return asdict(response)


def response_to_json(response: EngineResponse) -> str:
    return json.dumps(response_to_dict(response), indent=2)
