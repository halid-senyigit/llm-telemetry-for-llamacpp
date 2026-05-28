from __future__ import annotations

import os

import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host="127.0.0.1",
        port=int(os.getenv("BACKEND_PORT", "7171")),
        reload=True,
    )
