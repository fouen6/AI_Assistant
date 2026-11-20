"""提供给Agent的知识库工具；检索实现位于services/retrieval.py。"""
from langchain_core.tools import tool
from app.services.retrieval import retrieve_text

def make_api_handbook_tool():
    @tool
    def search_handbook(question: str) -> str:
        """检索当前已入库手册和制度，输入完整独立问题。返回候选原文及来源，候选可能没有答案。"""
        return retrieve_text(question)
    return search_handbook
