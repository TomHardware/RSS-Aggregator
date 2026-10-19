"""版本号的唯一来源。

其他地方（README、CHANGELOG、/healthz、页面页脚）都以这里为准，
避免出现「git 标签是 v2.0.2、文档写着 1.1.0」这类不一致。
"""

__version__ = "2.1.0"
__version_info__ = tuple(int(part) for part in __version__.split("."))

GITHUB_URL = "https://github.com/forestwolf-ai/RSS-Aggregator"
