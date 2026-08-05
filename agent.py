import base64
import json
import os
import shutil
from typing import Any, Dict

from dotenv import load_dotenv
from mcp import StdioServerParameters

from google.adk.agents import LlmAgent
from google.adk.integrations.agent_registry import AgentRegistry
from google.adk.tools import McpToolset
from google.adk.tools.mcp_tool.mcp_toolset import StdioConnectionParams
from google.adk.workflow import Workflow, START, node

os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE"


# ------------------------------------------------------------------------------
# Step 1: Parse Pub/Sub Event (Pass node_input as-is)
# ------------------------------------------------------------------------------
@node(name="parse_event")
def parse_event(ctx: Any, node_input: Any = None) -> Any:
    """Print node_input and pass node_input as-is to downstream nodes."""
    print(f"📥 [parse_event] node_input: {node_input}")

    if hasattr(node_input, "parts") and node_input.parts:
        content_str = "".join(p.text for p in node_input.parts if getattr(p, "text", None))
        try:
            val = json.loads(content_str)
        except Exception:
            val = content_str
    else:
        val = node_input

    ctx.state["parsed_event"] = val
    return val


def extract_text_from_any(obj: Any) -> str:
    """Safely extract text string from Content, Event, dict, or raw text objects."""
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if hasattr(obj, "text") and getattr(obj, "text", None):
        t = getattr(obj, "text")
        if isinstance(t, str) and t.strip():
            return t
    if hasattr(obj, "parts") and getattr(obj, "parts", None):
        parts_text = "".join(getattr(p, "text", "") or "" for p in obj.parts if getattr(p, "text", None))
        if parts_text.strip():
            return parts_text
    if hasattr(obj, "content") and getattr(obj, "content", None):
        res = extract_text_from_any(getattr(obj, "content"))
        if res.strip():
            return res
    if isinstance(obj, dict):
        if "text" in obj:
            return str(obj["text"])
        if "content" in obj:
            return extract_text_from_any(obj["content"])
    return str(obj)


# ------------------------------------------------------------------------------
# Callback executed after generate_case_summary_agent completes
# ------------------------------------------------------------------------------
def log_summary_callback(callback_context: Any) -> None:
    """Callback function executed immediately after generate_case_summary_agent completes."""
    raw_summary = getattr(callback_context, "output", None)
    if not raw_summary:
        state = getattr(callback_context, "state", {}) if hasattr(callback_context, "state") else {}
        raw_summary = state.get("generate_case_summary_agent")

    summary_message = extract_text_from_any(raw_summary)
    print(f"🤖 [generate_case_summary_agent]: {summary_message}")


# ------------------------------------------------------------------------------
# Step 2: LLM Analysis Agent
# ------------------------------------------------------------------------------
generate_case_summary_agent = LlmAgent(
    model="gemini-3.5-flash",
    name="generate_case_summary_agent",
    output_key="generate_case_summary_agent",
    after_agent_callback=log_summary_callback,
    instruction="""
        You are an SRE Incident Analysis AI Agent.

        Goal:
        Inspect the alerting event passed from the previous node (`parse_event` / `node_input` / `parsed_event`), extract the specific key fields below, and produce a well-structured Incident Summary Message:

        Required Fields to Extract from the input:
        - `endpoint_id`: Target Vertex AI Endpoint ID (from resource.labels.endpoint_id)
        - `location`: Endpoint location (from resource.labels.location)
        - `project_id`: GCP Project ID (from resource.labels.project_id or scoping_project_id)
        - `started_at`: Incident start timestamp
        - `ended_at`: Incident end timestamp (if resolved/closed, or None/Active)
        - `state`: Incident status (`open` or `closed`)
        - `condition_name`: Name of the triggering condition
        - `summary`: Alert summary text

        Expected Summary Output Format:
        ### 🚨 Vertex AI Endpoint Incident Analysis Report
        - 📌 **Project ID**: <project_id>
        - 🎯 **Endpoint ID**: <endpoint_id>
        - 🌍 **Location**: <location>
        - 📊 **Status**: <state>
        - 🔔 **Condition Name**: <condition_name>
        - ⏰ **Started At**: <started_at>
        - 🏁 **Ended At**: <ended_at or Active>
        - 📝 **Summary**: <summary>
        - 🛠️ **Recommended Actions**: <actionable SRE steps>
    """,
)


# ------------------------------------------------------------------------------
# Step 3: Support Case System Registration (Demo)
# ------------------------------------------------------------------------------
@node(name="create_support_case")
def create_support_case(ctx: Any) -> str:
    """Register an automated support ticket in the support case management system (Stub)."""
    parsed_event = ctx.state.get("parsed_event", {})
    raw_summary = ctx.state.get("generate_case_summary_agent")
    summary_message = extract_text_from_any(raw_summary)

    output_text = f"🎫 [create_support_case]: {summary_message}\n"
    print(output_text)
    return output_text


# ------------------------------------------------------------------------------
# Step 4: Send Slack User Notification (Demo)
# ------------------------------------------------------------------------------
@node(name="send_slack_notification")
def send_slack_notification(ctx: Any) -> str:
    """Send alert notification message to specified Slack channel (Stub)."""
    parsed_event = ctx.state.get("parsed_event", {})
    raw_summary = ctx.state.get("generate_case_summary_agent")
    summary_message = extract_text_from_any(raw_summary)

    output_text = f"💬 [send_slack_notification]: {summary_message}\n"
    print(output_text)
    return output_text


# ------------------------------------------------------------------------------
# Graph Workflow Definition
# ------------------------------------------------------------------------------
root_agent = Workflow(
    name="ambient_agent",
    description="Graph Workflow for processing Pub/Sub alerts: [Parse -> Analyze Metrics -> Register Case -> Send Slack Notification]",
    edges=[
        (START, parse_event, generate_case_summary_agent, create_support_case, send_slack_notification)
    ],
)