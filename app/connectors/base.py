from abc import ABC, abstractmethod

from app.schemas import NormalizedEvent


class SourceMapper(ABC):
    @abstractmethod
    def map_to_normalized_events(self, payload: dict) -> list[NormalizedEvent]:
        raise NotImplementedError
