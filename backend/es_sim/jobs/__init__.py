"""2D プラズマ計算のジョブ管理。"""

from .api import make_router
from .manager import JobError, JobManager
from .runners import default_runners

__all__ = ["JobError", "JobManager", "default_runners", "make_router"]
