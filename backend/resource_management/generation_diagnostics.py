"""Bounded validation diagnostics safe for tool receipts; never echo model text."""
from pydantic import ValidationError

_CONSTRAINTS = {
    'term cannot be blank': 'blank_term',
    'entry IDs must be unique': 'duplicate_entry_id',
    'variant must reference a main entry ID in this lexicon': 'invalid_parent_reference',
    'only variants may have a parent_id': 'invalid_parent_kind',
    'duplicate term/platform/match_type': 'duplicate_term_platform_match_type',
    'no searchable terms': 'no_searchable_terms',
    'generated enabled themes require enabled variants': 'theme_without_active_variants',
    'original terms or order were changed': 'exact_terms_mismatch',
    'explicit resource count was not preserved': 'requested_count_mismatch',
    'application_stages must not contain duplicates': 'duplicate_application_stage',
}
_FIELDS = frozenset({
    'title', 'risk_label', 'description', 'themes', 'variants', 'alternatives', 'tags',
    'term', 'note', 'platform', 'match_type', 'risk_level', 'entries', 'id', 'kind',
    'parent_id', 'enabled', 'categories', 'rules', 'name', 'domain', 'audit_goal',
    'category_id', 'rule_id', 'general_exemptions', 'rule_exemptions', 'hit_condition',
    'exemption_id', 'condition', 'application_stages', 'adjudication_notes',
    'suggested_risk_level', 'order', 'schema_version', 'source_mappings', 'source_file',
    'source_locator', 'migrated_semantics',
})


class AuthoringConflict(ValueError):
    """A program-produced pair of structural paths, with no term contents."""
    def __init__(self, path, conflicts_with):
        super().__init__('duplicate term/platform/match_type')
        self.path = path
        self.conflicts_with = conflicts_with


def _safe_path(path):
    return [part if isinstance(part, int) or part in _FIELDS else '<field>'
            for part in path[:12]]


def validation_details(exc, stage):
    if isinstance(exc, AuthoringConflict):
        return {'validation_stage': stage, 'validation_errors': [{
            'path': _safe_path(exc.path), 'conflicts_with': _safe_path(exc.conflicts_with),
            'type': 'constraint_error', 'constraint': 'duplicate_term_platform_match_type',
        }], 'validation_error_count': 1}
    if isinstance(exc, ValidationError):
        errors = []
        for error in exc.errors(include_url=False, include_context=False, include_input=False)[:20]:
            # Extra-field names and validator messages can themselves contain input text.
            path = _safe_path(error['loc'])
            message = error['msg'].removeprefix('Value error, ')
            errors.append({'path': path, 'type': error['type'],
                           'constraint': _CONSTRAINTS.get(message, 'schema_validation')})
        return {'validation_stage': stage, 'validation_errors': errors,
                'validation_error_count': exc.error_count()}
    return {'validation_stage': stage, 'validation_errors': [
        {'path': [], 'type': 'constraint_error',
         'constraint': _CONSTRAINTS.get(str(exc), 'validation_error')},
    ], 'validation_error_count': 1}
