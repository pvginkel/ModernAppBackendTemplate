import threading
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from app.services.container import ServiceContainer


class ProgressHandle(Protocol):
    """Interface for sending progress updates to connected clients."""

    def send_progress_text(self, text: str) -> None: ...
    def send_progress_value(self, value: float) -> None: ...
    def send_progress(self, text: str, value: float) -> None: ...


class SubProgressHandle(ProgressHandle):
    """Progress handle that represents a sub-task of a parent task."""

    def __init__(self, parent: ProgressHandle, start: float, end: float) -> None:
        self.parent = parent
        self.start = start
        self.end = end

    def send_progress_text(self, text: str) -> None:
        self.parent.send_progress_text(text)

    def send_progress_value(self, value: float) -> None:
        self.parent.send_progress_value(self._scale_progress_value(value))

    def send_progress(self, text: str, value: float) -> None:
        self.parent.send_progress(text, self._scale_progress_value(value))

    def _scale_progress_value(self, value: float) -> float:
        return self.start + (self.end - self.start) * value


class BaseTask(ABC):
    """Abstract base class for background tasks with progress reporting."""

    def __init__(self) -> None:
        self._cancelled = threading.Event()

    @abstractmethod
    def execute(self, progress_handle: ProgressHandle, **kwargs: Any) -> BaseModel:
        pass

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()


class BaseSessionTask(BaseTask):
    """Abstract base class for tasks that require a database session."""

    def __init__(self, container: 'ServiceContainer'):
        super().__init__()
        self.container = container

    def execute(self, progress_handle: ProgressHandle, **kwargs: Any) -> BaseModel:
        session = self.container.db_session()
        try:
            result = self.execute_session(session, progress_handle, **kwargs)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            self.container.db_session.reset()
        return result

    @abstractmethod
    def execute_session(self, session: Session, progress_handle: ProgressHandle, **kwargs: Any) -> BaseModel:
        pass
