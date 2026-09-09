from fastapi import APIRouter, Depends

from app.api.v1.routes import (
    agent,
    alerts,
    auth,
    broadcast_library,
    broadcasts,
    content,
    devices,
    health,
    images,
    notifications,
    publish,
    supply,
    tasks,
)
from app.core.admin_auth import require_admin

router = APIRouter(prefix="/api/v1")
# Console (management) routers require the admin token. The agent router uses
# per-device token auth; the devices router mixes agent-called routes
# (register/heartbeat) with management ones, so those two guard per-route.
_admin = [Depends(require_admin)]
router.include_router(health.router)
router.include_router(auth.router)  # self-guards per-route (me / token mgmt)
router.include_router(tasks.router, dependencies=_admin)
router.include_router(devices.router)
router.include_router(content.router, dependencies=_admin)
router.include_router(images.router, dependencies=_admin)
router.include_router(broadcasts.router, dependencies=_admin)
router.include_router(broadcast_library.router, dependencies=_admin)
router.include_router(notifications.router, dependencies=_admin)
router.include_router(alerts.router, dependencies=_admin)
router.include_router(supply.router, dependencies=_admin)
router.include_router(publish.router, dependencies=_admin)
router.include_router(agent.router)

