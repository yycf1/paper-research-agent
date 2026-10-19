import type { AgentTaskState, TaskVerification } from "./api";

export const OUTCOME_LABEL: Record<TaskVerification["status"], string> = {
  passed: "目标已核验", failed: "目标未达成", partial: "部分完成", unverified: "待核验",
};

export function exportRun(task: AgentTaskState) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(task, null, 2)], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `research-task-${task.id}.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
