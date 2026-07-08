from typing import TypedDict, Optional, List, Dict, Any


class HelmAgentState(TypedDict):
    microservice_path: str
    service_name: str
    env_content: str
    readme_content: str
    dockerfile_content: str
    dependency_file_content: str
    directory_tree: str
    service_metadata: Dict[str, Any]
    helm_files: Dict[str, str]
    output_path: str
    errors: List[str]
    logs: List[str]
    status: str
