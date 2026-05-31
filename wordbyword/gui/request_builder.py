from __future__ import annotations

from collections.abc import Mapping

from wordbyword.application.commands import (
    AfterClassReportRequest,
    ArchiveDuplicatesRequest,
    ArchiveRenameRequest,
    ClassPrintoutsRequest,
    CommandId,
    CommandRequest,
    DictionaryBuildRequest,
    LiveLexiconRequest,
    UsageSignalsRequest,
)
from wordbyword.application.workspace import (
    bool_value,
    int_value,
    llm_runtime_config_from_values,
    optional_int_value,
    optional_path_value,
    optional_text_value,
    path_value,
    reject_other_grade_paths,
    rewrite_vocabulary_source,
    target_grade_from_value,
)


GUI_WORKFLOW_IDS: dict[str, CommandId] = {
    "dictionary_build": CommandId.DICTIONARY_BUILD,
    "dictionary_prep": CommandId.DICTIONARY_PREP,
    "class_printouts": CommandId.CLASS_PRINTOUTS,
    "archive_rename_preview": CommandId.ARCHIVE_RENAME_PREVIEW,
    "archive_rename_apply": CommandId.ARCHIVE_RENAME_APPLY,
    "archive_duplicates_preview": CommandId.ARCHIVE_DUPLICATES_PREVIEW,
    "archive_duplicates_apply": CommandId.ARCHIVE_DUPLICATES_APPLY,
    "usage_signals": CommandId.USAGE_SIGNALS,
    "live_lexicon": CommandId.LIVE_LEXICON,
    "after_class_report": CommandId.AFTER_CLASS_REPORT,
}


def gui_workflow_command_ids() -> Mapping[str, CommandId]:
    return dict(GUI_WORKFLOW_IDS)


def build_gui_request(workflow: str, values: Mapping[str, object]) -> CommandRequest:
    try:
        command_id = GUI_WORKFLOW_IDS[workflow]
    except KeyError as error:
        raise ValueError(f"Unknown GUI workflow: {workflow}") from error
    llm = llm_runtime_config_from_values(values)
    if command_id in {CommandId.DICTIONARY_BUILD, CommandId.DICTIONARY_PREP}:
        target_grade = target_grade_from_value(optional_text_value(values, "target_grade"))
        reject_other_grade_paths(values, target_grade, "dictionary_input_dir", "dictionary_output_dir")
        return DictionaryBuildRequest(
            command_id=command_id,
            target_checkpoint="15" if command_id == CommandId.DICTIONARY_BUILD else "09",
            target_grade=target_grade,
            input_dir=path_value(values, "dictionary_input_dir"),
            output_dir=path_value(values, "dictionary_output_dir"),
            reference_dictionary_path=path_value(values, "reference_dictionary_path"),
            include_punctuation=bool_value(values, "include_punctuation"),
            include_numbers=bool_value(values, "include_numbers"),
            model=llm.model,
            base_url=llm.base_url,
            force=bool_value(values, "dictionary_force"),
            provider=llm.provider,
        )
    if command_id == CommandId.CLASS_PRINTOUTS:
        target_grade = target_grade_from_value(optional_text_value(values, "target_grade"))
        reject_other_grade_paths(
            values,
            target_grade,
            "class_source_dir",
            "source_output_root",
            "rewrite_output_root",
            "print_output_root",
            "live_lexicon_path",
            "dictionary_output_dir",
        )
        dictionary_output_dir = path_value(values, "dictionary_output_dir")
        explicit = optional_path_value(values, "rewrite_dictionary_path")
        return ClassPrintoutsRequest(
            command_id=command_id,
            target_grade=target_grade,
            source_dir=path_value(values, "class_source_dir"),
            parse_output_root=path_value(values, "source_output_root"),
            rewrite_output_root=path_value(values, "rewrite_output_root"),
            print_output_root=path_value(values, "print_output_root"),
            dictionary_output_dir=dictionary_output_dir,
            live_lexicon_path=path_value(values, "live_lexicon_path"),
            rewrite_dictionary_path=explicit,
            vocabulary_source=rewrite_vocabulary_source(
                explicit_path=explicit,
                target_grade=target_grade,
                dictionary_output_dir=dictionary_output_dir,
            ),
            parse_pdf=not bool_value(values, "skip_pdf_parse"),
            render_dpi=int_value(values, "pdf_render_dpi", 144),
            paddleocr_vl_settings=None,
            model=llm.model,
            base_url=llm.base_url,
            font_path=optional_path_value(values, "print_font_path"),
            body_font_size=optional_int_value(values, "print_body_font_size"),
            force_class_printouts=bool_value(values, "force_class_printouts"),
            provider=llm.provider,
        )
    if command_id in {CommandId.ARCHIVE_RENAME_PREVIEW, CommandId.ARCHIVE_RENAME_APPLY}:
        target_grade = target_grade_from_value(optional_text_value(values, "target_grade"))
        reject_other_grade_paths(values, target_grade, "archive_root")
        return ArchiveRenameRequest(
            command_id=command_id,
            target_grade=target_grade,
            archive_root=path_value(values, "archive_root"),
            apply=command_id == CommandId.ARCHIVE_RENAME_APPLY,
            model=llm.model,
            base_url=llm.base_url,
            provider=llm.provider,
        )
    if command_id in {CommandId.ARCHIVE_DUPLICATES_PREVIEW, CommandId.ARCHIVE_DUPLICATES_APPLY}:
        target_grade = target_grade_from_value(optional_text_value(values, "target_grade"))
        reject_other_grade_paths(values, target_grade, "archive_root")
        return ArchiveDuplicatesRequest(
            command_id=command_id,
            target_grade=target_grade,
            archive_root=path_value(values, "archive_root"),
            apply=command_id == CommandId.ARCHIVE_DUPLICATES_APPLY,
            include_near_duplicates=bool_value(values, "include_near_duplicates"),
            model=llm.model,
            base_url=llm.base_url,
            provider=llm.provider,
        )
    if command_id == CommandId.USAGE_SIGNALS:
        target_grade = target_grade_from_value(optional_text_value(values, "target_grade"))
        reject_other_grade_paths(values, target_grade, "archive_root", "usage_signal_output_root")
        return UsageSignalsRequest(
            command_id=command_id,
            target_grade=target_grade,
            archive_root=path_value(values, "archive_root"),
            output_root=path_value(values, "usage_signal_output_root"),
            include_punctuation=bool_value(values, "include_punctuation"),
            include_numbers=bool_value(values, "include_numbers"),
            model=llm.model,
            base_url=llm.base_url,
            provider=llm.provider,
        )
    if command_id == CommandId.LIVE_LEXICON:
        target_grade = target_grade_from_value(optional_text_value(values, "target_grade"))
        reject_other_grade_paths(
            values,
            target_grade,
            "canonical_dictionary_path",
            "usage_candidates_path",
            "live_lexicon_path",
            "supplemental_lexicon_path",
            "unmatched_usage_output_path",
        )
        return LiveLexiconRequest(
            command_id=command_id,
            target_grade=target_grade,
            canonical_dictionary_path=path_value(values, "canonical_dictionary_path"),
            usage_candidates_path=path_value(values, "usage_candidates_path"),
            usage_summary_path=optional_path_value(values, "usage_summary_path"),
            live_lexicon_path=path_value(values, "live_lexicon_path"),
            supplemental_lexicon_path=path_value(values, "supplemental_lexicon_path"),
            unmatched_usage_output_path=path_value(values, "unmatched_usage_output_path"),
        )
    if command_id == CommandId.AFTER_CLASS_REPORT:
        target_grade = target_grade_from_value(optional_text_value(values, "target_grade"))
        reject_other_grade_paths(values, target_grade, "after_class_report_output_root")
        return AfterClassReportRequest(
            command_id=command_id,
            target_grade=target_grade,
            printout_path=path_value(values, "after_class_printout_path"),
            output_root=path_value(values, "after_class_report_output_root"),
            model=llm.model,
            base_url=llm.base_url,
            provider=llm.provider,
            font_path=optional_path_value(values, "after_class_report_font_path"),
        )
    raise ValueError(f"Unsupported GUI command: {command_id}")
