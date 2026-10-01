from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run("repopilot.api.app:create_app", factory=True, host="127.0.0.1", port=8000)
