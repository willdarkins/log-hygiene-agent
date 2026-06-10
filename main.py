import uuid
import json
import os
from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from dotenv import load_dotenv
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agent import agent

load_dotenv()

app = FastAPI()

session_service = InMemorySessionService()

APP_NAME = "log_hygiene_agent"
USER_ID = "n8n"

security = HTTPBearer()

VALID_ACTIONS = {"Fix", "Silence", "Demote", "Remove"}


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    expected = os.environ.get("AGENT_API_KEY")
    if not expected:
        raise HTTPException(status_code=500, detail="AGENT_API_KEY not configured")
    if credentials.credentials != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


class HygieneRequest(BaseModel):
    service_name: str


def extract_json(text: str):
    """Parse agent output, tolerating markdown fences and surrounding prose."""
    if not text or not text.strip():
        return None
    cleaned = text.strip()
    # Strip markdown code fences if present
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Last resort: grab the outermost JSON object if prose surrounds it
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError:
                return None
    return None


def fallback(service: str, note: str):
    return {"service": service, "disposition_table": [], "note": note}


@app.post("/analyze", dependencies=[Depends(verify_token)])
async def analyze_logs(request: HygieneRequest):
    try:
        session_id = f"{request.service_name}-{uuid.uuid4().hex}"

        await session_service.create_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=session_id,
        )

        runner = Runner(
            agent=agent,
            app_name=APP_NAME,
            session_service=session_service,
        )

        message = types.Content(
            role="user",
            parts=[types.Part(text=f"Analyze logs for service: {request.service_name}")]
        )

        result_text = ""
        async for event in runner.run_async(
            user_id=USER_ID,
            session_id=session_id,
            new_message=message,
        ):
            print(f"EVENT: author={event.author} is_final={event.is_final_response()} content={event.content}")
            if event.is_final_response() and event.content and event.content.parts:
                result_text = event.content.parts[0].text or ""
                break

        if not result_text.strip():
            return fallback(
                request.service_name,
                "Agent returned no content — service may have no matching logs in the query window."
            )

        disposition = extract_json(result_text)
        if disposition is None:
            return fallback(
                request.service_name,
                "Agent returned output that could not be parsed as JSON."
            )

        # Normalize shape and validate actions
        table = disposition.get("disposition_table", [])
        for row in table:
            if row.get("action") not in VALID_ACTIONS:
                row["reasoning"] = (
                    f"[Action auto-corrected to Fix — agent suggested "
                    f"'{row.get('action')}'] {row.get('reasoning', '')}"
                )
                row["action"] = "Fix"
        disposition["disposition_table"] = table
        disposition.setdefault("service", request.service_name)

        return disposition

    except Exception as e:
        return fallback(request.service_name, f"Agent error: {str(e)}")


@app.get("/health")
async def health():
    return {"status": "ok"}