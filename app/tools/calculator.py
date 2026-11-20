"""纯计算工具声明；普通工具执行器在graph/workflow.py。"""
from langchain_core.tools import tool


@tool
def multiply(a:float, b:float)->float:
    """计算两个数的乘积，用于执行乘法计算。"""
    return a * b


@tool
def add(a:float, b:float)->float:
    """计算两个数的和，用于执行加法计算"""
    return a + b
