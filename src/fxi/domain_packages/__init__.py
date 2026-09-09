"""Optional domain packages for the Fxi v3 kernel."""

from .novel import NovelDomainPackage, register_novel

__all__ = ["NovelDomainPackage", "register_novel"]
