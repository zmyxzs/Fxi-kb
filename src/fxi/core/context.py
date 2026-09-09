"""Explicit application context and transaction boundary."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Generator

from fxi.storage.sqlite_client import DatabaseClient

from .config import FxiConfig


@dataclass(frozen=True)
class AppContext:
    """Shared dependencies for one configured Fxi application instance."""

    config: FxiConfig
    db: DatabaseClient

    @classmethod
    def from_config(cls, config: FxiConfig, *, initialize: bool = False) -> "AppContext":
        """Build a context without writes unless ``initialize`` is explicit."""

        if initialize:
            config.ensure_directories()
        return cls(config=config, db=DatabaseClient(config.sqlite_path, initialize=initialize))

    @classmethod
    def bootstrap(cls, config: FxiConfig) -> "AppContext":
        """Explicitly create runtime directories and initialize the schema."""

        return cls.from_config(config, initialize=True)


class UnitOfWork:
    """One transaction boundary shared by application services."""

    def __init__(self, context: AppContext):
        self.context = context

    @contextmanager
    def transaction(self) -> Generator:
        with self.context.db.transaction() as cursor:
            yield cursor


__all__ = ["AppContext", "UnitOfWork"]
