#!/usr/bin/env python3
# -_- coding: utf-8 -_-

# 版本号, 发版前手动修改，CI 构建自动写入
VERSION = "v1.16.5"

def print_info():
    """打印程序的版本和版权信息。"""
    print("\n")
    print("+ " + " M9A Update Assistant ".center(60, "="), "+")
    print("||" + "".center(60, " ") + "||")
    print("||" + "M9A CLI 更新小助手".center(55, " ") + "||")
    print("||" + "本项目使用 AI 进行生成".center(51, " ") + "||")
    print("||" + "".center(60, " ") + "||")
    print("|| " + "".center(58, "-") + " ||")
    print("||" + "".center(60, " ") + "||")
    print("||" + f"Version: {VERSION}    License: WTFPL".center(60, " ") + "||")
    print("||" + "".center(60, " ") + "||")
    print("+ " + "".center(60, "=") + " +")
    print("\n")