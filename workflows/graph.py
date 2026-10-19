"""小红书内容生成与人工审核的 LangGraph 编排。"""
from langgraph.graph import END, START, StateGraph

from . import nodes
from .state import ContentWorkflowState, WorkflowContext


def build_graph(checkpointer):
    graph = StateGraph(ContentWorkflowState, context_schema=WorkflowContext)
    for name in ("load_analyses", "generate_content", "generate_images", "platform_rules", "human_review", "ready_to_publish"):
        graph.add_node(name, getattr(nodes, name))
    graph.add_edge(START, "load_analyses")
    graph.add_edge("load_analyses", "generate_content")
    graph.add_conditional_edges("generate_content", nodes.generation_route,
        {name: name for name in ("generate_images", "platform_rules")})
    graph.add_edge("generate_images", "platform_rules")
    graph.add_edge("platform_rules", "human_review")
    graph.add_conditional_edges("human_review", nodes.review_route,
        {name: name for name in ("ready_to_publish", "human_review", "platform_rules", "load_analyses")})
    graph.add_edge("ready_to_publish", END)
    return graph.compile(checkpointer=checkpointer)
