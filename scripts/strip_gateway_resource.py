#!/usr/bin/env python3
"""
构建期辅助脚本：在没有内嵌网关时，从 project.yml 里移除网关资源条目。

为什么要单独放成文件：在 workflow 里内联 heredoc 容易破坏 YAML 缩进，
这类问题在 CI 上排查成本很高，拆成脚本可测试、可复用。
"""

import sys

TARGET = """      - path: WildWorkNative/Resources/gateway
        type: file
        buildPhase: resources
"""


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "project.yml"
    with open(path, encoding="utf-8") as f:
        content = f.read()

    if TARGET not in content:
        print("未找到网关资源条目，无需移除")
        return 0

    content = content.replace(TARGET, "")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print("已移除网关资源条目（本次为客户端版）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
