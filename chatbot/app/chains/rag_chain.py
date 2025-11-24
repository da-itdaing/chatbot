from __future__ import annotations

from typing import Optional

from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from app.config import Settings, get_settings


def build_consumer_rag_chain(settings: Optional[Settings] = None) -> Runnable:
    """
    Build a simple RAG generation chain for the consumer bot.

    This chain expects two inputs:
    - ``question``: the user's natural language question
    - ``context``: concatenated text from retrieved documents

    It returns a plain text answer (Korean) as a string.
    """

    cfg = settings or get_settings()

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                (
                    "당신은 광주 플리마켓/팝업 마켓 추천을 도와주는 어시스턴트입니다.\n"
                    "다음에 제공되는 '문서 컨텍스트'를 가능한 한 충실히 활용해 한국어로 답변하세요.\n"
                    "답변은 최대 3~5문장 정도로 정중하고 친근하게 작성하고, "
                    "모르는 내용은 지어내지 말고 솔직하게 모른다고 말하세요."
                ),
            ),
            (
                "human",
                (
                    "질문:\n{question}\n\n"
                    "문서 컨텍스트:\n{context}\n\n"
                    "위 정보를 바탕으로 한국어로 답변해 주세요."
                ),
            ),
        ]
    )

    # NOTE: `api_key` accepts plain strings at runtime; the typing here is
    # more permissive than our concrete usage, so we ignore the type checker.
    llm = ChatOpenAI(  # type: ignore[call-arg]
        model=cfg.openai_model,
        temperature=0.2,
    )

    return prompt | llm | StrOutputParser()


__all__ = ["build_consumer_rag_chain"]


