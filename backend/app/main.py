from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.api.routes import admin_router, events_router, notifications_router, subscribers_router
from app.core.config import settings

app = FastAPI(
    title=settings.APP_NAME,
    description="Campus Notification Engine - Event ingestion, preferences, and atomic digest delivery.",
    version="0.1.0",
    debug=settings.DEBUG,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


from fastapi.encoders import jsonable_encoder


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Format request validation errors as 400 Bad Request to strictly match API.md.
    """
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "error": "Bad Request",
            "detail": jsonable_encoder(exc.errors()),
            "message": "Invalid request payload format or missing required fields.",
        },
    )


# Register API routes under /v1
for _router in (events_router, subscribers_router, notifications_router, admin_router):
    app.include_router(_router, prefix=settings.API_V1_PREFIX)


@app.get("/health", tags=["System"])
async def health_check():
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "environment": settings.APP_ENV,
        "version": "0.1.0",
    }


@app.get("/", tags=["System"])
async def root():
    return {
        "message": f"Welcome to {settings.APP_NAME} API",
        "docs_url": "/docs",
        "health_url": "/health",
    }
