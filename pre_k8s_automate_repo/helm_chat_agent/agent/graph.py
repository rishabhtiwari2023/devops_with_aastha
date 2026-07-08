from langgraph.graph import StateGraph, END

from .state import HelmAgentState
from .nodes import (
    node_read_microservice,
    node_analyze_microservice,
    node_generate_helm_chart,
    node_write_helm_files,
)


def _route_after_read(state: HelmAgentState) -> str:
    if state.get("status") == "error":
        return END
    return "analyze"


def _route_after_analyze(state: HelmAgentState) -> str:
    if state.get("status") == "error":
        return END
    return "generate"


def _route_after_generate(state: HelmAgentState) -> str:
    if state.get("status") == "error":
        return END
    return "write"


def build_helm_agent():
    graph = StateGraph(HelmAgentState)

    graph.add_node("read", node_read_microservice)
    graph.add_node("analyze", node_analyze_microservice)
    graph.add_node("generate", node_generate_helm_chart)
    graph.add_node("write", node_write_helm_files)

    graph.set_entry_point("read")

    graph.add_conditional_edges("read", _route_after_read, {"analyze": "analyze", END: END})
    graph.add_conditional_edges("analyze", _route_after_analyze, {"generate": "generate", END: END})
    graph.add_conditional_edges("generate", _route_after_generate, {"write": "write", END: END})
    graph.add_edge("write", END)

    return graph.compile()
