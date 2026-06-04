import uuid
import json
from fastapi import FastAPI, HTTPException
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


class HygieneRequest(BaseModel):
    service_name: str


@app.post("/analyze")
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
                result_text = event.content.parts[0].text
                break

        if not result_text:
            raise HTTPException(status_code=500, detail="Agent returned no content")

        disposition = json.loads(result_text)
        return disposition

    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Agent returned invalid JSON")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    return {"status": "ok"}