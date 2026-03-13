import requests
from backend.config import parse_hardware_info


def get_all_models(endpoint: str, with_details: bool = False):
    try:
        data = requests.get(endpoint).json()
    except Exception:
        return []
    models = []
    for node_info in data.values():
        # Only include nodes that are currently connected
        # if not node_info.get('connected', False):
        #     continue
        if not node_info.get("service"):
            continue
        device_info = parse_hardware_info(node_info.get("hardware"))
        for service in node_info["service"]:
            if not service.get("identity_group"):
                continue
            model_names = [
                identity[len("model=") :]
                for identity in service["identity_group"]
                if identity.startswith("model=")
            ]
            # Add each model to the list
            if with_details:
                models.extend(
                    {
                        "id": model_name,
                        "device": device_info,
                        "object": "model",
                        "created": "0x",
                        "owner": "0x",
                    }
                    for model_name in model_names
                )
            else:
                models.extend(
                    {
                        "id": model_name,
                        "object": "model",
                        "created": "0x",
                        "owner": "0x",
                    }
                    for model_name in model_names
                )
    return models
