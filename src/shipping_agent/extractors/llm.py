"""LLM 抽取：LangChain Chain。

关键代码：

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", "{context}"),
    ])
    chain = prompt | llm.with_structured_output(ExtractionResult)
    result = chain.invoke({"context": context.render()})

- `ChatPromptTemplate` 负责把上下文渲染成消息；
- `with_structured_output` 让模型直接返回 Pydantic 对象；
- `|` 是 Runnable 组合，把 prompt 和模型串成一条 Chain；
- key 未配置时 chain 为 None，整个节点自动跳过。
"""

from __future__ import annotations

from ..config import Settings
from ..models import ExtractionResult
from .context import ExtractionContext

SYSTEM_PROMPT = """你是航运单据抽取助手。请从文档中抽取账单字段和费用明细。

硬规则：
1. 只抽取文档中实际存在的信息，不要猜测或补全。
2. 找不到的字段输出 null。
3. 金额必须是数字，不要带 $、千分位或币种符号。
4. 币种使用三位 ISO 4217 代码，如 USD、CNY。
5. 保留原始费用名，不要合并重复的费用行。
6. 每个费用行单独输出，不要合并。
7. document_type 使用：Debit Note、Credit Note、Invoice、Arrival Notice、Pre-Alert、Other。
8. payment_term 使用：PP（预付）、CC（到付）、null（无法判断）。
9. Credit Note 的金额输出负数，同时保留 document_type=Credit Note。
10. posting_type_candidate 从当前记账主体视角判断：我方收钱 -> AR；我方付钱 -> AP；
    双方都不是我方或无法判断 -> null。
11. 不能仅根据 Debit Note / Credit Note 判断 AR/AP，必须结合签发方、接收方、收费方向。
12. 每个关键字段尽量给出原文证据。
13. 输出 confidence（0-1），表示你对整体抽取结果的置信度；信息模糊、证据不足时降低，不要虚高。
"""


class LLMExtractor:
    def __init__(self, chain=None):
        self.chain = chain

    @property
    def available(self) -> bool:
        return self.chain is not None

    def extract(self, context: ExtractionContext | str) -> ExtractionResult:
        if self.chain is None:
            return ExtractionResult()

        rendered = (
            context.render() if isinstance(context, ExtractionContext) else context
        )
        result = self.chain.invoke({"context": rendered})
        if isinstance(result, ExtractionResult):
            return result
        if isinstance(result, dict):
            return ExtractionResult.model_validate(result)
        return ExtractionResult()


def build_llm_extractor(settings: Settings) -> LLMExtractor:
    if not settings.is_llm_configured:
        return LLMExtractor(None)

    from langchain.chat_models import init_chat_model
    from langchain_core.prompts import ChatPromptTemplate

    llm = init_chat_model(
        model=settings.llm_model,
        model_provider=settings.llm_provider,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url or None,
        temperature=settings.llm_temperature,
    )

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT),
            ("human", "{context}"),
        ]
    )
    # 关键 Chain：prompt -> structured LLM
    chain = prompt | llm.with_structured_output(ExtractionResult)
    return LLMExtractor(chain)
