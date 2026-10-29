"""
TempoApply -- Main entrypoint.
Starts the FastAPI backend server.
"""
import uvicorn
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asyncio
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from backend.db.models import init_db
from backend.config import settings

if __name__ == "__main__":
    print("=" * 50)
    print("  TempoApply — Entry-Level Job Search Engine (<2 Yrs Exp)")
    print("=" * 50)
    print(f"  API Server : http://{settings.api_host}:{settings.api_port}")
    print(f"  Target Roles: {', '.join(settings.target_roles_list)}")
    print(f"  Max Exp     : {settings.experience_years} years (Hard limit)")
    print(f"  Locations   : {', '.join(settings.preferred_locations_list)}")
    print("=" * 50)
    print("  Dashboard  : http://localhost:3000 (cd dashboard && npm run dev)")
    print("=" * 50)

    init_db()
    uvicorn.run(
        "backend.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
        log_level="info",
    )
