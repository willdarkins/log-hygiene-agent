import os
from typing import Dict, Any, List
import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from google.adk.agents import Agent
from google.adk.tools import FunctionTool
from google.genai import types

# 1. Load and Validate Environment Variables immediately
load_dotenv()

DATADOG_API_KEY = os.getenv("DATADOG_API_KEY")
DATADOG_APP_KEY = os.getenv("DATADOG_APP_KEY")
DATADOG_SITE = os.getenv("DATADOG_SITE", "us5.datadoghq.com")

if not DATADOG_API_KEY or not DATADOG_APP_KEY:
    raise ValueError("CRITICAL: Both DATADOG_API_KEY and DATADOG_APP_KEY must be set in the environment.")


# 2. Define a Pydantic Schema for Guaranteed Agent Output Structure
class DispositionItem(BaseModel):
    rank: int
    log_message: str
    count: int
    action: str = Field(description="Must be one of: Fix, Silence, Demote, Remove")
    reasoning: str = Field(description="One sentence explanation for the recommended action.")

class LogHygieneResponse(BaseModel):
    service: str
    disposition_table: List[DispositionItem]


# 3. Tool function with original strict signature (hardcoded window and limit)
def query_datadog_logs(service_name: str) -> Dict[str, Any]:
    """
    Queries Datadog Logs Analytics for the top 5 most frequent error/warning
    log patterns for a given service over the past 30 days.
    """
    url = f"https://api.{DATADOG_SITE}/api/v2/logs/analytics/aggregate"

    headers = {
        "DD-API-KEY": DATADOG_API_KEY,
        "DD-APPLICATION-KEY": DATADOG_APP_KEY,
        "Content-Type": "application/json",
    }

    payload = {
        "compute": [
            {
                "aggregation": "count"
            }
        ],
        "filter": {
            "query": f"service:{service_name} status:(error OR warn)",
            "from": "now-30d",
            "to": "now"
        },
    "group_by": [
    {
        "facet": "message",
        "limit": 5,
        "sort": {
                "aggregation": "count",
                "order": "desc",
                "type": "measure"
    },
        "type": "facet"
    }
]
    }

    try:
        with httpx.Client(timeout=30) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        buckets = data.get("data", {}).get("buckets", [])
        results = []
        for bucket in buckets:
            results.append({
                "message": bucket.get("by", {}).get("message", "Unknown"),
                "count": bucket.get("computes", {}).get("c0", 0)
})

        return {"service": service_name, "top_logs": results, "status": "success"}

    except httpx.HTTPStatusError as e:
        return {
            "status": "error",
            "error_message": f"Datadog API returned status code {e.response.status_code}",
            "error_detail": e.response.text,
            "top_logs": []
        }
    except Exception as e:
        return {
            "status": "error",
            "error_message": f"Unexpected error querying Datadog: {str(e)}",
            "top_logs": []
        }


datadog_tool = FunctionTool(func=query_datadog_logs)

# 4. Agent Configuration with Enforced Output Schema
agent = Agent(
    name="log_hygiene_agent",
    model="gemini-2.5-flash",
    description="Analyzes Datadog logs for a service and recommends hygiene actions.",
    instruction="""
You are an expert log hygiene analyst for an engineering team. Your job is to:

1. Query Datadog for the top 5 most frequent error/warning log patterns for the given service.
2. If the tool returns an error status, explain that you couldn't retrieve the data.
3. If successful, analyze each unique log pattern and recommend one of these actions:
   - Fix: The log represents a genuine bug or unhandled edge case.
   - Silence: The log is expected noise (e.g., known network timeout) and should be suppressed.
   - Demote: The log is useful but not an actual failure state. Change log level to INFO/WARN.
   - Remove: The log is outdated, redundant, or provides no troubleshooting value.

Be precise, data-driven, and highly concise in your reasoning.
""",
    tools=[datadog_tool],
    output_schema=LogHygieneResponse,
)