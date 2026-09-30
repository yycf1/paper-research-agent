"""Agent 模式：function-calling 主循环与工具执行器（M14）。

对外暴露 run_agent_task（循环）与 ToolExecutor（工具实现）。
"""

from paper_agent.agent.executor import ToolExecutor
from paper_agent.agent.loop import AgentRunResult, AgentStep, run_agent_task

__all__ = ["ToolExecutor", "run_agent_task", "AgentRunResult", "AgentStep"]
