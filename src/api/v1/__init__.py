from fastapi import APIRouter

from src.api.v1.auth import router as auth_router
from src.api.v1.chat import router as chat_router

router = APIRouter(prefix="/api/v1")
router.include_router(auth_router)
router.include_router(chat_router)
