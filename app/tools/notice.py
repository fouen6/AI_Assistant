"""模拟通知声明；只能由图中批准后的节点调用。"""
from langchain_core.tools import tool


@tool
def send_notice(recipient:str, content:str)->str:
    """向指定收件人模拟发送通知，仅打印内容，不发生真实消息。"""
    print(f"[模拟通知]收件人：{recipient},正文：{content}")
    return f"模拟通知完成：收件人={recipient}"
