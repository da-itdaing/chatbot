from __future__ import annotations

from fastapi import FastAPI
from dotenv import load_dotenv
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer

from app.config import ROOT_DIR, get_settings
from app.graphs.consumer import build_consumer_graph, build_consumer_graph_async
from app.graphs.seller import build_seller_graph, build_seller_graph_async
from app.routers import chat_consumer, chat_seller, admin, embed


def create_app() -> FastAPI:
    # Ensure environment variables from chatbot.env are visible to OpenAI clients.
    load_dotenv(ROOT_DIR / "chatbot.env")

    app = FastAPI(
        title="Itdaing Chatbot API",
        version="1.0.0",
    )

    settings = get_settings()
    app.state.settings = settings

    @app.on_event("startup")
    async def _startup() -> None:
        # Initialise LangGraph AsyncPostgresSaver once and keep it for the app lifetime.
        serde = EncryptedSerializer.from_pycryptodome_aes()
        checkpointer_cm = AsyncPostgresSaver.from_conn_string(
            settings.checkpoint_conn_string,
            serde=serde,
        )
        checkpointer = await checkpointer_cm.__aenter__()  # type: ignore[union-attr]
        await checkpointer.setup()

        app.state.checkpointer_cm = checkpointer_cm
        app.state.checkpointer = checkpointer
        app.state.consumer_graph = build_consumer_graph(checkpointer=checkpointer)
        app.state.consumer_graph_async = build_consumer_graph_async(checkpointer=checkpointer)
        app.state.seller_graph = build_seller_graph(checkpointer=checkpointer)
        app.state.seller_graph_async = build_seller_graph_async(checkpointer=checkpointer)

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        cm = getattr(app.state, "checkpointer_cm", None)
        if cm is not None:
            await cm.__aexit__(None, None, None)  # type: ignore[union-attr]

    @app.get("/health", include_in_schema=False)
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/key-status", include_in_schema=False)
    async def key_status() -> dict:
        """API 키 로테이션 상태 확인 (관리자용)"""
        from app.utils.key_rotation import get_key_manager
        key_manager = get_key_manager()
        return key_manager.get_status_summary()

    app.include_router(chat_consumer.router)
    app.include_router(chat_seller.router)
    app.include_router(admin.router)
    app.include_router(embed.router)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)


