"""LunaSRE web layer — AG-UI-style SSE server + human approval endpoint (Phase 3).

The browser opens an EventSource to /incidents/{alert_id}/stream and watches the
investigation unfold node-by-node (investigate -> delegate -> rca -> summarize),
then the graph PAUSES at the human-approval gate and the stream ends with an
`awaiting_approval` event. The human clicks Approve/Reject, which POSTs to
/incidents/{thread_id}/approve; the IC graph resumes from its checkpoint into
execute_remediation.

This is the AG-UI *seam* (server streams agent events; UI renders; human gates a
side-effecting step; backend resumes). The production frontend is Next.js 15 +
CopilotKit consuming the same event shapes; this single-file vanilla UI proves
the seam without a build chain.

Run:
    uv run uvicorn lunasre.web:app --port 8080
    # open http://localhost:8080
"""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from lunasre.agents.base import PROJECT_ROOT, load_agent_config
from lunasre.agents.ic import resume_hitl, stream_hitl
from lunasre.registries import load_mcp_registry

app = FastAPI(title="LunaSRE", version="0.1.0")

_FRONTEND = PROJECT_ROOT / "frontend" / "index.html"


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(_FRONTEND)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "service": "lunasre-web"}


@app.get("/alerts")
async def alerts() -> dict:
    """List the mock alerts (for the UI dropdown)."""
    path = PROJECT_ROOT / "mock_data" / "alerts.json"
    return {"alerts": json.loads(path.read_text())}


def _sse(event: dict) -> str:
    """Format one {event, data} dict as a Server-Sent Event frame."""
    return f"event: {event['event']}\ndata: {json.dumps(event['data'])}\n\n"


@app.get("/incidents/{alert_id}/stream")
async def stream(alert_id: str) -> StreamingResponse:
    """SSE: stream the investigation node-by-node, ending at the approval gate."""

    async def gen():
        try:
            async for ev in stream_hitl(alert_id):
                yield _sse(ev)
        except Exception as e:  # surface errors to the browser instead of a silent hang
            yield _sse({"event": "error", "data": {"message": repr(e)}})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class ApprovalRequest(BaseModel):
    approved: bool
    alert_id: str


@app.post("/incidents/{thread_id}/approve")
async def approve(thread_id: str, req: ApprovalRequest) -> dict:
    """Resume a paused incident from its checkpoint with the human's decision."""
    final = await resume_hitl(req.alert_id, thread_id, req.approved)
    return {
        "thread_id": thread_id,
        "approved": req.approved,
        "executed": final.get("executed", False),
        "last_message": (final.get("messages") or [{}])[-1].get("content", ""),
    }


# Sanity: surface config/registry load errors at import time in dev.
def _selfcheck() -> None:
    load_agent_config("ic-agent")
    load_mcp_registry(PROJECT_ROOT / "infra" / "registries" / "mcp_registry.yaml")


_selfcheck()
