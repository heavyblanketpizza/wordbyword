"""Grade-aware class source-material rewrite modules."""

from wordbyword.classroom_printout_studio.class_rewriting.rewrite import (
    DEFAULT_VOCABULARY_GUIDED_REWRITE_OUTPUT_ROOT,
    DEFAULT_WHOLE_REWRITE_OUTPUT_ROOT,
    SourceRewriteError,
    SourceVocabularyGuidedRewriteBatchOutputs,
    SourceVocabularyGuidedRewriteOutputs,
    SourceWholeRewriteBatchOutputs,
    SourceWholeRewriteOutputs,
    build_context_packet,
    rewrite_vocabulary_guided_source_material,
    rewrite_vocabulary_guided_source_materials,
    rewrite_whole_source_material,
    rewrite_whole_source_materials,
)

__all__ = [
    "DEFAULT_VOCABULARY_GUIDED_REWRITE_OUTPUT_ROOT",
    "DEFAULT_WHOLE_REWRITE_OUTPUT_ROOT",
    "SourceRewriteError",
    "SourceVocabularyGuidedRewriteBatchOutputs",
    "SourceVocabularyGuidedRewriteOutputs",
    "SourceWholeRewriteBatchOutputs",
    "SourceWholeRewriteOutputs",
    "build_context_packet",
    "rewrite_vocabulary_guided_source_material",
    "rewrite_vocabulary_guided_source_materials",
    "rewrite_whole_source_material",
    "rewrite_whole_source_materials",
]
