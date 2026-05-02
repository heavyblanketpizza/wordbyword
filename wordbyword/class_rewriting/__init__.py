"""Grade-aware class source-material rewrite modules."""

from wordbyword.class_rewriting.rewrite import (
    DEFAULT_REWRITE_OUTPUT_ROOT,
    SourceRewriteBatchOutputs,
    SourceRewriteError,
    SourceRewriteOutputs,
    build_context_packet,
    rewrite_source_material,
    rewrite_source_materials,
)

__all__ = [
    "DEFAULT_REWRITE_OUTPUT_ROOT",
    "SourceRewriteBatchOutputs",
    "SourceRewriteError",
    "SourceRewriteOutputs",
    "build_context_packet",
    "rewrite_source_material",
    "rewrite_source_materials",
]

